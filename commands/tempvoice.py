import discord
import logging
from discord.ext import commands
from discord.ui import View, Modal, TextInput, Button
from database.mysql_connector import get_db_connection

logger = logging.getLogger(__name__)


class SlotModal(Modal, title="Изменить количество слотов"):
    def __init__(self, channel, cog):
        super().__init__()
        self.channel = channel
        self.cog = cog
        current_limit = channel.user_limit if channel.user_limit > 0 else 99
        self.add_item(TextInput(
            label="Количество слотов",
            placeholder=f"Введите число от 2 до 99 (сейчас: {current_limit})",
            min_length=1,
            max_length=2,
            default=str(current_limit)
        ))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            slot_count = int(self.children[0].value.strip())
            if slot_count < 2 or slot_count > 99:
                embed = discord.Embed(
                    title="❌ Неверное значение",
                    description="Количество слотов должно быть от 2 до 99.",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            await self.channel.edit(user_limit=slot_count)
            
            # Обновляем метку кнопки в представлении
            view = self.channel.view
            if view and hasattr(view, 'slot_btn'):
                view.slot_btn.label = f"➕ Слоты: {slot_count}"
            
            embed = discord.Embed(
                title="✅ Лимит изменён",
                description=f"Максимум участников: `{slot_count}`",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except ValueError:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Введите корректное число от 2 до 99.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class RenameModal(Modal, title="Переименовать канал"):
    def __init__(self, channel, cog):
        super().__init__()
        self.channel = channel
        self.cog = cog
        self.add_item(TextInput(label="Новое название", default=channel.name, max_length=100))

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.children[0].value
        await self.channel.edit(name=new_name)
        # Обновляем в БД
        connection = get_db_connection()
        if connection:
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "UPDATE voice_channels SET channel_name = %s WHERE channel_id = %s",
                    (new_name, self.channel.id)
                )
                connection.commit()
            except Exception as e:
                logger.error(f"Ошибка обновления названия канала в БД: {e}")
            finally:
                connection.close()
        
        embed = discord.Embed(
            title="✅ Канал переименован",
            description=f"Новое название: `{new_name}`",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AddUserModal(Modal, title="Добавить пользователя"):
    def __init__(self, channel, cog):
        super().__init__()
        self.channel = channel
        self.cog = cog
        self.add_item(TextInput(label="ID пользователя или упоминание", placeholder="123456789 или @user", max_length=100))

    async def on_submit(self, interaction: discord.Interaction):
        user_input = self.children[0].value.strip()
        member = None
        
        # Пробуем найти по упоминанию
        if user_input.startswith("<@") and user_input.endswith(">"):
            user_id = int(user_input.strip("<@!>"))
            member = self.channel.guild.get_member(user_id)
        else:
            # Пробуем по ID
            try:
                user_id = int(user_input)
                member = self.channel.guild.get_member(user_id)
            except ValueError:
                pass
        
        if not member:
            embed = discord.Embed(
                title="❌ Пользователь не найден",
                description="Не удалось найти участника с таким ID или упоминанием.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Добавляем разрешение на просмотр канала
        overwrites = self.channel.overwrites_for(member)
        overwrites.view_channel = True
        overwrites.connect = True
        await self.channel.set_permissions(member, overwrite=overwrites)
        
        embed = discord.Embed(
            title="✅ Пользователь добавлен",
            description=f"{member.mention} теперь может зайти в канал.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RemoveUserModal(Modal, title="Убрать пользователя"):
    def __init__(self, channel, cog):
        super().__init__()
        self.channel = channel
        self.cog = cog
        self.add_item(TextInput(label="ID пользователя или упоминание", placeholder="123456789 или @user", max_length=100))

    async def on_submit(self, interaction: discord.Interaction):
        user_input = self.children[0].value.strip()
        member = None
        
        if user_input.startswith("<@") and user_input.endswith(">"):
            user_id = int(user_input.strip("<@!>"))
            member = self.channel.guild.get_member(user_id)
        else:
            try:
                user_id = int(user_input)
                member = self.channel.guild.get_member(user_id)
            except ValueError:
                pass
        
        if not member:
            embed = discord.Embed(
                title="❌ Пользователь не найден",
                description="Не удалось найти участника с таким ID или упоминанием.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Убираем разрешение
        await self.channel.set_permissions(member, overwrite=None)
        
        # Если пользователь в канале - выгоняем
        if member in self.channel.members:
            try:
                await member.move_to(None)
            except:
                pass
        
        embed = discord.Embed(
            title="✅ Пользователь убран",
            description=f"{member.mention} больше не имеет доступа к каналу.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class TransferModal(Modal, title="Передать канал"):
    def __init__(self, channel, cog):
        super().__init__()
        self.channel = channel
        self.cog = cog
        self.add_item(TextInput(label="ID нового владельца или упоминание", placeholder="123456789 или @user", max_length=100))

    async def on_submit(self, interaction: discord.Interaction):
        user_input = self.children[0].value.strip()
        member = None
        
        if user_input.startswith("<@") and user_input.endswith(">"):
            user_id = int(user_input.strip("<@!>"))
            member = self.channel.guild.get_member(user_id)
        else:
            try:
                user_id = int(user_input)
                member = self.channel.guild.get_member(user_id)
            except ValueError:
                pass
        
        if not member:
            embed = discord.Embed(
                title="❌ Пользователь не найден",
                description="Не удалось найти участника с таким ID или упоминанием.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Обновляем владельца в БД
        connection = get_db_connection()
        if connection:
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "UPDATE voice_channels SET owner_id = %s WHERE channel_id = %s",
                    (member.id, self.channel.id)
                )
                connection.commit()
            except Exception as e:
                logger.error(f"Ошибка передачи владельца в БД: {e}")
            finally:
                connection.close()
        
        # Обновляем права
        old_owner = self.cog.created_channels.get(self.channel.id, {}).get("owner")
        if old_owner:
            overwrites = self.channel.overwrites_for(old_owner)
            overwrites.manage_channels = False
            await self.channel.set_permissions(old_owner, overwrite=overwrites)
        
        overwrites = self.channel.overwrites_for(member)
        overwrites.manage_channels = True
        await self.channel.set_permissions(member, overwrite=overwrites)
        
        # Обновляем кэш
        if self.channel.id in self.cog.created_channels:
            self.cog.created_channels[self.channel.id]["owner"] = member
        
        embed = discord.Embed(
            title="✅ Владелец передан",
            description=f"Новый владелец канала: {member.mention}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BlockUserModal(Modal, title="Заблокировать пользователя"):
    def __init__(self, channel, cog):
        super().__init__()
        self.channel = channel
        self.cog = cog
        self.add_item(TextInput(label="ID пользователя или упоминание", placeholder="123456789 или @user", max_length=100))

    async def on_submit(self, interaction: discord.Interaction):
        user_input = self.children[0].value.strip()
        member = None
        
        if user_input.startswith("<@") and user_input.endswith(">"):
            user_id = int(user_input.strip("<@!>"))
            member = self.channel.guild.get_member(user_id)
        else:
            try:
                user_id = int(user_input)
                member = self.channel.guild.get_member(user_id)
            except ValueError:
                pass
        
        if not member:
            embed = discord.Embed(
                title="❌ Пользователь не найден",
                description="Не удалось найти участника с таким ID или упоминанием.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Блокируем доступ
        overwrites = self.channel.overwrites_for(member)
        overwrites.view_channel = False
        overwrites.connect = False
        await self.channel.set_permissions(member, overwrite=overwrites)
        
        # Если пользователь в канале - выгоняем
        if member in self.channel.members:
            try:
                await member.move_to(None)
            except:
                pass
        
        embed = discord.Embed(
            title="✅ Пользователь заблокирован",
            description=f"{member.mention} заблокирован в этом канале.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ChannelControlView(View):
    def __init__(self, channel, owner, cog):
        super().__init__(timeout=None)
        self.channel = channel
        self.owner = owner
        self.cog = cog
        self.is_private = False
        self.is_hidden = False
        
        # Кнопка: Добавить/Убрать слот
        self.slot_btn = Button(label="➕", style=discord.ButtonStyle.primary)
        self.slot_btn.callback = self.toggle_slot
        self.add_item(self.slot_btn)
        
        # Кнопка: Открыть/Закрыть канал
        self.lock_btn = Button(label="🔓", style=discord.ButtonStyle.secondary)
        self.lock_btn.callback = self.toggle_lock
        self.add_item(self.lock_btn)
        # Кнопка: Добавить пользователя
        self.add_user_btn = Button(
            label="",
            style=discord.ButtonStyle.success,
            emoji=discord.PartialEmoji.from_str("<:pluspolz:1507549400073769060>")
        )
        self.add_user_btn.callback = self.add_user
        self.add_item(self.add_user_btn)

        # Кнопка: Убрать пользователя
        self.remove_user_btn = Button(
            label="",
            style=discord.ButtonStyle.danger,
            emoji=discord.PartialEmoji.from_str("<:minuspolz:1507551823328706600>")
        )
        self.remove_user_btn.callback = self.remove_user
        self.add_item(self.remove_user_btn)
        
        # Кнопка: Скрыть/Показать
        self.hide_btn = Button(label="👁️", style=discord.ButtonStyle.secondary)
        self.hide_btn.callback = self.toggle_visibility
        self.add_item(self.hide_btn)
        
        # Кнопка: Передать канал
        self.transfer_btn = Button(label="🔄", style=discord.ButtonStyle.primary)
        self.transfer_btn.callback = self.transfer_ownership
        self.add_item(self.transfer_btn)
        
        # Кнопка: Переименовать
        self.rename_btn = Button(label="✏️", style=discord.ButtonStyle.secondary)
        self.rename_btn.callback = self.rename_channel
        self.add_item(self.rename_btn)
        
        # Кнопка: Заблокировать
        self.block_btn = Button(label="🚫", style=discord.ButtonStyle.danger)
        self.block_btn.callback = self.block_user
        self.add_item(self.block_btn)

    async def check_owner(self, interaction: discord.Interaction):
        """Проверяет, является ли пользователь владельцем."""
        if interaction.user != self.owner:
            embed = discord.Embed(
                title="🚫 Доступ запрещён",
                description="Только создатель канала может управлять им.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    async def toggle_slot(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        
        # Открываем модальное окно для ввода количества слотов
        await interaction.response.send_modal(SlotModal(self.channel, self.cog))

    async def toggle_lock(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        
        self.is_private = not self.is_private
        
        if self.is_private:
            # Закрываем канал для всех кроме владельца и текущих участников
            overwrites = discord.PermissionOverwrite()
            overwrites.view_channel = False
            overwrites.connect = False
            await self.channel.set_permissions(self.channel.guild.default_role, overwrite=overwrites)
            self.lock_btn.label = "🔒 Закрыт"
            self.lock_btn.style = discord.ButtonStyle.danger
        else:
            # Открываем для всех
            overwrites = discord.PermissionOverwrite()
            overwrites.view_channel = True
            overwrites.connect = True
            await self.channel.set_permissions(self.channel.guild.default_role, overwrite=overwrites)
            self.lock_btn.label = "🔓 Открыт"
            self.lock_btn.style = discord.ButtonStyle.secondary
        
        status = "закрыт" if self.is_private else "открыт"
        embed = discord.Embed(
            title="✅ Статус изменён",
            description=f"Канал теперь **{status}**.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def add_user(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        await interaction.response.send_modal(AddUserModal(self.channel, self.cog))

    async def remove_user(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        await interaction.response.send_modal(RemoveUserModal(self.channel, self.cog))

    async def toggle_visibility(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        
        self.is_hidden = not self.is_hidden
        
        if self.is_hidden:
            # Скрываем канал для всех кроме владельца и участников
            overwrites = discord.PermissionOverwrite()
            overwrites.view_channel = False
            await self.channel.set_permissions(self.channel.guild.default_role, overwrite=overwrites)
            self.hide_btn.label = "👁️ Скрыт"
            self.hide_btn.style = discord.ButtonStyle.danger
        else:
            # Показываем всем
            overwrites = discord.PermissionOverwrite()
            overwrites.view_channel = True
            await self.channel.set_permissions(self.channel.guild.default_role, overwrite=overwrites)
            self.hide_btn.label = "👁️ Показать"
            self.hide_btn.style = discord.ButtonStyle.secondary
        
        status = "скрыт" if self.is_hidden else "показан"
        embed = discord.Embed(
            title="✅ Видимость изменена",
            description=f"Канал теперь **{status}** в списке каналов.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def transfer_ownership(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        await interaction.response.send_modal(TransferModal(self.channel, self.cog))

    async def rename_channel(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        await interaction.response.send_modal(RenameModal(self.channel, self.cog))

    async def block_user(self, interaction: discord.Interaction):
        if not await self.check_owner(interaction):
            return
        await interaction.response.send_modal(BlockUserModal(self.channel, self.cog))

class VoiceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.created_channels = {}
        self.emojis = ['🦉', '🎤', '🗣️', '💬', '🔊']

    async def cog_load(self):
        """Загружает данные о каналах из БД при старте бота."""
        logger.info("Загрузка данных о временных голосовых каналах из БД...")
        connection = get_db_connection()
        if not connection:
            logger.warning("Не удалось подключиться к БД при загрузке каналов.")
            return

        try:
            cursor = connection.cursor()
            cursor.execute("SELECT channel_id, owner_id FROM voice_channels")
            rows = cursor.fetchall()
            
            for channel_id, owner_id in rows:
                channel = self.bot.get_channel(channel_id)
                if channel:
                    # Проверяем, пуст ли канал
                    if len(channel.members) == 0:
                        logger.info(f"Удаление пустого канала {channel_id} при загрузке")
                        await self._safe_delete_voice_channel(channel_id)
                    else:
                        # Восстанавливаем владельца
                        guild = channel.guild
                        owner = guild.get_member(owner_id)
                        if owner:
                            self.created_channels[channel_id] = {"channel_id": channel_id, "owner": owner}
                            # Восстанавливаем права владельца
                            overwrites = channel.overwrites_for(owner)
                            overwrites.manage_channels = True
                            await channel.set_permissions(owner, overwrite=overwrites)
                            logger.info(f"Восстановлен канал {channel_id} с владельцем {owner.id}")
                        else:
                            logger.warning(f"Владелец {owner_id} не найден для канала {channel_id}, удаляем канал")
                            await self._safe_delete_voice_channel(channel_id)
                else:
                    logger.warning(f"Канал {channel_id} не найден в Discord, удаляем из БД")
                    await self._safe_delete_voice_channel(channel_id)
            
            logger.info(f"Загружено {len(self.created_channels)} активных временных каналов")
        except Exception as e:
            logger.error(f"Ошибка при загрузке каналов из БД: {e}")
        finally:
            connection.close()

    async def _safe_delete_voice_channel(self, channel_id: int):
        """Безопасно удаляет голосовой канал и очищает кэш."""
        channel = self.bot.get_channel(channel_id)

        # Удаляем из БД независимо от существования канала
        connection = get_db_connection()
        if connection:
            try:
                cursor = connection.cursor()
                cursor.execute(
                    "DELETE FROM voice_channels WHERE channel_id = %s",
            (channel_id,)
        )
                connection.commit()
            except Exception as e:
                logger.error(f"Ошибка удаления канала {channel_id} из БД: {e}")
            finally:
                connection.close()

        if not channel:
            logger.warning(f"Канал {channel_id} не найден в кэше. Очищаем кэш.")
            if channel_id in self.created_channels:
                del self.created_channels[channel_id]
            return False

        try:
            await channel.delete()
            logger.info(f"Канал {channel_id} удалён через Discord API")
            if channel_id in self.created_channels:
                del self.created_channels[channel_id]
            return True
        except discord.NotFound:
            logger.warning(f"Канал {channel_id} уже удалён (404 Not Found). Очищаем кэш.")
            if channel_id in self.created_channels:
                del self.created_channels[channel_id]
            return False
        except discord.Forbidden:
            logger.error(f"Нет прав на удаление канала {channel_id}")
            return False
        except discord.HTTPException as e:
            logger.error(f"Ошибка API при удалении канала {channel_id}: {e}")
            return False

    def generate_channel_name(self, guild_id):
        """Генерирует имя канала со смайликом и номером в верхнем индексе для конкретного сервера."""
        connection = get_db_connection()
        if not connection:
            logger.warning("Не удалось подключиться к БД при генерации имени канала. Используем имя по умолчанию.")
            return "Голосовой ¹"

        # Словарь для преобразования цифр в верхний индекс
        superscript_map = str.maketrans({
            '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
            '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹'
        })

        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM voice_channels WHERE guild_id = %s",
                (guild_id,)
            )
            count = cursor.fetchone()[0]
            connection.close()

            # Определяем эмодзи по остатку от деления на длину списка
            emoji = self.emojis[count % len(self.emojis)]

            # Преобразуем номер в верхний индекс
            channel_number = str(count + 1).translate(superscript_map)

            channel_name = f"{emoji} | Голосовой {channel_number}"
            logger.debug(f"Сгенерировано имя канала: {channel_name} для сервера {guild_id}")
            return channel_name
        except Exception as e:
            logger.error(f"Ошибка при генерации имени канала для сервера {guild_id}: {e}")
            return "Голосовой ¹"

    async def get_notification_channel(self, guild_id):
        """Получает канал для уведомлений для конкретного сервера."""
        connection = get_db_connection()
        if not connection:
            logger.warning("Не удалось подключиться к БД при получении канала уведомлений.")
            return None

        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT notification_channel_id FROM server_settings WHERE guild_id = %s",
                (guild_id,)
            )
            result = cursor.fetchone()
            connection.close()

            if result and result[0]:
                channel = self.bot.get_channel(result[0])
                if channel:
                    logger.debug(f"Получен канал уведомлений {result[0]} для сервера {guild_id}")
                    return channel
                else:
                    logger.warning(f"Канал уведомлений {result[0]} не найден в Discord.")
                    return None
            else:
                logger.debug(f"Для сервера {guild_id} канал уведомлений не настроен.")
                return None
        except Exception as e:
            logger.error(f"Ошибка получения канала уведомлений для сервера {guild_id}: {e}")
            return None

    async def save_channel_to_db(self, channel_id, guild_id, owner_id, channel_name):
        """Сохраняет информацию о канале в базу данных."""
        connection = get_db_connection()
        if not connection:
            logger.warning("Не удалось подключиться к БД при сохранении канала.")
            return

        try:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO voice_channels (channel_id, guild_id, owner_id, channel_name) VALUES (%s, %s, %s, %s)",
                (channel_id, guild_id, owner_id, channel_name)
            )
            connection.commit()
            logger.info(f"Канал {channel_id} сохранён в БД: сервер {guild_id}, владелец {owner_id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения канала {channel_id} в БД: {e}")
        finally:
            connection.close()
    async def send_notification(self, guild_id, channel, owner):
        """Отправляет уведомление о создании канала."""
        notification_channel = await self.get_notification_channel(guild_id)

        # Проверка на существование канала уведомлений
        if not notification_channel:
            logger.warning(
                f"Не удалось отправить уведомление: канал уведомлений не найден для сервера {guild_id}"
            )
            return

        # Проверка существования канала, о котором отправляем уведомление
        if not channel:
            logger.error(f"Попытка отправить уведомление о несуществующем канале для сервера {guild_id}")
            return

        embed = discord.Embed(
            title="🔔 Создан новый голосовой канал!",
            color=discord.Color.blue()
        )
        embed.add_field(name="Канал", value=channel.mention, inline=False)
        embed.add_field(name="Имя канала", value=f"`{channel.name}`", inline=False)
        embed.add_field(name="Владелец", value=owner.mention, inline=True)
        embed.set_footer(text=f"Канал: {channel.name}")

        try:
            await notification_channel.send(embed=embed)
        except discord.Forbidden:
            logger.error(
                f"Нет прав на отправку сообщений в канал уведомлений {notification_channel.id} "
                f"сервера {guild_id}"
            )
        except discord.HTTPException as e:
            logger.error(
                f"Ошибка API Discord при отправке уведомления о канале {channel.id}: {e}"
            )
        except Exception as e:
            logger.error(
                f"Неожиданная ошибка при отправке уведомления о канале {channel.id}: "
                f"{type(e).__name__}: {e}"
            )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild

        # Получаем настройки сервера из БД
        connection = get_db_connection()
        if not connection:
            logger.warning(f"Не удалось подключиться к БД при обработке voice state update для сервера {guild.id}")
            return

        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT creator_channel_id FROM server_settings WHERE guild_id = %s",
                (guild.id,)
            )
            result = cursor.fetchone()
            connection.close()

            if not result or not result[0]:
                logger.debug(f"Сервер {guild.id} не настроен — нет creator_channel_id")
                return  # Сервер не настроен

            creator_channel_id = result[0]
        except Exception as e:
            logger.error(f"Ошибка получения настроек сервера {guild.id}: {e}")
            return
        
        # Проверка: пользователь покинул голосовой канал
        if before.channel and before.channel.id in self.created_channels:
            channel = self.bot.get_channel(before.channel.id)
            # Проверяем, существует ли канал и пуст ли он
            if channel and len(channel.members) == 0:
                # Удаляем из БД независимо от существования канала
                connection = get_db_connection()
                if connection:
                    try:
                        cursor = connection.cursor()
                        cursor.execute(
                            "DELETE FROM voice_channels WHERE channel_id = %s",
                            (before.channel.id,)
                        )
                        connection.commit()
                    except Exception as e:
                        logger.error(f"Ошибка удаления канала {before.channel.id} из БД: {e}")
                    finally:
                        connection.close()

                # Безопасно удаляем канал
                try:
                    await channel.delete()
                except discord.NotFound:
                    logger.warning(f"Канал {before.channel.id} уже удалён (404 Not Found)")
                except discord.Forbidden:
                    logger.error(f"Нет прав на удаление канала {before.channel.id}")
                except discord.HTTPException as e:
                    logger.error(f"Другая ошибка API при удалении канала {before.channel.id}: {e}")

                # Очищаем кэш в любом случае
                if before.channel.id in self.created_channels:
                    del self.created_channels[before.channel.id]
                logger.debug(f"Кэш очищен для канала {before.channel.id}")
        # Проверка: пользователь зашёл в отслеживаемый канал
        if after.channel and after.channel.id == creator_channel_id:
            # Проверяем, не находится ли пользователь уже в созданном канале
            for channel_data in self.created_channels.values():
                if (channel_data["owner"].id == member.id
                        and member in channel_data["channel"].members):
                    logger.debug(f"Пользователь {member.id} уже в созданном канале {channel_data['channel'].id}")
                    return

            category = after.channel.category
            channel_name = self.generate_channel_name(guild.id)
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=True),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    manage_channels=True
                )
            }
            new_channel = await guild.create_voice_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites
            )

            await self.save_channel_to_db(new_channel.id, guild.id, member.id, channel_name)
            self.created_channels[new_channel.id] = {"channel_id": new_channel.id, "owner": member}
            await member.move_to(new_channel)
            await self.send_notification(guild.id, new_channel, member)

            text_channel = new_channel.guild.get_channel(new_channel.id)
            if text_channel is None:
                text_channel = await new_channel.create_text_channel(name=f"chat-{new_channel.name}")
            embed = discord.Embed(title=f"Управление приватной комнатой `{channel_name}`", color=discord.Color.dark_red())
            embed.add_field(
                name="🛠️ Основные действия",
                value=(
                    f"➕ Добавить/Убрать слот\n"
                    f"🔓 Открыть/Закрыть канал\n"
                    f"✏️ Переименовать канал\n"
                    f"👁️ Скрыть/Показать\n"
                    f"🔄 Передать канал"
                ),
                inline=True
            )
            embed.add_field(
                name="👥 Управление пользователями",
                value=(
                    f"<:pluspolz:1507549400073769060> Добавить пользователя\n"
                    f"<:minuspolz:1507551823328706600> Убрать пользователя\n"
                    f"🚫 Заблокировать пользователя"
                ),
                inline=True
            )
            embed.set_footer(text="Только владелец канала может использовать эти кнопки.")
            view = ChannelControlView(new_channel, member, self)
            await text_channel.send(embed=embed, view=view)

    @commands.hybrid_command(name="setup_voice", description="Настройка голосового канала")
    @commands.has_permissions(administrator=True)
    async def setup_voice(self, ctx, watched_channel: discord.VoiceChannel, notification_channel: discord.TextChannel):
        """Команда для настройки бота на сервере."""
        connection = get_db_connection()
        if not connection:
            logger.error("Ошибка подключения к базе данных при выполнении setup_voice")
            await ctx.send("Ошибка подключения к базе данных.")
            return

        try:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO server_settings (guild_id, creator_channel_id, notification_channel_id) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE creator_channel_id = VALUES(creator_channel_id), notification_channel_id = VALUES(notification_channel_id)",
                (ctx.guild.id, watched_channel.id, notification_channel.id)
            )
            connection.commit()
            connection.close()

            embed = discord.Embed(
                title="✅ Настройка завершена",
                description=f"**Отслеживаемый канал:** {watched_channel.mention}\n**Канал уведомлений:** {notification_channel.mention}",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            logger.info(f"Выполнена настройка сервера {ctx.guild.id}. Отслеживаемый канал: {watched_channel.id}, канал уведомлений: {notification_channel.id}")
        except Exception as e:
            logger.error(f"Ошибка сохранения настроек сервера {ctx.guild.id}: {e}")
            await ctx.send("Произошла ошибка при сохранении настроек.")

    @commands.hybrid_command(name="voice_stats", description="Статистика голосовых каналов")
    async def voice_stats(self, ctx):
        """Показывает статистику созданных каналов на сервере."""
        connection = get_db_connection()
        if not connection:
            logger.error("Ошибка подключения к БД при выполнении voice_stats")
            await ctx.send("Ошибка подключения к базе данных.")
            return

        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM voice_channels WHERE guild_id = %s",
                (ctx.guild.id,)
            )
            total_channels = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM voice_channels WHERE guild_id = %s AND owner_id = %s",
                (ctx.guild.id, ctx.author.id)
            )
            user_channels = cursor.fetchone()[0]
            connection.close()

            embed = discord.Embed(
                title="📊 Статистика голосовых каналов",
                color=discord.Color.orange()
            )
            embed.add_field(name="Всего создано каналов на сервере", value=str(total_channels), inline=False)
            embed.add_field(name="Вами создано каналов", value=str(user_channels), inline=False)
            await ctx.send(embed=embed)
            logger.debug(f"Статистика для сервера {ctx.guild.id}: всего каналов — {total_channels}, каналов пользователя — {user_channels}")
        except Exception as e:
            logger.error(f"Ошибка получения статистики для сервера {ctx.guild.id}: {e}")
            await ctx.send("Произошла ошибка при получении статистики.")

    def cog_unload(self):
        """Очистка кэша при выгрузке кога."""
        self.created_channels.clear()
        logger.info("Выгрузка кога 'VoiceCog', очистка кэша каналов...")

# Функция для загрузки Cog
async def setup(bot):
    await bot.add_cog(VoiceCog(bot))
