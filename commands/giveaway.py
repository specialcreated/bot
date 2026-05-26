import discord
import asyncio
import logging
import random
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Set
from collections import OrderedDict
from discord.ext import commands, tasks
from discord.ui import Button, View, Select, Modal, TextInput

# Логирование
logger = logging.getLogger(__name__)

# Глобальные словики для debounce и кэша активных розыгрышей
_giveaway_debounce: Dict[int, Set[int]] = {}  # giveaway_id -> set of user_id (для защиты от спама кликов)
_active_giveaways: OrderedDict[int, dict] = OrderedDict()  # giveaway_id -> данные розыгрыша (OrderedDict для LRU)
_MAX_CACHE_SIZE = 1000  # Максимальный размер кэша

# Блокировки для предотвращения гонок данных
_giveaway_locks: Dict[int, asyncio.Lock] = {}

# Защита от рекурсии в модальных окнах
_modal_error_tracking: Set[int] = set()  # tracking interaction IDs to prevent recursion
_MODAL_ERROR_TIMEOUT = 5  # секунды до сброса tracking


def parse_participants(participants_raw):
    """Парсит участников из JSON строки или возвращает список."""
    if isinstance(participants_raw, str):
        try:
            return json.loads(participants_raw)
        except json.JSONDecodeError:
            return []
    elif participants_raw is None:
        return []
    else:
        return participants_raw


async def retry_api_call(func, *args, max_retries=3, delay=1.0, **kwargs):
    """Вызов функции с повторными попытками при ошибках Discord API."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except discord.HTTPException as e:
            last_exception = e
            if e.status == 429:  # Rate limit
                retry_after = getattr(e, 'retry_after', delay * (attempt + 1))
                logger.warning(f"Rate limit, ждём {retry_after}с перед попыткой {attempt + 2}/{max_retries}")
                await asyncio.sleep(retry_after)
            elif e.status >= 500:  # Server error
                logger.warning(f"Серверная ошибка Discord ({e.status}), попытка {attempt + 2}/{max_retries}")
                await asyncio.sleep(delay * (attempt + 1))
            else:
                raise  # Client error - не retry
        except Exception as e:
            last_exception = e
            logger.warning(f"Ошибка при вызове API (попытка {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(delay * (attempt + 1))
    
    logger.error(f"Все {max_retries} попыток исчерпаны")
    if last_exception:
        raise last_exception


def cleanup_old_cache():
    """Очистка старых записей из кэша при превышении лимита."""
    while len(_active_giveaways) > _MAX_CACHE_SIZE:
        try:
            _active_giveaways.popitem(last=False)  # Удаляем oldest (FIFO)
        except KeyError:
            # Кэш пуст, выходим
            break


class GiveawayModal(Modal):
    """Модальное окно для ввода параметров розыгрыша."""
    
    def __init__(self, channel: discord.TextChannel, giveaway_cog: 'Giveaway'):
        super().__init__(title="🏆 Создание розыгрыша", timeout=600)
        self.channel = channel
        self.giveaway_cog = giveaway_cog
        
        self.prize_input = TextInput(
            label="Приз(ы)",
            placeholder="Например: Steam Gift Card $10 или несколько призов (каждый с новой строки)",
            min_length=5,
            max_length=1000,
            required=True,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.prize_input)
        
        self.duration_input = TextInput(
            label="Длительность",
            placeholder="Формат: 30m, 1h, 2h30m, 1d",
            min_length=2,
            max_length=20,
            required=True
        )
        self.add_item(self.duration_input)
        
        self.winners_input = TextInput(
            label="Количество победителей",
            placeholder="Число от 1 до 99",
            min_length=1,
            max_length=2,
            required=True,
            default="1"
        )
        self.add_item(self.winners_input)
        
        self.requirements_input = TextInput(
            label="Требования (опционально)",
            placeholder="ID роли или название (или '-' если нет)",
            min_length=1,
            max_length=100,
            required=False,
            default="-"
        )
        self.add_item(self.requirements_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Обработка отправки модального окна."""
        try:
            # Валидация данных перед отправкой
            validation_error = await self._validate_inputs(interaction)
            if validation_error:
                await interaction.response.send_message(validation_error, ephemeral=True)
                return
            
            data = {
                'channel': self.channel,
                'prize': self.prize_input.value.strip(),
                'duration': self.duration_input.value.strip(),
                'winners_count': self.winners_input.value.strip(),
                'requirements': self.requirements_input.value.strip()
            }
            await self.giveaway_cog.create_giveaway_from_data(interaction, data)
        except Exception as e:
            logger.error(f"Ошибка при создании розыгрыша: {e}", exc_info=True)
            await self._safe_send_error(interaction, "❌ Произошла ошибка при создании розыгрыша.")
    
    async def _validate_inputs(self, interaction: discord.Interaction) -> Optional[str]:
        """Валидация введённых данных. Возвращает сообщение об ошибке или None."""
        # Проверка длительности
        duration_result = self.giveaway_cog.parse_duration(self.duration_input.value)
        if duration_result is None:
            return "❌ Неверный формат длительности! Используйте формат: 30m, 1h, 2h30m, 1d"
        
        # Проверка количества победителей
        try:
            winners_count = int(self.winners_input.value)
            if winners_count < 1 or winners_count > 99:
                return "❌ Количество победителей должно быть от 1 до 99!"
        except ValueError:
            return "❌ Количество победителей должно быть числом!"
        
        # Проверка требований (роли)
        requirements_str = self.requirements_input.value.strip()
        if requirements_str and requirements_str != '-':
            try:
                role_id = int(requirements_str)
                role = interaction.guild.get_role(role_id)
                if not role:
                    return f"❌ Роль с ID {role_id} не найдена!"
            except ValueError:
                # Пробуем найти по имени
                role = discord.utils.get(interaction.guild.roles, name=requirements_str)
                if not role:
                    return f"❌ Роль '{requirements_str}' не найдена!"
        
        # Проверка прав бота в канале
        bot_perms = self.channel.permissions_for(interaction.guild.me)
        if not bot_perms.send_messages:
            return "❌ У меня нет прав на отправку сообщений в этот канал!"
        if not bot_perms.embed_links:
            return "❌ У меня нет прав на отправку embed-сообщений в этот канал!"
        
        return None
    
    async def _safe_send_error(self, interaction: discord.Interaction, message: str):
        """Безопасная отправка сообщения об ошибке с защитой от рекурсии."""
        import time
        current_time = int(time.time())
        interaction_hash = hash((interaction.id, interaction.type, current_time // _MODAL_ERROR_TIMEOUT))
        
        # Проверка на рекурсию
        if interaction_hash in _modal_error_tracking:
            logger.warning(f"Попытка повторной отправки ошибки для interaction {interaction.id}, пропускаем")
            return
        
        _modal_error_tracking.add(interaction_hash)
        
        # Планируем удаление из tracking через таймаут
        asyncio.create_task(self._clear_error_tracking(interaction_hash))
        
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException as e:
            logger.error(f"Не удалось отправить сообщение об ошибке через Discord API: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при отправке сообщения: {e}", exc_info=True)
        finally:
            _modal_error_tracking.discard(interaction_hash)
    
    async def _clear_error_tracking(self, interaction_hash: int):
        """Очистка tracking после таймаута."""
        await asyncio.sleep(_MODAL_ERROR_TIMEOUT)
        _modal_error_tracking.discard(interaction_hash)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        """Обработка ошибок модального окна с защитой от рекурсии."""
        logger.error(f"Ошибка в модальном окне розыгрыша: {error}", exc_info=True)
        await self._safe_send_error(interaction, "❌ Произошла ошибка при создании розыгрыша.")


class GiveawayCreateButton(Button):
    """Кнопка для создания розыгрыша."""
    
    def __init__(self, giveaway_cog: 'Giveaway', guild: discord.Guild):
        super().__init__(
            label="🏆 Создать розыгрыш",
            style=discord.ButtonStyle.success,
            custom_id="giveaway_create_button"
        )
        self.giveaway_cog = giveaway_cog
        self.guild = guild
    
    async def callback(self, interaction: discord.Interaction):
        # Получаем выбранный канал из родительского View
        view = self.view
        if isinstance(view, GiveawayCreateView) and hasattr(view, 'selected_channel'):
            # Проверяем, был ли выбран канал через Select-меню
            # Если нет - используем текущий канал
            selected_channel = None
            for child in view.children:
                if isinstance(child, GiveawayChannelSelect) and child.values:
                    selected_channel_id = int(child.values[0])
                    selected_channel = self.guild.get_channel(selected_channel_id)
                    break
            
            if not selected_channel:
                # Если канал не выбран, используем текущий канал
                selected_channel = interaction.channel
            
            # Открываем модальное окно с выбранным каналом
            modal = GiveawayModal(selected_channel, self.giveaway_cog)
            await interaction.response.send_modal(modal)
        else:
            # Fallback - используем текущий канал
            modal = GiveawayModal(interaction.channel, self.giveaway_cog)
            await interaction.response.send_modal(modal)


class GiveawayChannelSelect(Select):
    """Select-меню для выбора канала создания розыгрыша."""
    
    def __init__(self, guild: discord.Guild):
        # Получаем текстовые каналы, доступные боту
        text_channels = [
            channel for channel in guild.text_channels
            if channel.permissions_for(guild.me).send_messages
        ][:25]  # Ограничение Discord - максимум 25 опций
        
        options = [
            discord.SelectOption(
                label=channel.name[:100],  # Ограничение длины label
                value=str(channel.id),
                emoji="📝"
            )
            for channel in text_channels
        ]
        
        super().__init__(
            placeholder="Выберите канал для розыгрыша...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.guild = guild
    
    async def callback(self, interaction: discord.Interaction):
        # Просто подтверждаем выбор, канал будет использован при нажатии кнопки
        selected_channel_id = int(self.values[0])
        selected_channel = self.guild.get_channel(selected_channel_id)
        await interaction.response.send_message(
            f"✅ Выбран канал: {selected_channel.mention}",
            ephemeral=True
        )


class GiveawayCreateView(View):
    """View с Select-меню выбора канала и кнопкой для создания розыгрыша."""
    
    def __init__(self, giveaway_cog: 'Giveaway', guild: discord.Guild):
        super().__init__(timeout=None)
        self.giveaway_cog = giveaway_cog
        self.guild = guild
        self.selected_channel: Optional[discord.TextChannel] = None
        self.add_item(GiveawayChannelSelect(guild))
        self.add_item(GiveawayCreateButton(giveaway_cog, guild))


class GiveawayCancelSelectView(View):
    """View с Select-меню для выбора розыгрыша на отмену."""
    
    def __init__(self, giveaway_cog: 'Giveaway', options: list):
        super().__init__(timeout=None)
        self.giveaway_cog = giveaway_cog
        self.add_item(GiveawayCancelSelect(giveaway_cog, options))


class GiveawayCancelSelect(Select):
    """Select-меню для выбора розыгрыша на отмену."""
    
    def __init__(self, giveaway_cog: 'Giveaway', options: list):
        super().__init__(
            placeholder="Выберите розыгрыш для отмены...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.giveaway_cog = giveaway_cog
    
    async def callback(self, interaction: discord.Interaction):
        giveaway_id = int(self.values[0])
        # Отменяем розыгрыш (удаляем из БД)
        db = await self.giveaway_cog.get_db_module()
        
        # Получаем данные розыгрыша перед отменой для обновления embed
        giveaway = await db.get_giveaway_by_id(giveaway_id)
        
        await db.cancel_giveaway(giveaway_id)
        
        embed = discord.Embed(
            title="❌ Розыгрыш отменён",
            description=f"Розыгрыш #{giveaway_id} был успешно отменён.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Удаляем сообщение розыгрыша при отмене
        if giveaway:
            try:
                channel = self.giveaway_cog.bot.get_channel(giveaway['channel_id'])
                if channel:
                    message = await channel.fetch_message(giveaway['message_id'])
                    await message.delete()
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение розыгрыша #{giveaway_id}: {e}")


class GiveawayEndSelectView(View):
    """View с Select-меню для выбора розыгрыша на завершение."""
    
    def __init__(self, giveaway_cog: 'Giveaway', options: list):
        super().__init__(timeout=None)
        self.giveaway_cog = giveaway_cog
        self.add_item(GiveawayEndSelect(giveaway_cog, options))


class GiveawayEndSelect(Select):
    """Select-меню для выбора розыгрыша на завершение."""
    
    def __init__(self, giveaway_cog: 'Giveaway', options: list):
        super().__init__(
            placeholder="Выберите розыгрыш для завершения...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.giveaway_cog = giveaway_cog
    
    async def callback(self, interaction: discord.Interaction):
        giveaway_id = int(self.values[0])
        # Завершаем розыгрыш
        await self.giveaway_cog.end_giveaway(interaction, giveaway_id)


class GiveawayMainView(View):
    """Основное меню управления розыгрышами с кнопками действий."""
    
    def __init__(self, giveaway_cog: 'Giveaway', guild: Optional[discord.Guild] = None):
        super().__init__(timeout=None)
        self.giveaway_cog = giveaway_cog
        self.guild = guild
        
        # Кнопка создания розыгрыша
        create_button = Button(
            label="🏆 Создать",
            style=discord.ButtonStyle.success,
            custom_id="giveaway_main_create"
        )
        create_button.callback = self.create_callback
        self.add_item(create_button)
        
        # Кнопка отмены розыгрыша
        cancel_button = Button(
            label="❌ Отменить",
            style=discord.ButtonStyle.danger,
            custom_id="giveaway_main_cancel"
        )
        cancel_button.callback = self.cancel_callback
        self.add_item(cancel_button)
        
        # Кнопка завершения розыгрыша
        end_button = Button(
            label="🎉 Завершить",
            style=discord.ButtonStyle.primary,
            custom_id="giveaway_main_end"
        )
        end_button.callback = self.end_callback
        self.add_item(end_button)
    
    async def create_callback(self, interaction: discord.Interaction):
        """Обработчик кнопки создания розыгрыша."""
        # Получаем guild из interaction, если не был установлен при создании
        guild = self.guild or interaction.guild
        view = GiveawayCreateView(self.giveaway_cog, guild)
        embed = discord.Embed(
            title="🏆 Создание розыгрыша",
            description=(
                "Выберите канал и нажмите кнопку для создания.\n\n"
                "В модальном окне вы сможете указать:\n"
                "• Приз\n"
                "• Длительность (30m, 1h, 2h30m, 1d)\n"
                "• Количество победителей\n"
                "• Требования (опционально)"
            ),
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def cancel_callback(self, interaction: discord.Interaction):
        """Обработчик кнопки отмены розыгрыша."""
        # Проверяем права
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ У вас нет прав для управления розыгрышами.", ephemeral=True)
            return
        
        # Получаем активные розыгрыши
        db = await self.giveaway_cog.get_db_module()
        active_giveaways = await db.get_active_giveaways(interaction.guild.id)
        
        if not active_giveaways:
            embed = discord.Embed(
                title="❌ Отмена розыгрыша",
                description="Нет активных розыгрышей для отмены.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Создаём список розыгрышей для выбора
        options = []
        for gw in active_giveaways[:25]:  # Максимум 25 опций
            giveaway_id = gw['id']
            prize = gw['prize'][:50] + "..." if len(gw['prize']) > 50 else gw['prize']
            options.append(discord.SelectOption(
                label=f"#{giveaway_id}: {prize}",
                value=str(giveaway_id),
                description=f"Канал: {interaction.guild.get_channel(gw['channel_id'])}"
            ))
        
        view = GiveawayCancelSelectView(self.giveaway_cog, options)
        embed = discord.Embed(
            title="❌ Отмена розыгрыша",
            description="Выберите розыгрыш для отмены:",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def end_callback(self, interaction: discord.Interaction):
        """Обработчик кнопки завершения розыгрыша."""
        # Проверяем права
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ У вас нет прав для управления розыгрышами.", ephemeral=True)
            return
        
        # Получаем активные розыгрыши
        db = await self.giveaway_cog.get_db_module()
        active_giveaways = await db.get_active_giveaways(interaction.guild.id)
        
        if not active_giveaways:
            embed = discord.Embed(
                title="🎉 Завершение розыгрыша",
                description="Нет активных розыгрышей для завершения.",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Создаём список розыгрышей для выбора
        options = []
        for gw in active_giveaways[:25]:  # Максимум 25 опций
            giveaway_id = gw['id']
            prize = gw['prize'][:50] + "..." if len(gw['prize']) > 50 else gw['prize']
            participants_count = len(parse_participants(gw.get('participants')))
            options.append(discord.SelectOption(
                label=f"#{giveaway_id}: {prize}",
                value=str(giveaway_id),
                description=f"Участников: {participants_count}"
            ))
        
        view = GiveawayEndSelectView(self.giveaway_cog, options)
        embed = discord.Embed(
            title="🎉 Завершение розыгрыша",
            description="Выберите розыгрыш для завершения:",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class GiveawayView(View):
    """View с кнопкой для участия в розыгрыше."""
    
    def __init__(self, giveaway_cog: 'Giveaway', giveaway_id: int = 0):
        super().__init__(timeout=None)
        self.giveaway_cog = giveaway_cog
        self.giveaway_id = giveaway_id
    
    @discord.ui.button(label="🏆 Участвовать", style=discord.ButtonStyle.success, custom_id="giveaway_participate")
    async def participate_button(self, interaction: discord.Interaction, button: Button):
        if ':' in button.custom_id:
            try:
                actual_giveaway_id = int(button.custom_id.split(':')[1])
            except (ValueError, IndexError):
                actual_giveaway_id = self.giveaway_id
        else:
            actual_giveaway_id = self.giveaway_id
        
        await self.giveaway_cog.handle_participation(interaction, actual_giveaway_id)


class GiveawayPersistentView(View):
    """Persistent View для обработки кнопок после перезапуска бота.
    
    Этот класс используется только для регистрации persistent view в bot.add_view().
    Фактический giveaway_id извлекается из custom_id кнопки при нажатии.
    """
    
    def __init__(self, giveaway_cog: 'Giveaway'):
        super().__init__(timeout=None)
        self.giveaway_cog = giveaway_cog
    
    @discord.ui.button(label="🏆 Участвовать", style=discord.ButtonStyle.success, custom_id="giveaway_participate:0")
    async def participate_button(self, interaction: discord.Interaction, button: Button):
        # Извлекаем giveaway_id из custom_id кнопки (формат: "giveaway_participate:{giveaway_id}")
        if ':' in button.custom_id:
            try:
                actual_giveaway_id = int(button.custom_id.split(':')[1])
            except (ValueError, IndexError):
                logger.error(f"Не удалось извлечь giveaway_id из custom_id: {button.custom_id}")
                return
        else:
            logger.error(f"custom_id не содержит giveaway_id: {button.custom_id}")
            return
        
        await self.giveaway_cog.handle_participation(interaction, actual_giveaway_id)


class Giveaway(commands.Cog):
    """Ког для управления системой розыгрышей."""
    
    def __init__(self, bot):
        self.bot = bot
        self.db_module = None
    
    async def cog_load(self):
        """Запускаем цикл при загрузке кога."""
        self.giveaway_loop.start()
    
    async def cog_unload(self):
        """Останавливаем цикл и очищаем кэш при выгрузке кога."""
        self.giveaway_loop.cancel()
        _giveaway_debounce.clear()
        _active_giveaways.clear()
        _giveaway_locks.clear()
        _modal_error_tracking.clear()
    
    async def get_db_module(self):
        """Ленивая загрузка модуля БД."""
        if self.db_module is None:
            from database import mysql_connector
            self.db_module = mysql_connector
        return self.db_module
    
    def parse_duration(self, duration_str: str):
        """Парсинг строки длительности в datetime с использованием timezone-aware datetime."""
        total_minutes = 0
        
        # Паттерн для поиска часов и минут
        hours_match = re.search(r'(\d+)\s*h', duration_str, re.IGNORECASE)
        minutes_match = re.search(r'(\d+)\s*m', duration_str, re.IGNORECASE)
        days_match = re.search(r'(\d+)\s*d', duration_str, re.IGNORECASE)
        
        if days_match:
            total_minutes += int(days_match.group(1)) * 24 * 60
        if hours_match:
            total_minutes += int(hours_match.group(1)) * 60
        if minutes_match:
            total_minutes += int(minutes_match.group(1))
        
        # Если ничего не найдено, пробуем интерпретировать как минуты
        if total_minutes == 0:
            try:
                total_minutes = int(duration_str)
            except ValueError:
                return None
        
        if total_minutes <= 0:
            return None
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=total_minutes)
    
    def format_time_left(self, delta: timedelta) -> str:
        """Форматирование оставшегося времени (только часы и минуты)."""
        total_seconds = int(delta.total_seconds())
        
        if total_seconds < 0:
            return "0 мин."
        elif total_seconds < 60:
            return "1 мин."
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            return f"{minutes} мин."
        else:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            if minutes == 0:
                return f"{hours} ч."
            return f"{hours} ч. {minutes} мин."
    
    @tasks.loop(seconds=30)
    async def giveaway_loop(self):
        """Цикл проверки и авто-завершения розыгрышей."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)  # offset-naive для совместимости с БД
        
        # Загружаем активные розыгрыши из БД
        db = await self.get_db_module()
        
        for giveaway_id in list(_active_giveaways.keys()):
            giveaway_data = _active_giveaways[giveaway_id]
            
            if giveaway_data['end_time'] <= now:
                # Время истекло, завершаем
                giveaway = await db.get_giveaway_by_id(giveaway_id)
                if giveaway and giveaway['status'] == 'active':
                    await self.finalize_giveaway(giveaway_id, giveaway)
            else:
                # Розыгрыш активен - обновляем таймер в embed
                giveaway = await db.get_giveaway_by_id(giveaway_id)
                if giveaway and giveaway['status'] == 'active':
                    await self.update_giveaway_embed(giveaway_id, giveaway, show_winners=False)
        
        # Также проверяем все активные розыгрыши в БД (на случай перезапуска бота)
        all_active = await db.get_all_active_giveaways()
        for giveaway in all_active:
            if giveaway['id'] not in _active_giveaways:
                # Добавляем в кэш
                _active_giveaways[giveaway['id']] = {
                    'id': giveaway['id'],
                    'message_id': giveaway['message_id'],
                    'end_time': giveaway['end_time'],
                    'status': giveaway['status']
                }
            
            # Если время истекло и ещё не обработано
            if giveaway['end_time'] <= now and giveaway['status'] == 'active':
                await self.finalize_giveaway(giveaway['id'], giveaway)
            elif giveaway['status'] == 'active':
                # Обновляем таймер для активных розыгрышей
                await self.update_giveaway_embed(giveaway['id'], giveaway, show_winners=False)
    
    @giveaway_loop.before_loop
    async def before_giveaway_loop(self):
        """Загрузка активных розыгрышей при старте."""
        await self.bot.wait_until_ready()
        db = await self.get_db_module()
        
        # Загружаем все активные розыгрыши
        active_giveaways = await db.get_all_active_giveaways()
        
        for gw in active_giveaways:
            _active_giveaways[gw['id']] = {
                'id': gw['id'],
                'message_id': gw['message_id'],
                'end_time': gw['end_time'],
                'status': gw['status']
            }
    
    async def finalize_giveaway(self, giveaway_id: int, giveaway: dict):
        """Финализация розыгрыша: выбор победителей и объявление."""
        # Получаем или создаём блокировку для этого розыгрыша
        if giveaway_id not in _giveaway_locks:
            _giveaway_locks[giveaway_id] = asyncio.Lock()
        
        async with _giveaway_locks[giveaway_id]:
            db = await self.get_db_module()
            
            participants = parse_participants(giveaway.get('participants'))
            winners_count = min(giveaway['winners_count'], len(participants))
            
            # Выбор победителей - используем secrets для криптографически безопасного random
            import secrets
            if participants and winners_count > 0:
                # Создаём secure random generator
                secure_random = secrets.SystemRandom()
                winners = secure_random.sample(participants, winners_count)
            else:
                winners = []
            
            # Если призов несколько, сопоставляем каждого победителя с призом
            prize_winner_mapping = []
            prizes = giveaway.get('prize', [])
            if isinstance(prizes, list) and len(prizes) > 1:
                # Каждый победитель получает свой приз по порядку
                for i, winner in enumerate(winners):
                    if i < len(prizes):
                        prize_winner_mapping.append({'prize': prizes[i], 'winner': winner})
            else:
                # Один приз или все победители получают одинаковый приз
                single_prize = prizes[0] if isinstance(prizes, list) else prizes
                for winner in winners:
                    prize_winner_mapping.append({'prize': single_prize, 'winner': winner})
            
            # Обновляем в БД
            await db.update_giveaway_winners(giveaway_id, winners)
            await db.update_giveaway_status(giveaway_id, 'completed')
            
            # Сохраняем mapping призов и победителей (для отображения)
            await db.execute_query(
                "UPDATE giveaways SET winners = %s WHERE id = %s",
                (json.dumps(prize_winner_mapping, ensure_ascii=False), giveaway_id)
            )
            
            # Удаляем из кэша
            _active_giveaways.pop(giveaway_id, None)
            
            # Обновляем embed
            giveaway['winners'] = prize_winner_mapping
            giveaway['status'] = 'completed'
            await self.update_giveaway_embed(giveaway_id, giveaway, show_winners=True)
            
            # Лог
            guild = self.bot.get_guild(giveaway['guild_id'])
            if guild:
                log_channel = await self.get_log_channel(guild)
                if log_channel:
                    embed = discord.Embed(
                        title="🏆 Розыгрыш завершён",
                        description=f"Розыгрыш #{giveaway_id}",
                        color=discord.Color.green()
                    )
                    
                    # Форматируем призы для лога
                    if isinstance(prizes, list) and len(prizes) > 1:
                        prize_display = '\n'.join([f"{i+1}. {p}" for i, p in enumerate(prizes)])
                    else:
                        prize_display = prizes[0] if isinstance(prizes, list) else prizes
                    
                    embed.add_field(name="Приз(ы)", value=prize_display, inline=False)
                    
                    # Форматируем победителей с их призами
                    if prize_winner_mapping:
                        winners_text = '\n'.join([
                            f"• <@{pw['winner']}> — {pw['prize']}"
                            for pw in prize_winner_mapping
                        ])
                    else:
                        winners_text = "Нет победителей"
                    
                    embed.add_field(name="Победители", value=winners_text, inline=False)
                    embed.add_field(name="Участников", value=len(participants), inline=True)
                    await log_channel.send(embed=embed)
            
            # Уведомляем победителей в ЛС
            for pw in prize_winner_mapping:
                try:
                    winner_user = await self.bot.fetch_user(pw['winner'])
                    
                    # Форматируем призы для отображения в ЛС
                    prize_raw = giveaway.get('prize', '')
                    if isinstance(prize_raw, list) and len(prize_raw) > 1:
                        # Для нескольких призов показываем конкретный приз победителя
                        dm_embed = discord.Embed(
                            title="🎉 Поздравляем с победой!",
                            description=f"Вы выиграли в розыгрыше #{giveaway_id}!\n\n**Ваш приз:** {pw['prize']}",
                            color=discord.Color.gold()
                        )
                    else:
                        single_prize = prize_raw[0] if isinstance(prize_raw, list) else prize_raw
                        dm_embed = discord.Embed(
                            title="🎉 Поздравляем с победой!",
                            description=f"Вы выиграли в розыгрыше #{giveaway_id}!\n\n**Приз:** {single_prize}",
                            color=discord.Color.gold()
                        )
                    dm_embed.add_field(name="Что делать дальше?", value="Свяжитесь с организатором розыгрыша для получения приза!", inline=False)
                    dm_embed.set_footer(text=f"Розыгрыш проведён на сервере {guild.name if guild else 'N/A'}")
                    await winner_user.send(embed=dm_embed)
                    logger.info(f"✅ Отправлено уведомление победителю {pw['winner']} в ЛС")
                except discord.Forbidden:
                    logger.warning(f"⚠️ Не удалось отправить ЛС победителю {pw['winner']} (закрыты ЛС)")
                except Exception as e:
                    logger.error(f"Ошибка при отправке ЛС победителю {pw['winner']}: {e}")
    
    async def end_giveaway(self, interaction: discord.Interaction, giveaway_id: int):
        """Завершение розыгрыша по ID (выбор победителей)."""
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❌ У вас нет прав для управления розыгрышами.", ephemeral=True)
            return
        
        db = await self.get_db_module()
        giveaway = await db.get_giveaway_by_id(giveaway_id)
        
        if not giveaway:
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Розыгрыш #{giveaway_id} не найден.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        if giveaway.get('status') == 'completed':
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Розыгрыш #{giveaway_id} уже завершён.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Финализируем розыгрыш
        await interaction.response.defer()
        await self.finalize_giveaway(giveaway_id, giveaway)
        
        embed = discord.Embed(
            title="🎉 Розыгрыш завершён",
            description=f"Розыгрыш #{giveaway_id} был успешно завершён. Победители выбраны!",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    # ==================== СОЗДАНИЕ РОЗЫГРЫША ====================
    
    @commands.command(name='giveaway', aliases=['gw'])
    @commands.has_permissions(manage_guild=True)
    async def giveaway(self, ctx):
        """
        Основная команда для управления розыгрышами через кнопки.
        
        Показывает панель с кнопками: Создать, Отменить, Завершить.
        """
        view = GiveawayMainView(self, ctx.guild)
        embed = discord.Embed(
            title="🏆 Система розыгрышей",
            description="Выберите действие для управления розыгрышами:",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed, view=view)
    
    async def create_giveaway_from_data(self, interaction: discord.Interaction, data: dict):
        """Создание розыгрыша из собранных данных."""
        # Проверка прав
        if not interaction.user.guild_permissions.manage_guild:
            return
        
        prize_raw = data.get('prize', '').strip()
        duration_str = data.get('duration', '').strip()
        winners_count_str = str(data.get('winners_count', '1')).strip()
        requirements_str = data.get('requirements', '').strip()
        channel = data.get('channel', interaction.channel)
        
        # Парсим призы - каждый с новой строки отдельный приз
        prizes = [p.strip() for p in prize_raw.split('\n') if p.strip()]
        if not prizes:
            await interaction.response.send_message("❌ Укажите хотя бы один приз!", ephemeral=True)
            return
        
        # Сохраняем как JSON строку если несколько призов, иначе как строку
        prize_data = json.dumps(prizes, ensure_ascii=False) if len(prizes) > 1 else prizes[0]
        
        # Парсим количество победителей
        try:
            winners_count = int(winners_count_str)
            if winners_count < 1 or winners_count > 99:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ Количество победителей должно быть от 1 до 99!", ephemeral=True)
            return
        
        # Проверка: количество победителей не должно превышать количество призов (если призов несколько)
        if len(prizes) > 1 and winners_count > len(prizes):
            await interaction.response.send_message(
                f"❌ Количество победителей ({winners_count}) не может превышать количество призов ({len(prizes)})!\n"
                "Каждый победитель получает один уникальный приз.", 
                ephemeral=True
            )
            return
        
        # Парсим длительность
        end_time = self.parse_duration(duration_str)
        if end_time is None:
            await interaction.response.send_message("❌ Неверный формат длительности! Используйте формат: 30m, 1h, 2h30m, 1d", ephemeral=True)
            return
        
        # Парсим требования (роль)
        required_role = None
        if requirements_str and requirements_str != '-':
            try:
                role_id = int(requirements_str)
                required_role = interaction.guild.get_role(role_id)
                if not required_role:
                    await interaction.response.send_message(f"❌ Роль с ID {role_id} не найдена!", ephemeral=True)
                    return
            except ValueError:
                # Пробуем найти роль по имени
                required_role = discord.utils.get(interaction.guild.roles, name=requirements_str)
                if not required_role:
                    await interaction.response.send_message(f"❌ Роль '{requirements_str}' не найдена!", ephemeral=True)
                    return
        
        # Формируем требования
        requirements = {
            'role_id': required_role.id if required_role else None,
            'require_subscription': False,
            'require_verification': False
        }
        
        # Создаём запись в БД
        db = await self.get_db_module()
        giveaway_data = {
            'message_id': None,  # Будет установлено после отправки
            'channel_id': channel.id,
            'guild_id': interaction.guild.id,
            'creator_id': interaction.user.id,
            'prize': prize_data,  # Может быть строкой или списком
            'winners_count': winners_count,
            'start_time': datetime.now(timezone.utc).replace(tzinfo=None),  # offset-naive для совместимости с БД
            'end_time': end_time,  # уже offset-naive из parse_duration
            'status': 'active',
            'requirements': requirements,
            'participants': [],
            'winners': [],
            'prizes_claimed': []  # Отслеживание какие призы уже забрали (индексы)
        }
        
        giveaway_id = await db.create_giveaway(giveaway_data)
        if not giveaway_id:
            await interaction.response.send_message("❌ Ошибка при создании розыгрыша в БД!", ephemeral=True)
            return
        
        # Создаём Embed
        embed = self.create_giveaway_embed(giveaway_data, giveaway_id)
        
        # Создаём view с кнопкой, используя dynamic custom_id с giveaway_id
        view = GiveawayView(self, giveaway_id)
        
        # Для persistent view после перезапуска бота custom_id должен содержать giveaway_id
        # Обновляем custom_id кнопки перед отправкой
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.custom_id == "giveaway_participate":
                item.custom_id = f"giveaway_participate:{giveaway_id}"
        
        # Отправляем сообщение в выбранный канал с проверкой существования канала
        if not channel:
            logger.error(f"Канал не найден для отправки розыгрыша")
            await interaction.response.send_message("❌ Канал не найден!", ephemeral=True)
            return
        
        try:
            message = await channel.send(embed=embed, view=view)
        except discord.NotFound:
            logger.error(f"Канал {channel.id} не найден (возможно, удалён)")
            await interaction.response.send_message("❌ Канал был удалён!", ephemeral=True)
            return
        except discord.Forbidden:
            logger.error(f"Нет прав на отправку сообщений в канал {channel.id}")
            await interaction.response.send_message("❌ Нет прав на отправку сообщений в этот канал!", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения розыгрыша: {e}", exc_info=True)
            await interaction.response.send_message("❌ Произошла ошибка при создании розыгрыша!", ephemeral=True)
            return
        
        # Обновляем message_id в БД
        await db.update_giveaway_message_id(giveaway_id, message.id)
        
        # Добавляем в кэш
        _active_giveaways[giveaway_id] = giveaway_data
        giveaway_data['id'] = giveaway_id
        giveaway_data['message_id'] = message.id
        
        # Логирование
        prize_display = ', '.join(prizes) if isinstance(prize_data, list) else prize_data
        logger.info(f"🏆 Создан розыгрыш #{giveaway_id} | Приз(ы): {prize_display} | Победителей: {winners_count} | Окончание: {end_time}")
        
        await interaction.response.send_message(
            "✅ Розыгрыш успешно создан!",
            ephemeral=True
        )
    
    def create_giveaway_embed(self, giveaway_data: dict, giveaway_id: int) -> discord.Embed:
        """Создание embed для розыгрыша."""
        time_left = giveaway_data['end_time'] - datetime.now(timezone.utc).replace(tzinfo=None)
        
        # Форматируем призы для отображения
        prize_raw = giveaway_data['prize']
        if isinstance(prize_raw, list):
            # Несколько призов - нумерованный список
            prize_display = '\n'.join([f"{i+1}. {p}" for i, p in enumerate(prize_raw)])
            prize_title = f"🏆 РОЗЫГРЫШ ({len(prize_raw)} ПРИЗОВ)!"
        else:
            prize_display = prize_raw
            prize_title = "🏆 НОВЫЙ РОЗЫГРЫШ!"
        
        embed = discord.Embed(
            title=prize_title,
            description=f"**Приз(ы):**\n{prize_display}",
            color=discord.Color.gold()
        )
        
        embed.add_field(name="🏆 Победителей", value=str(giveaway_data['winners_count']), inline=True)
        embed.add_field(name="⏰ Длительность", value=self.format_time_left(time_left), inline=True)
        embed.add_field(name="👥 Участников", value="0", inline=True)
        
        # Требования
        requirements_text = []
        reqs = giveaway_data.get('requirements', {})
        if reqs.get('role_id'):
            guild = self.bot.get_guild(giveaway_data['guild_id'])
            if guild:
                role = guild.get_role(reqs['role_id'])
                if role:
                    requirements_text.append(f"• Роль: {role.name}")
        if reqs.get('require_subscription'):
            requirements_text.append("• Требуется подписка")
        if reqs.get('require_verification'):
            requirements_text.append("• Требуется верификация")
        
        if requirements_text:
            embed.add_field(name="📋 Требования", value="\n".join(requirements_text), inline=False)
        
        embed.set_footer(text=f"ID розыгрыша: {giveaway_id} | Каждый победитель получает 1 уникальный приз" if isinstance(prize_raw, list) else f"ID розыгрыша: {giveaway_id}")
        embed.timestamp = giveaway_data['start_time']
        
        return embed
    
    async def update_giveaway_embed(self, giveaway_id: int, giveaway: dict, show_winners: bool = False):
        """Обновление embed розыгрыша с retry-логикой и проверкой прав."""
        try:
            channel = self.bot.get_channel(giveaway['channel_id'])
            if not channel:
                logger.warning(f"Канал {giveaway['channel_id']} не найден для розыгрыша #{giveaway_id}")
                return
            
            # Проверка прав бота перед попыткой редактирования
            # Получаем объект Member для бота в этой гильдии, так как permissions_for требует Member, а не ClientUser
            guild = channel.guild
            bot_member = guild.me if guild else None
            
            if bot_member:
                bot_perms = channel.permissions_for(bot_member)
                if not bot_perms.manage_messages:
                    logger.warning(f"Нет прав на редактирование сообщений в канале {channel.id} для розыгрыша #{giveaway_id}")
            else:
                logger.warning(f"Не удалось получить объект участника для бота в гильдии {channel.guild_id if hasattr(channel, 'guild_id') else 'N/A'}")
            
            message = await retry_api_call(channel.fetch_message, giveaway['message_id'])
            
            participants_count = len(parse_participants(giveaway.get('participants')))
            
            if show_winners:
                # Завершённый розыгрыш
                winners_data = giveaway.get('winners', [])
                
                # Проверяем формат winners - может быть списком ID или списком dict {prize, winner}
                if winners_data and isinstance(winners_data[0], dict):
                    # Новый формат с mapping призов и победителей
                    winner_mentions = [f"<@{pw['winner']}> — {pw['prize']}" for pw in winners_data]
                else:
                    # Старый формат - просто список ID
                    winner_mentions = [f"<@{w}>" for w in winners_data] if winners_data else ["Нет победителей"]
                
                # Форматируем призы для отображения
                prize_raw = giveaway.get('prize', '')
                if isinstance(prize_raw, list) and len(prize_raw) > 1:
                    prize_display = '\n'.join([f"{i+1}. {p}" for i, p in enumerate(prize_raw)])
                else:
                    prize_display = prize_raw[0] if isinstance(prize_raw, list) else prize_raw
                
                embed = discord.Embed(
                    title="🎉 РОЗЫГРЫШ ЗАВЕРШЁН! 🎉",
                    description=f"**Приз(ы):**\n{prize_display}",
                    color=discord.Color.green() if winners_data else discord.Color.red()
                )
                embed.add_field(name="🏆 Победители", value="\n".join(winner_mentions) if winner_mentions else "Нет победителей", inline=False)
                embed.add_field(name="👥 Участников", value=participants_count, inline=True)
                embed.add_field(name="📊 Статус", value="Завершён", inline=True)
            else:
                # Активный розыгрыш
                time_left = giveaway['end_time'] - datetime.now(timezone.utc).replace(tzinfo=None)  # offset-naive для совместимости
                if time_left.total_seconds() <= 0:
                    time_left_str = "Завершается..."
                else:
                    time_left_str = self.format_time_left(time_left)
                
                # Форматируем призы для отображения
                prize_raw = giveaway.get('prize', '')
                if isinstance(prize_raw, list) and len(prize_raw) > 1:
                    prize_display = '\n'.join([f"{i+1}. {p}" for i, p in enumerate(prize_raw)])
                    title_text = f"🏆 РОЗЫГРЫШ ({len(prize_raw)} ПРИЗОВ)"
                else:
                    prize_display = prize_raw[0] if isinstance(prize_raw, list) else prize_raw
                    title_text = "🏆 РОЗЫГРЫШ"
                
                embed = discord.Embed(
                    title=title_text,
                    description=f"**Приз(ы):**\n{prize_display}",
                    color=discord.Color.gold()
                )
                embed.add_field(name="🏆 Победителей", value=str(giveaway['winners_count']), inline=True)
                embed.add_field(name="⏰ Осталось", value=time_left_str, inline=True)
                embed.add_field(name="👥 Участников", value=participants_count, inline=True)
            
            embed.set_footer(text=f"ID розыгрыша: {giveaway_id}" + (" | Каждый победитель получает 1 уникальный приз" if isinstance(giveaway.get('prize'), list) and len(giveaway['prize']) > 1 else ""))
            
            # Создаём view с кнопкой (отключенной если розыгрыш завершён)
            view = GiveawayView(self, giveaway_id)
            for item in view.children:
                if isinstance(item, discord.ui.Button) and item.custom_id.startswith("giveaway_participate"):
                    item.custom_id = f"giveaway_participate:{giveaway_id}"
                    if show_winners:
                        # Отключаем кнопку для завершённого розыгрыша
                        item.disabled = True
                        item.label = "🏆 Розыгрыш завершён"
                        item.style = discord.ButtonStyle.secondary
            
            await retry_api_call(message.edit, embed=embed, view=view)
            logger.debug(f"Embed розыгрыша #{giveaway_id} успешно обновлён")
            
        except discord.NotFound:
            logger.warning(f"Сообщение розыгрыша #{giveaway_id} не найдено (возможно, удалено). Создаю новое сообщение...")
            # Сообщение удалено, создаём новое
            await self.recreate_giveaway_message(giveaway_id, giveaway, show_winners)
        except discord.Forbidden:
            logger.error(f"Нет прав на редактирование сообщения розыгрыша #{giveaway_id}")
        except Exception as e:
            logger.error(f"Ошибка при обновлении embed розыгрыша #{giveaway_id}: {e}", exc_info=True)
    
    async def recreate_giveaway_message(self, giveaway_id: int, giveaway: dict, show_winners: bool = False):
        """Пересоздание сообщения розыгрыша, если оригинал был удалён."""
        try:
            channel = self.bot.get_channel(giveaway['channel_id'])
            if not channel:
                logger.error(f"Канал {giveaway['channel_id']} не найден для пересоздания розыгрыша #{giveaway_id}")
                return
            
            participants_count = len(parse_participants(giveaway.get('participants')))
            
            if show_winners:
                # Завершённый розыгрыш
                winners = giveaway.get('winners', [])
                winner_mentions = [f"<@{w}>" for w in winners] if winners else ["Нет победителей"]
                
                embed = discord.Embed(
                    title="🎉 РОЗЫГРЫШ ЗАВЕРШЁН! 🎉",
                    description=f"**Приз:** {giveaway['prize']}",
                    color=discord.Color.green() if winners else discord.Color.red()
                )
                embed.add_field(name="🏆 Победители", value=", ".join(winner_mentions), inline=False)
                embed.add_field(name="👥 Участников", value=participants_count, inline=True)
                embed.add_field(name="📊 Статус", value="Завершён", inline=True)
            else:
                # Активный розыгрыш
                time_left = giveaway['end_time'] - datetime.now(timezone.utc).replace(tzinfo=None)
                if time_left.total_seconds() <= 0:
                    time_left_str = "Завершается..."
                else:
                    time_left_str = self.format_time_left(time_left)
                
                embed = discord.Embed(
                    title="🏆 РОЗЫГРЫШ",
                    description=f"**Приз:** {giveaway['prize']}",
                    color=discord.Color.gold()
                )
                embed.add_field(name="🏆 Победителей", value=str(giveaway['winners_count']), inline=True)
                embed.add_field(name="⏰ Осталось", value=time_left_str, inline=True)
                embed.add_field(name="👥 Участников", value=participants_count, inline=True)
            
            embed.set_footer(text=f"ID розыгрыша: {giveaway_id}")
            
            # Создаём view с кнопкой (отключенной если розыгрыш завершён)
            view = GiveawayView(self, giveaway_id)
            for item in view.children:
                if isinstance(item, discord.ui.Button) and item.custom_id == "giveaway_participate":
                    item.custom_id = f"giveaway_participate:{giveaway_id}"
                    if show_winners:
                        # Отключаем кнопку для завершённого розыгрыша
                        item.disabled = True
                        item.label = "🏆 Розыгрыш завершён"
                        item.style = discord.ButtonStyle.secondary
            
            # Отправляем новое сообщение
            new_message = await channel.send(embed=embed, view=view)
            
            # Обновляем message_id в БД
            db = await self.get_db_module()
            await db.update_giveaway_message_id(giveaway_id, new_message.id)
            
            # Обновляем кэш
            if giveaway_id in _active_giveaways:
                _active_giveaways[giveaway_id]['message_id'] = new_message.id
            giveaway['message_id'] = new_message.id
            
            logger.info(f"✅ Розыгрыш #{giveaway_id} пересоздан в канале {channel.id} (новое сообщение: {new_message.id})")
            
        except Exception as e:
            logger.error(f"Ошибка при пересоздании сообщения розыгрыша #{giveaway_id}: {e}", exc_info=True)

# ==================== ОБРАБОТКА УЧАСТИЯ ====================

async def handle_participation_method(self, interaction: discord.Interaction, giveaway_id: int):
    """Обработка нажатия кнопки участия с defer() и followup для предотвращения истечения времени."""
    # Получаем или создаём блокировку для этого розыгрыша
    if giveaway_id not in _giveaway_locks:
        _giveaway_locks[giveaway_id] = asyncio.Lock()
    
    async with _giveaway_locks[giveaway_id]:
        try:
            # Ранняя проверка - если interaction уже завершён, сразу возвращаемся
            if interaction.response.is_done():
                logger.warning(f"Interaction уже завершён для розыгрыша #{giveaway_id}")
                return
            
            # Откладываем ответ сразу после проверки - это даёт до 15 минут на обработку
            await interaction.response.defer(ephemeral=True)
            
            db = await self.get_db_module()
            giveaway = await retry_api_call(db.get_giveaway_by_id, giveaway_id)
            
            if not giveaway:
                await interaction.followup.send("❌ Этот розыгрыш не найден!", ephemeral=True)
                return
            
            if giveaway['status'] != 'active':
                await interaction.followup.send("❌ Этот розыгрыш уже завершён!", ephemeral=True)
                return
            
            # Проверка времени окончания
            if datetime.now(timezone.utc).replace(tzinfo=None) > giveaway['end_time']:  # offset-naive для совместимости
                await interaction.followup.send("⏰ Время этого розыгрыша истекло!", ephemeral=True)
                return
            
            user_id = interaction.user.id
            
            # Debounce защита от спама кликов
            if giveaway_id in _giveaway_debounce:
                if user_id in _giveaway_debounce[giveaway_id]:
                    await interaction.followup.send("⏳ Пожалуйста, подождите немного перед следующим кликом!", ephemeral=True)
                    return
            else:
                _giveaway_debounce[giveaway_id] = set()
            
            # Добавляем в debounce
            _giveaway_debounce[giveaway_id].add(user_id)
            
            # Удаляем из debounce через 2 секунды
            asyncio.create_task(self.clear_debounce(giveaway_id, user_id))
            
            # Проверяем, участвует ли уже (повторная проверка после получения блокировки)
            participants_list = parse_participants(giveaway.get('participants'))
            
            if user_id in participants_list:
                await interaction.followup.send("✅ Вы уже участвуете в этом розыгрыше!", ephemeral=True)
                return
            
            # Валидация требований
            validation_result = await self.validate_requirements(interaction.user, giveaway['requirements'], interaction.guild)
            
            if not validation_result['valid']:
                await interaction.followup.send(f"❌ {validation_result['reason']}", ephemeral=True)
                return
            
            # Добавляем участника
            success = await db.add_giveaway_participant(giveaway_id, user_id)
            
            if success:
                # Обновляем кэш с защитой от переполнения
                if giveaway_id in _active_giveaways:
                    if 'participants' not in _active_giveaways[giveaway_id]:
                        _active_giveaways[giveaway_id]['participants'] = []
                    _active_giveaways[giveaway_id]['participants'].append(user_id)
                
                # Очистка старого кэша если нужно
                cleanup_old_cache()
                
                # Загружаем актуальные данные розыгрыша из БД для обновления embed
                updated_giveaway = await retry_api_call(db.get_giveaway_by_id, giveaway_id)
                if updated_giveaway:
                    giveaway = updated_giveaway
                
                # Обновляем счётчик в embed с retry
                await self.update_giveaway_embed(giveaway_id, giveaway)
                
                await interaction.followup.send("✅ Вы успешно участвуете в розыгрыше! 🍀", ephemeral=True)
                logger.info(f"🏆 Участие в розыгрыше #{giveaway_id} | Пользователь: {interaction.user}")
            else:
                await interaction.followup.send("❌ Ошибка при добавлении вашего участия!", ephemeral=True)
        
        except discord.NotFound as e:
            # Interaction has expired or is invalid - cannot respond
            logger.warning(f"Interaction expired или недействительна для розыгрыша #{giveaway_id}: {e}")
            # Не пытаемся отправить сообщение, так как interaction уже недействителен
        except discord.HTTPException as e:
            logger.error(f"Discord API ошибка при обработке участия в розыгрыше #{giveaway_id}: {e}")
            try:
                # Проверяем, не является ли ошибкой "Unknown interaction"
                error_str = str(e)
                if "Unknown interaction" in error_str:
                    logger.warning(f"Unknown interaction для розыгрыша #{giveaway_id} - пропускаем отправку")
                    return
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Произошла ошибка при обработке вашего участия. Попробуйте позже.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Произошла ошибка при обработке вашего участия. Попробуйте позже.", ephemeral=True)
            except discord.NotFound:
                # Interaction expired while trying to send error message
                logger.warning(f"Не удалось отправить сообщение об ошибке - interaction истёк для розыгрыша #{giveaway_id}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при обработке участия в розыгрыше #{giveaway_id}: {e}", exc_info=True)
            try:
                # Проверяем, не является ли ошибкой "Unknown interaction"
                error_str = str(e)
                if "Unknown interaction" in error_str:
                    logger.warning(f"Unknown interaction для розыгрыша #{giveaway_id} - пропускаем отправку")
                    return
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Произошла непредвиденная ошибка. Попробуйте позже.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Произошла непредвиденная ошибка. Попробуйте позже.", ephemeral=True)
            except discord.NotFound:
                # Interaction expired while trying to send error message
                logger.warning(f"Не удалось отправить сообщение об ошибке - interaction истёк для розыгрыша #{giveaway_id}")

async def clear_debounce_method(self, giveaway_id: int, user_id: int):
    """Очистка debounce записи через 2 секунды."""
    await asyncio.sleep(2)
    if giveaway_id in _giveaway_debounce:
        _giveaway_debounce[giveaway_id].discard(user_id)
        if not _giveaway_debounce[giveaway_id]:
            del _giveaway_debounce[giveaway_id]

async def validate_requirements_method(self, user: discord.Member, requirements: dict, guild: discord.Guild) -> dict:
    """Валидация требований пользователя для участия."""
    # Проверка роли
    if requirements.get('role_id'):
        role = guild.get_role(requirements['role_id'])
        if not role or role not in user.roles:
            role_name = role.name if role and hasattr(role, 'name') else 'не найдена'
            return {'valid': False, 'reason': f"Для участия требуется роль {role_name}!"}
    
    # Проверка подписки (наличие любой платной роли - упрощённо проверяем наличие ролей кроме @everyone)
    if requirements.get('require_subscription'):
        paid_roles = [r for r in user.roles if r.id != guild.id]
        if not paid_roles:
            return {'valid': False, 'reason': "Для участия требуется подписка (наличие ролей)!"}
    
    # Проверка верификации (наличие любой роли кроме @everyone)
    if requirements.get('require_verification'):
        verified_roles = [r for r in user.roles if r.id != guild.id]
        if not verified_roles:
            return {'valid': False, 'reason': "Для участия требуется верификация (получите роль на сервере)!"}
    
    return {'valid': True, 'reason': ''}


Giveaway.handle_participation = handle_participation_method
Giveaway.clear_debounce = clear_debounce_method
Giveaway.validate_requirements = validate_requirements_method

# ==================== АВТОМАТИЧЕСКОЕ ЗАВЕРШЕНИЕ ====================
async def giveaway_loop_method(self):
    """Цикл проверки и авто-завершения розыгрышей с очисткой кэша."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)  # offset-naive для совместимости с БД
    
    # Загружаем активные розыгрыши из БД
    db = await self.get_db_module()
    
    for giveaway_id in list(_active_giveaways.keys()):
        giveaway_data = _active_giveaways[giveaway_id]
        
        # Проверка на None перед сравнением
        if giveaway_data.get('end_time') is None:
            logger.warning(f"Розыгрыш #{giveaway_id} имеет None end_time, пропускаем")
            continue
        
        if giveaway_data['end_time'] <= now:
            # Время истекло, завершаем
            try:
                giveaway = await retry_api_call(db.get_giveaway_by_id, giveaway_id)
                if giveaway and giveaway['status'] == 'active':
                    await self.finalize_giveaway(giveaway_id, giveaway)
            except Exception as e:
                logger.error(f"Ошибка при финализации розыгрыша #{giveaway_id} в цикле: {e}", exc_info=True)
        else:
            # Розыгрыш активен - обновляем таймер в embed
            try:
                giveaway = await retry_api_call(db.get_giveaway_by_id, giveaway_id)
                if giveaway and giveaway['status'] == 'active':
                    await self.update_giveaway_embed(giveaway_id, giveaway, show_winners=False)
            except Exception as e:
                logger.error(f"Ошибка при обновлении таймера розыгрыша #{giveaway_id}: {e}", exc_info=True)
    
    # Также проверяем все активные розыгрыши в БД (на случай перезапуска бота)
    try:
        all_active = await retry_api_call(db.get_all_active_giveaways)
        for giveaway in all_active:
            if giveaway['id'] not in _active_giveaways:
                # Добавляем в кэш
                _active_giveaways[giveaway['id']] = {
                    'id': giveaway['id'],
                    'message_id': giveaway['message_id'],
                    'end_time': giveaway['end_time'],
                    'status': giveaway['status']
                }
            
            # Если время истекло и ещё не обработано
            if giveaway.get('end_time') and giveaway['end_time'] <= now and giveaway['status'] == 'active':
                await self.finalize_giveaway(giveaway['id'], giveaway)
            elif giveaway['status'] == 'active':
                # Обновляем таймер для активных розыгрышей
                await self.update_giveaway_embed(giveaway['id'], giveaway, show_winners=False)
    except Exception as e:
        logger.error(f"Ошибка при загрузке активных розыгрышей из БД: {e}", exc_info=True)
    
    # Очистка старого кэша периодически
    cleanup_old_cache()


async def before_giveaway_loop_method(self):
    """Загрузка активных розыгрышей при старте."""
    await self.bot.wait_until_ready()
    db = await self.get_db_module()
    
    try:
        # Загружаем все активные розыгрыши
        active_giveaways = await retry_api_call(db.get_all_active_giveaways)
        
        for gw in active_giveaways:
            _active_giveaways[gw['id']] = {
                'id': gw['id'],
                'message_id': gw['message_id'],
                'end_time': gw['end_time'],
                'status': gw['status']
            }
        
        logger.info(f"🏆 Загружено {len(_active_giveaways)} активных розыгрышей")
    except Exception as e:
        logger.error(f"Ошибка при загрузке активных розыгрышей: {e}", exc_info=True)

Giveaway.giveaway_loop = tasks.loop(seconds=30)(giveaway_loop_method)
Giveaway.giveaway_loop.before_loop(before_giveaway_loop_method)


async def finalize_giveaway_method(self, giveaway_id: int, giveaway: dict):
    """Финализация розыгрыша: выбор победителей и объявление."""
    # Получаем или создаём блокировку для этого розыгрыша
    if giveaway_id not in _giveaway_locks:
        _giveaway_locks[giveaway_id] = asyncio.Lock()
    
    async with _giveaway_locks[giveaway_id]:
        db = await self.get_db_module()
        
        participants = parse_participants(giveaway.get('participants'))
        winners_count = min(giveaway['winners_count'], len(participants))
        
        # Выбор победителей
        if participants and winners_count > 0:
            winners = random.sample(participants, winners_count)
        else:
            winners = []
        
        # Обновляем в БД
        await db.update_giveaway_winners(giveaway_id, winners)
        await db.update_giveaway_status(giveaway_id, 'completed')
        
        # Удаляем из кэша
        _active_giveaways.pop(giveaway_id, None)
        
        # Обновляем embed
        giveaway['winners'] = winners
        giveaway['status'] = 'completed'
        await self.update_giveaway_embed(giveaway_id, giveaway, show_winners=True)
        
        
        # Лог
        guild = self.bot.get_guild(giveaway['guild_id'])
        if guild:
            log_channel = await self.get_log_channel(guild)
            if log_channel:
                embed = discord.Embed(
                    title="🏆 Розыгрыш завершён",
                    description=f"Розыгрыш #{giveaway_id}",
                    color=discord.Color.green()
                )
                embed.add_field(name="Приз", value=giveaway['prize'], inline=False)
                embed.add_field(name="Победители", value=", ".join([f"<@{w}>" for w in winners]) if winners else "Нет победителей", inline=False)
                embed.add_field(name="Участников", value=len(participants), inline=True)
                await log_channel.send(embed=embed)
        
        logger.info(f"🎉 Завершён розыгрыш #{giveaway_id} | Победители: {winners}")

Giveaway.finalize_giveaway = finalize_giveaway_method

def format_time_left_method(delta: timedelta) -> str:
    """Форматирование оставшегося времени (только часы и минуты)."""
    total_seconds = int(delta.total_seconds())
    
    if total_seconds < 0:
        return "0 мин."
    elif total_seconds < 60:
        return "1 мин."
    elif total_seconds < 3600:
        minutes = total_seconds // 60
        return f"{minutes} мин."
    else:
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if minutes == 0:
            return f"{hours} ч."
        return f"{hours} ч. {minutes} мин."


def create_giveaway_embed_method(self, giveaway: dict, giveaway_id: int) -> discord.Embed:
    """Создание embed для розыгрыша."""
    time_left = giveaway['end_time'] - datetime.now(timezone.utc).replace(tzinfo=None)  # offset-naive для совместимости
    
    embed = discord.Embed(
        title="🏆 НОВЫЙ РОЗЫГРЫШ!",
        description=f"**Приз:** {giveaway['prize']}",
        color=discord.Color.gold()
    )
    
    embed.add_field(name="🏆 Победителей", value=str(giveaway['winners_count']), inline=True)
    embed.add_field(name="⏰ Длительность", value=self.format_time_left(time_left), inline=True)
    embed.add_field(name="👥 Участников", value="0", inline=True)
    
    # Требования
    requirements_text = []
    reqs = giveaway.get('requirements', {})
    if reqs.get('role_id'):
        guild = self.bot.get_guild(giveaway['guild_id'])
        if guild:
            role = guild.get_role(reqs['role_id'])
            if role:
                requirements_text.append(f"• Роль: {role.name}")
    if reqs.get('require_subscription'):
        requirements_text.append("• Требуется подписка")
    if reqs.get('require_verification'):
        requirements_text.append("• Требуется верификация")
    
    if requirements_text:
        embed.add_field(name="📋 Требования", value="\n".join(requirements_text), inline=False)
    
    embed.set_footer(text=f"ID розыгрыша: {giveaway_id} | Создатель: <@{giveaway['creator_id']}>")
    embed.timestamp = giveaway['start_time']
    
    return embed

async def update_giveaway_embed_method(self, giveaway_id: int, giveaway: dict, show_winners: bool = False):
    """Обновление embed розыгрыша с retry-логикой и проверкой прав."""
    try:
        channel = self.bot.get_channel(giveaway['channel_id'])
        if not channel:
            logger.warning(f"Канал {giveaway['channel_id']} не найден для розыгрыша #{giveaway_id}")
            return
        
        # Проверка прав бота перед попыткой редактирования
        guild = channel.guild
        bot_member = guild.get_member(self.bot.user.id) if guild else None
        if not bot_member:
            logger.warning(f"Не удалось получить участника бота для сервера {guild.id if guild else 'N/A'}")
            return
        bot_perms = channel.permissions_for(bot_member)
        if not bot_perms.manage_messages:
            logger.warning(f"Нет прав на редактирование сообщений в канале {channel.id} для розыгрыша #{giveaway_id}")
        
        message = await retry_api_call(channel.fetch_message, giveaway['message_id'])
        
        participants_count = len(parse_participants(giveaway.get('participants')))
        
        if show_winners:
            # Завершённый розыгрыш
            winners = giveaway.get('winners', [])
            winner_mentions = [f"<@{w}>" for w in winners] if winners else ["Нет победителей"]
            
            embed = discord.Embed(
                title="🎉 РОЗЫГРЫШ ЗАВЕРШЁН! 🎉",
                description=f"**Приз:** {giveaway['prize']}",
                color=discord.Color.green() if winners else discord.Color.red()
            )
            embed.add_field(name="🏆 Победители", value=", ".join(winner_mentions), inline=False)
            embed.add_field(name="👥 Участников", value=participants_count, inline=True)
            embed.add_field(name="📊 Статус", value="Завершён", inline=True)
        else:
            # Активный розыгрыш
            time_left = giveaway['end_time'] - datetime.now(timezone.utc).replace(tzinfo=None)  # offset-naive для совместимости
            if time_left.total_seconds() <= 0:
                time_left_str = "Завершается..."
            else:
                time_left_str = self.format_time_left(time_left)
            
            embed = discord.Embed(
                title="🏆 РОЗЫГРЫШ",
                description=f"**Приз:** {giveaway['prize']}",
                color=discord.Color.gold()
            )
            embed.add_field(name="🏆 Победителей", value=str(giveaway['winners_count']), inline=True)
            embed.add_field(name="⏰ Осталось", value=time_left_str, inline=True)
            embed.add_field(name="👥 Участников", value=participants_count, inline=True)
        
        embed.set_footer(text=f"ID розыгрыша: {giveaway_id}")
        
        await retry_api_call(message.edit, embed=embed)
        logger.debug(f"Embed розыгрыша #{giveaway_id} успешно обновлён")
        
    except discord.NotFound:
        logger.warning(f"Сообщение розыгрыша #{giveaway_id} не найдено (возможно, удалено). Создаю новое сообщение...")
        # Сообщение удалено, создаём новое
        await self.recreate_giveaway_message(giveaway_id, giveaway, show_winners)
    except discord.Forbidden:
        logger.error(f"Нет прав на редактирование сообщения розыгрыша #{giveaway_id}")
    except Exception as e:
        logger.error(f"Ошибка при обновлении embed розыгрыша #{giveaway_id}: {e}", exc_info=True)

async def get_log_channel_method(self, guild: discord.Guild):
    """Получение канала для логов."""
    # Ищем канал с названием "giveaway-logs" или "log"
    for channel_name in ['giveaway-logs', 'logs', 'log', 'bot-logs']:
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel:
            return channel
    return None

Giveaway.update_giveaway_embed = update_giveaway_embed_method
Giveaway.create_giveaway_embed = create_giveaway_embed_method
Giveaway.get_log_channel = get_log_channel_method

async def setup(bot):
    """Функция загрузки кога."""
    cog = Giveaway(bot)
    await bot.add_cog(cog)
    bot.add_view(GiveawayPersistentView(cog))
    bot.add_view(GiveawayMainView(cog, None))
