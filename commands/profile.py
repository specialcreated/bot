import discord
from discord.ext import commands
from datetime import datetime
import logging
import asyncio
from typing import Optional, Dict
from database.mysql_connector import (
    get_user_profile,
    create_user_record,
    _update_balance,
    _sync_update_voice_time,
    increment_message_count
)

logger = logging.getLogger(__name__)

def format_time(seconds: int) -> str:
    """Форматирует секунды в строку вида '1ч 20м'."""
    if seconds == 0:
        return "0 мин"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    parts = []
    if hours > 0:
        parts.append(f"{hours} ч")
    if minutes > 0 or not parts:  # если нет часов, но есть минуты, или вообще 0
        parts.append(f"{minutes} м")
    return " ".join(parts)

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.voice_start_times = {}  # Храним время входа в канал
        self.voice_lock = asyncio.Lock()  # Защита словаря от гонок
        self.cooldowns: Dict[tuple, float] = {}  # Ключ: (user_id, guild_id), Значение: timestamp последнего обновления
    @commands.command(name='profile', aliases=['проф', 'профиль'])
    async def profile(self, ctx, member: discord.Member = None):
        if member is None:
            member = ctx.author
        if member.bot:
            await ctx.send("❌ Нельзя просмотреть профиль бота.")
            return

        try:
            # Получаем данные из БД
            user_data = await get_user_profile(member.id, ctx.guild.id)
            logger.debug(f"Полученные данные профиля: {user_data}")

            # Улучшенная проверка: учитываем None, пустой dict и отсутствие обязательных полей
            if not user_data or not isinstance(user_data, dict) or 'user_id' not in user_data:
                # Создаём запись, если её нет
                success = await create_user_record({
                    'user_id': member.id,
                    'discord_tag': str(member),
                    'created_at': member.created_at,
                    'joined_at': member.joined_at,
                    'guild_id': ctx.guild.id,
                    'voice_time_total': 0,
                    'message_count': 0,
                    'balance': 0,
                    'level': 1,
                    'xp': 0,
                    'next_level_xp': 100
                })
                if not success:
                    await ctx.send("❌ Не удалось создать профиль пользователя.")
                    return

                # Перезагружаем данные после создания записи
                user_data = await get_user_profile(member.id, ctx.guild.id)
                if not user_data or 'user_id' not in user_data:
                    await ctx.send("❌ Не удалось загрузить профиль пользователя после создания.")
                    logger.error(f"Профиль пользователя {member.id} не найден после создания записи")
                    return

            # Валидируем обязательные поля и проверяем на None
            for field in ['voice_time_total', 'message_count', 'balance', 'level', 'xp', 'next_level_xp']:
                if field not in user_data or user_data[field] is None:
                    logger.warning(f"Пропущено или NULL поле {field} в профиле пользователя {member.id}")
                    if field == 'level':
                        user_data[field] = 1
                    elif field in ['xp', 'next_level_xp']:
                        user_data[field] = 0
                    else:
                        user_data[field] = 0
            # Добавляем информацию об уровне.
            level = user_data.get('level', 1)
            xp = user_data.get('xp', 0)
            next_level_xp = user_data.get('next_level_xp', 100)
            # Форматируем баланс под нужный формат.
            balance = user_data.get('balance', 0)
            formatted_balance = "{:,}".format(balance).replace(",", ".")

            embed = discord.Embed(
                title=f"👤 Профиль {member.display_name}",
                color=discord.Color.blurple(),
                timestamp=datetime.utcnow()
            )

            embed.add_field(
                name="`Достижения`",
                value=(
                    f"🌟 | Уровень: **{level}**\n"
                    f"📈 | Прогресс: **{xp}/{next_level_xp}**\n"
                    f"💸 | Баланс: **{formatted_balance}**"
                ),
                inline=True
            )
            embed.add_field(
                name="`Активность`",
                value=(
                    f"🕒 | Время: **{format_time(user_data.get('voice_time_total', 0))}**\n"
                    f"💬 | Сообщения: **{user_data.get('message_count', 0)}**"
                ),
                inline=True
            )

            if member.avatar:
                embed.set_thumbnail(url=member.avatar.url)
            else:
                embed.set_thumbnail(url=member.default_avatar.url)

            embed.set_footer(text=f"📤 Запросил: {ctx.author.display_name}")
            embed.timestamp = datetime.utcnow()

            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Критическая ошибка при получении профиля пользователя {member.id}: {e}")
            await ctx.send("❌ Произошла критическая ошибка при загрузке профиля.")
    @commands.command(name='setbalance')
    @commands.is_owner()
    async def set_balance(self, ctx, member: discord.Member, balance: int):
        # Валидация баланса
        if balance < 0:
            await ctx.send("❌ Баланс не может быть отрицательным.")
            return

        loop = asyncio.get_event_loop()

        try:
            result = await loop.run_in_executor(
                None,
                _update_balance,
                balance,
                member.id,
                ctx.guild.id
            )
            if result == 0:
                await ctx.send(f"❌ Пользователь {member.mention} не найден в базе данных.")
                return

            await ctx.send(f"✅ Баланс {member.mention} установлен на {balance} монет.")
            logger.info(f"Баланс обновлён для {member.id} до {balance} в гильдии {ctx.guild.id}")
        except Exception as e:
            logger.error(f"Ошибка БД в set_balance: {e}")
            await ctx.send("❌ Ошибка при обновлении баланса в базе данных.")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:  # Игнорируем ботов
            return

        user_id = member.id
        guild_id = member.guild.id
        key = (user_id, guild_id)

        logger.debug(f"Voice state update: user={user_id}, guild={guild_id}, "
                    f"before={before.channel.id if before.channel else None}, "
                    f"after={after.channel.id if after.channel else None}")

        async with self.voice_lock:
            # Проверяем охлаждение перед обработкой
            if key in self.cooldowns:
                last_update = self.cooldowns[key]
                current_time = datetime.utcnow().timestamp()
                time_since_last = current_time - last_update

                if time_since_last < 0.5:  # 500 мс
                    logger.debug(f"Пропускаем обновление для {user_id} — охлаждение ({time_since_last*1000:.0f} мс)")
                    return

            # Пользователь вошёл в голосовой канал
            if not before.channel and after.channel:
                self.voice_start_times[key] = datetime.utcnow()
                logger.debug(f"Пользователь {user_id} вошёл в канал {after.channel.id} в {self.voice_start_times[key]}")

            # Пользователь вышел из голосового канала
            elif before.channel and not after.channel:
                if key in self.voice_start_times:
                    start_time = self.voice_start_times.pop(key)
                    duration = max(0, (datetime.utcnow() - start_time).total_seconds())

                    if duration >= 1:
                        await self.update_voice_time(user_id, guild_id, duration)
                        # Обновляем отметку охлаждения после обновления
                        self.cooldowns[key] = datetime.utcnow().timestamp()
                    else:
                        logger.debug(f"Короткое пребывание в канале ({duration:.2f}с), не учитываем.")
                else:
                    logger.warning(f"Нет записи о входе в канал для {user_id} ({guild_id}) — пропускаем обновление.")

            # Перемещение между каналами
            elif before.channel and after.channel and before.channel != after.channel:
                if key in self.voice_start_times:
                    start_time = self.voice_start_times[key]
                    duration = max(0, (datetime.utcnow() - start_time).total_seconds())

                    if duration >= 1:
                        await self.update_voice_time(user_id, guild_id, duration)
                        # Обновляем отметку охлаждения после обновления
                        self.cooldowns[key] = datetime.utcnow().timestamp()
                    else:
                        logger.debug(f"Короткое пребывание в канале ({duration:.2f}с) при перемещении, не учитываем.")
                else:
                    logger.warning(f"Нет записи о входе в канал для {user_id} ({guild_id}) при перемещении.")

                # Начинаем отсчёт времени для нового канала
                self.voice_start_times[key] = datetime.utcnow()
    async def update_voice_time(self, user_id: int, guild_id: int, duration: float):
        """
        Обновляет время пребывания в голосовых каналах для пользователя в БД.
        
        :param user_id: ID пользователя (int)
        :param guild_id: ID гильдии (сервера) (int)
        :param duration: Длительность пребывания в секундах (float)
        """
        try:
            # Получаем текущее время в UTC для логирования
            current_time = datetime.utcnow()
            
            # Формируем параметры для обновления БД
            update_data = {
                'user_id': user_id,
                'guild_id': guild_id,
                'duration': duration
            }
            
            # Выполняем обновление через executor (чтобы не блокировать event loop)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                _sync_update_voice_time,
                user_id,
                guild_id,
                duration
            )
            
            # Проверяем результат обновления
            if result is None:
                logger.warning(f"Обновление voice_time для {user_id} в гильдии {guild_id} вернуло None — возможная ошибка в БД")
                return
            
            if result == 0:
                # Пользователь не найден в БД — создаём запись
                await self._create_voice_record(user_id, guild_id, duration)
                logger.info(f"Создана новая запись для {user_id} в гильдии {guild_id} с voice_time={duration} сек.")
            else:
                logger.info(f"Обновлено voice_time_total для {user_id} в гильдии {guild_id} на {duration:.2f} сек. (общее время: {result} сек.)")
                
        except asyncio.CancelledError:
            logger.error(f"Обновление voice_time для {user_id} отменено (CancelledError)")
            raise
        except Exception as e:
            logger.error(f"Критическая ошибка при обновлении voice_time_total для {user_id} в гильдии {guild_id}: {e}")
            # Дополнительно логируем параметры для отладки
            logger.debug(f"Параметры обновления: user_id={user_id}, guild_id={guild_id}, duration={duration}")

    async def _create_voice_record(self, user_id: int, guild_id: int, initial_duration: float):
        """
        Создаёт начальную запись пользователя в БД, если её нет.
        Вызывается при первом подключении или если пользователь не найден.
        """
        try:
            creation_data = {
                'user_id': user_id,
                'discord_tag': str(await self.bot.fetch_user(user_id)),
                'created_at': datetime.utcnow(),
                'joined_at': datetime.utcnow(),
                'guild_id': guild_id,
                'voice_time_total': initial_duration,
                'message_count': 0,
                'balance': 0
            }
            
            success = await create_user_record(creation_data)
            if not success:
                logger.error(f"Не удалось создать запись для {user_id} в гильдии {guild_id}")
            else:
                logger.info(f"Запись создана для {user_id} в гильдии {guild_id} с начальным voice_time={initial_duration} сек.")
                
        except Exception as e:
            logger.error(f"Ошибка при создании начальной записи для {user_id}: {e}")


    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Игнорируем:
        # - ботов
        # - системные сообщения
        # - сообщения вне гильдий
        # - команды
        if (
            message.author.bot
            or not message.guild
            or message.type != discord.MessageType.default
            or message.content.startswith(self.bot.command_prefix)
        ):
            return

        try:
            # Увеличиваем счётчик сообщений
            success = await increment_message_count(message.author.id, message.guild.id)
            if not success:
                logger.warning(f"Не удалось обновить message_count для пользователя {message.author.id}")
                    
        except Exception as e:
            logger.error(f"Критическая ошибка при учёте сообщения от {message.author.id}: {e}")
  
    def cog_unload(self):
        self.voice_start_times.clear()
        self.cooldowns.clear()  # Очищаем словарь охлаждения
        logger.info("Выгрузка кога 'Profile', очистка временных данных...")

# profile.py
async def setup(bot):
    await bot.add_cog(Profile(bot))