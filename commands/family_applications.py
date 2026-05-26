import discord
from discord.ext import commands
from discord import ui
from datetime import datetime
import logging
import asyncio
from typing import Optional, Dict, List
from database.mysql_connector import (
    fetch_one,
    fetch_all,
    execute_query
)

logger = logging.getLogger(__name__)


class ChannelSelect(discord.ui.Select):
    """Select-меню для выбора канала"""
    def __init__(self, cog: FamilyApplications, setting_type: str, channels: List[discord.TextChannel]):
        self.cog = cog
        self.setting_type = setting_type
        options = []
        for channel in channels[:25]:  # Discord limit: 25 options max
            options.append(
                discord.SelectOption(
                    label=channel.name[:100],
                    description=f"ID: {channel.id}",
                    value=str(channel.id)
                )
            )
        super().__init__(
            placeholder="Выберите канал...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="family_channel_select"
        )

    async def callback(self, interaction: discord.Interaction):
        channel_id = int(self.values[0])
        guild_id = interaction.guild.id
        
        try:
            if self.setting_type == "applications_channel":
                await execute_query(
                    """INSERT INTO family_settings (guild_id, applications_channel_id, enabled)
                    VALUES (%s, %s, TRUE)
                    ON DUPLICATE KEY UPDATE applications_channel_id = %s, enabled = TRUE""",
                    (guild_id, channel_id, channel_id)
                )
                # После настройки канала заявок проверяем и создаем сообщение с кнопкой
                await self.cog._check_and_create_application_message(guild_id)
            elif self.setting_type == "review_channel":
                await execute_query(
                    """INSERT INTO family_settings (guild_id, review_channel_id, enabled)
                    VALUES (%s, %s, TRUE)
                    ON DUPLICATE KEY UPDATE review_channel_id = %s, enabled = TRUE""",
                    (guild_id, channel_id, channel_id)
                )
            elif self.setting_type == "audit_channel":
                await execute_query(
                    """INSERT INTO family_settings (guild_id, audit_channel_id, enabled)
                    VALUES (%s, %s, TRUE)
                    ON DUPLICATE KEY UPDATE audit_channel_id = %s, enabled = TRUE""",
                    (guild_id, channel_id, channel_id)
                )
            
            # Инвалидируем кэш после обновления
            await self.cog._invalidate_settings_cache(guild_id)
            
            # Обновляем оригинальное сообщение с настройками
            settings = await self.cog._get_family_settings(guild_id)
            new_view = FamilySetupView(self.cog, settings)
            
            embed = discord.Embed(
                title="⚙️ Настройки системы заявок",
                description="Выберите тип настройки из меню ниже или нажмите кнопку для создания заявки:",
                color=0xffd700
            )
            
            if settings.get('applications_channel_id'):
                embed.add_field(name="✅ Канал заявок", value=f"<#{settings['applications_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="📥 Канал заявок", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
                
            if settings.get('review_channel_id'):
                embed.add_field(name="✅ Канал рассмотрения", value=f"<#{settings['review_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="📋 Канал рассмотрения", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
                
            if settings.get('accepted_role_id'):
                role = interaction.guild.get_role(settings['accepted_role_id'])
                embed.add_field(name="✅ Роль принятых", value=role.mention if role else "Роль не найдена\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="✅ Роль принятых", value="Не настроена\n\n*Нажмите для настройки*", inline=False)
                
            if settings.get('audit_channel_id'):
                embed.add_field(name="✅ Канал аудита", value=f"<#{settings['audit_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="🔍 Канал аудита", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
            
            embed.set_footer(text=f"Настроено пользователем: {interaction.user.display_name}")
            
            # Сначала пытаемся отредактировать сообщение
            try:
                await interaction.response.edit_message(embed=embed, view=new_view)
            except discord.errors.InteractionResponded:
                # Если уже отвечено, используем безопасный метод
                await self.cog._safe_update_or_send(interaction, embed, new_view)
            except discord.errors.NotFound:
                # Вебхук недействителен, отправляем новое сообщение
                await interaction.followup.send(embed=embed, view=new_view, ephemeral=True)

        except Exception as e:
            logger.error(f"Ошибка при настройке канала: {e}")
            try:
                await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)
            except discord.errors.InteractionResponded:
                pass
            except:
                pass


class ChannelSelectView(discord.ui.View):
    """View для выбора канала с кнопкой назад"""
    def __init__(self, cog: FamilyApplications, setting_type: str, channels: List[discord.TextChannel], original_settings: dict):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(ChannelSelect(cog, setting_type, channels))
        self.original_settings = original_settings
    
    @discord.ui.button(label="⬅️ Назад", style=discord.ButtonStyle.secondary, custom_id="channel_back_btn")
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await self.cog._get_family_settings(interaction.guild.id)
        new_view = FamilySetupView(self.cog, settings)
        
        embed = discord.Embed(
            title="⚙️ Настройки системы заявок",
            description="Выберите тип настройки из меню ниже или нажмите кнопку для создания заявки:",
            color=0xffd700
        )
        
        if settings.get('applications_channel_id'):
            embed.add_field(name="✅ Канал заявок", value=f"<#{settings['applications_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="📥 Канал заявок", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
            
        if settings.get('review_channel_id'):
            embed.add_field(name="✅ Канал рассмотрения", value=f"<#{settings['review_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="📋 Канал рассмотрения", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
            
        if settings.get('accepted_role_id'):
            role = interaction.guild.get_role(settings['accepted_role_id'])
            embed.add_field(name="✅ Роль принятых", value=role.mention if role else "Роль не найдена\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="✅ Роль принятых", value="Не настроена\n\n*Нажмите для настройки*", inline=False)
            
        if settings.get('audit_channel_id'):
            embed.add_field(name="✅ Канал аудита", value=f"<#{settings['audit_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="🔍 Канал аудита", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
        
        embed.set_footer(text=f"Настроено пользователем: {interaction.user.display_name}")
        
        # Обновляем оригинальное сообщение или отправляем новое
        await self.cog._safe_update_or_send(interaction, embed, new_view)


class RoleSelect(discord.ui.Select):
    """Select-меню для выбора роли"""
    def __init__(self, cog: FamilyApplications, roles: List[discord.Role]):
        self.cog = cog
        options = []
        for role in roles[:25]:  # Discord limit: 25 options max
            options.append(
                discord.SelectOption(
                    label=role.name[:100],
                    description=f"ID: {role.id}",
                    value=str(role.id)
                )
            )
        super().__init__(
            placeholder="Выберите роль...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="family_role_select"
        )

    async def callback(self, interaction: discord.Interaction):
        role_id = int(self.values[0])
        guild_id = interaction.guild.id
        
        try:
            await execute_query(
                """INSERT INTO family_settings (guild_id, accepted_role_id, enabled)
                VALUES (%s, %s, TRUE)
                ON DUPLICATE KEY UPDATE accepted_role_id = %s, enabled = TRUE""",
                (guild_id, role_id, role_id)
            )
            
            # Инвалидируем кэш после обновления
            await self.cog._invalidate_settings_cache(guild_id)
            
            # Обновляем оригинальное сообщение с настройками
            settings = await self.cog._get_family_settings(guild_id)
            new_view = FamilySetupView(self.cog, settings)
            
            embed = discord.Embed(
                title="⚙️ Настройки системы заявок",
                description="Выберите тип настройки из меню ниже или нажмите кнопку для создания заявки:",
                color=0xffd700
            )
            
            if settings.get('applications_channel_id'):
                embed.add_field(name="✅ Канал заявок", value=f"<#{settings['applications_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="📥 Канал заявок", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
                
            if settings.get('review_channel_id'):
                embed.add_field(name="✅ Канал рассмотрения", value=f"<#{settings['review_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="📋 Канал рассмотрения", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
                
            if settings.get('accepted_role_id'):
                role = interaction.guild.get_role(settings['accepted_role_id'])
                embed.add_field(name="✅ Роль принятых", value=role.mention if role else "Роль не найдена\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="✅ Роль принятых", value="Не настроена\n\n*Нажмите для настройки*", inline=False)
                
            if settings.get('audit_channel_id'):
                embed.add_field(name="✅ Канал аудита", value=f"<#{settings['audit_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
            else:
                embed.add_field(name="🔍 Канал аудита", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
            
            embed.set_footer(text=f"Настроено пользователем: {interaction.user.display_name}")
            
            # Сначала пытаемся отредактировать сообщение
            try:
                await interaction.response.edit_message(embed=embed, view=new_view)
            except discord.errors.InteractionResponded:
                # Если уже отвечено, используем безопасный метод
                await self.cog._safe_update_or_send(interaction, embed, new_view)
            except discord.errors.NotFound:
                # Вебхук недействителен, отправляем новое сообщение
                await interaction.followup.send(embed=embed, view=new_view, ephemeral=True)

        except Exception as e:
            logger.error(f"Ошибка при настройке роли: {e}")
            try:
                await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)
            except discord.errors.InteractionResponded:
                pass
            except:
                pass

class RoleSelectView(discord.ui.View):
    """View для выбора роли с кнопкой назад"""
    def __init__(self, cog: FamilyApplications, roles: List[discord.Role], original_settings: dict):
        super().__init__(timeout=None)
        self.cog = cog
        self.add_item(RoleSelect(cog, roles))
        self.original_settings = original_settings
    
    @discord.ui.button(label="⬅️ Назад", style=discord.ButtonStyle.secondary, custom_id="role_back_btn")
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = await self.cog._get_family_settings(interaction.guild.id)
        new_view = FamilySetupView(self.cog, settings)
        
        embed = discord.Embed(
            title="⚙️ Настройки системы заявок",
            description="Выберите тип настройки из меню ниже или нажмите кнопку для создания заявки:",
            color=0xffd700
        )
        
        if settings.get('applications_channel_id'):
            embed.add_field(name="✅ Канал заявок", value=f"<#{settings['applications_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="📥 Канал заявок", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
            
        if settings.get('review_channel_id'):
            embed.add_field(name="✅ Канал рассмотрения", value=f"<#{settings['review_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="📋 Канал рассмотрения", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
            
        if settings.get('accepted_role_id'):
            role = interaction.guild.get_role(settings['accepted_role_id'])
            embed.add_field(name="✅ Роль принятых", value=role.mention if role else "Роль не найдена\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="✅ Роль принятых", value="Не настроена\n\n*Нажмите для настройки*", inline=False)
            
        if settings.get('audit_channel_id'):
            embed.add_field(name="✅ Канал аудита", value=f"<#{settings['audit_channel_id']}>\n\n*Нажмите для изменения*", inline=False)
        else:
            embed.add_field(name="🔍 Канал аудита", value="Не настроен\n\n*Нажмите для настройки*", inline=False)
        
        embed.set_footer(text=f"Настроено пользователем: {interaction.user.display_name}")
        
        # Обновляем оригинальное сообщение или отправляем новое
        await self.cog._safe_update_or_send(interaction, embed, new_view)


class FamilySetupSelect(discord.ui.Select):
    """Select-меню для выбора типа настройки при setup_family"""
    def __init__(self, cog: FamilyApplications, current_settings: dict):
        self.cog = cog
        self.current_settings = current_settings
        
        # Определяем, какие настройки не настроены
        apps_not_set = current_settings.get('applications_channel_id') is None
        review_not_set = current_settings.get('review_channel_id') is None
        role_not_set = current_settings.get('accepted_role_id') is None
        audit_not_set = current_settings.get('audit_channel_id') is None
        
        # Убираем automatic default, чтобы пользователь мог сам выбрать любой пункт
        options = [
            discord.SelectOption(
                label="Канал заявок",
                description="Выберите канал для подачи заявок",
                emoji="📥",
                value="applications_channel"
                # default убран
            ),
            discord.SelectOption(
                label="Канал рассмотрения",
                description="Выберите канал для рассмотрения заявок",
                emoji="📋",
                value="review_channel"
                # default убран
            ),
            discord.SelectOption(
                label="Роль принятых",
                description="Выберите роль для принятых участников",
                emoji="✅",
                value="accepted_role"
                # default убран
            ),
            discord.SelectOption(
                label="Канал аудита",
                description="Выберите канал для аудита действий",
                emoji="🔍",
                value="audit_channel"
                # default убран
            ),
        ]
        super().__init__(
            placeholder="Выберите тип настройки...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="family_setup_select"
        )

    async def callback(self, interaction: discord.Interaction):
        selected_type = self.values[0]
        guild = interaction.guild
        
        try:
            if selected_type in ["applications_channel", "review_channel", "audit_channel"]:
                # Получаем все текстовые каналы сервера
                channels = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]
                channels.sort(key=lambda c: c.position)
                
                settings = await self.cog._get_family_settings(guild.id)
                view = ChannelSelectView(self.cog, selected_type, channels, settings)
                
                embed = discord.Embed(
                    title="📝 Выбор канала",
                    description=f"Выберите канал из списка ниже:",
                    color=0xffd700
                )
                
                # Сначала пытаемся отредактировать сообщение
                try:
                    await interaction.response.edit_message(embed=embed, view=view)
                except discord.errors.InteractionResponded:
                    # Если уже отвечено, используем безопасный метод
                    await self.cog._safe_update_or_send(interaction, embed, view)
                except discord.errors.NotFound:
                    # Вебхук недействителен, отправляем новое сообщение
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
            elif selected_type == "accepted_role":
                # Получаем все роли сервера (исключая ботов и Nitro роли)
                roles = [r for r in guild.roles if not r.managed and not r.is_premium_subscriber()]
                roles = roles[1:]  # Исключаем @everyone
                roles.reverse()  # Сортируем от высшей к низшей
                
                settings = await self.cog._get_family_settings(guild.id)
                view = RoleSelectView(self.cog, roles, settings)
                
                embed = discord.Embed(
                    title="🎭 Выбор роли",
                    description=f"Выберите роль из списка ниже:",
                    color=0xffd700
                )
                
                # Сначала пытаемся отредактировать сообщение
                try:
                    await interaction.response.edit_message(embed=embed, view=view)
                except discord.errors.InteractionResponded:
                    # Если уже отвечено, используем безопасный метод
                    await self.cog._safe_update_or_send(interaction, embed, view)
                except discord.errors.NotFound:
                    # Вебхук недействителен, отправляем новое сообщение
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
        except Exception as e:
            logger.error(f"Ошибка при открытии меню выбора: {e}")
            try:
                await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)
            except:
                pass


class FamilySetupView(discord.ui.View):
    """View для настройки системы заявок через select-меню"""
    def __init__(self, cog: FamilyApplications, current_settings: dict = None):
        super().__init__(timeout=None)
        self.cog = cog
        self.current_settings = current_settings or {}
        self.add_item(FamilySetupSelect(cog, self.current_settings))
        
    @discord.ui.button(label="📝 Создать заявку", style=discord.ButtonStyle.primary, custom_id="create_application_btn", row=1)
    async def create_application_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateApplicationModal(self.cog)
        await interaction.response.send_modal(modal)


class CreateApplicationButtonView(discord.ui.View):
    """View с кнопкой создания заявки администратором"""
    def __init__(self, cog: FamilyApplications):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="📝 Создать заявку", style=discord.ButtonStyle.primary, custom_id="create_application_button")
    async def create_application_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateApplicationModal(self.cog)
        await interaction.response.send_modal(modal)


class CreateApplicationModal(discord.ui.Modal, title="Создание заявки"):
    """Модальное окно для создания сообщения с заявкой"""
    def __init__(self, cog: FamilyApplications):
        super().__init__()
        self.cog = cog

    title_input = discord.ui.TextInput(
        label="Заголовок сообщения",
        placeholder="👪 Заявка в семью",
        required=True,
        max_length=256,
        style=discord.TextStyle.short
    )

    description_input = discord.ui.TextInput(
        label="Текст приветствия",
        placeholder="Описание для пользователей...",
        required=True,
        max_length=4000,
        style=discord.TextStyle.paragraph
    )

    image_url_input = discord.ui.TextInput(
        label="URL изображения (необязательно)",
        placeholder="https://example.com/image.png",
        required=False,
        max_length=512,
        style=discord.TextStyle.short
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Сначала defer, чтобы закрыть модальное окно и избежать таймаута
            await interaction.response.defer(ephemeral=True)
            
            guild_id = interaction.guild.id
            settings = await self.cog._get_family_settings(guild_id)
            applications_channel_id = settings.get('applications_channel_id')

            if not applications_channel_id:
                await interaction.followup.send(
                    "❌ Канал заявок не настроен! Используйте команду настройки.",
                    ephemeral=True
                )
                return

            channel = interaction.guild.get_channel(applications_channel_id)
            if not channel:
                await interaction.followup.send(
                    "❌ Канал заявок не найден!",
                    ephemeral=True
                )
                return

            # Сохраняем данные в базу данных
            await execute_query(
                """INSERT INTO family_settings (guild_id, application_title, application_text, application_image_url, enabled)
                VALUES (%s, %s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE 
                    application_title = %s,
                    application_text = %s,
                    application_image_url = %s,
                    enabled = TRUE""",
                (guild_id, self.title_input.value, self.description_input.value, 
                 self.image_url_input.value or None,
                 self.title_input.value, self.description_input.value, self.image_url_input.value or None)
            )

            # Инвалидируем кэш
            await self.cog._invalidate_settings_cache(guild_id)

            # Получаем обновленные настройки
            updated_settings = await self.cog._get_family_settings(guild_id)

            # Создаём embed с сохраненными данными
            embed = discord.Embed(
                title=updated_settings.get('application_title', '👪 Заявка в семью'),
                description=updated_settings.get('application_text', 'Нажмите кнопку ниже, чтобы подать заявку в нашу семью!'),
                color=0xffd700
            )

            # Добавляем изображение если указано
            image_url = updated_settings.get('application_image_url')
            if image_url:
                embed.set_image(url=image_url)

            # Отправляем сообщение с кнопкой "Подать заявку"
            view = ApplicationButtonView(self.cog)
            message = await channel.send(embed=embed, view=view)

            # Сохраняем ID сообщения в базу данных вместе с текстом и изображением
            await execute_query(
                """INSERT INTO family_settings (guild_id, application_message_id, application_title, application_text, application_image_url, enabled)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE 
                    application_message_id = %s,
                    application_title = %s,
                    application_text = %s,
                    application_image_url = %s""",
                (guild_id, message.id, updated_settings.get('application_title'), 
                 updated_settings.get('application_text'), updated_settings.get('application_image_url'),
                 message.id, updated_settings.get('application_title'), 
                 updated_settings.get('application_text'), updated_settings.get('application_image_url'))
            )

            # Снова инвалидируем кэш после обновления ID сообщения
            await self.cog._invalidate_settings_cache(guild_id)

            try:
                await interaction.followup.send(
                    f"✅ Сообщение с заявкой создано в канале {channel.mention}!",
                    ephemeral=True
                )
            except discord.errors.NotFound:
                # Вебхук недействителен, отправляем новое сообщение
                await interaction.channel.send(
                    f"✅ Сообщение с заявкой создано в канале {channel.mention}!",
                    delete_after=10
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке подтверждения: {e}")
                
        except Exception as e:
            logger.error(f"Критическая ошибка при создании заявки: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ Произошла ошибка при создании сообщения с заявкой. Попробуйте снова.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "❌ Произошла ошибка при создании сообщения с заявкой. Попробуйте снова.",
                        ephemeral=True
                    )
            except:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Ошибка при создании заявки: {error}")
        try:
            if isinstance(error, discord.errors.NotFound) and error.code == 10015:
                # Unknown Webhook - вебхук больше не действителен
                await interaction.channel.send(
                    "❌ Произошла ошибка при создании сообщения с заявкой (вебхук недействителен). Попробуйте снова.",
                    ephemeral=False,
                    delete_after=10
                )
            else:
                await interaction.followup.send(
                    "❌ Произошла ошибка при создании сообщения с заявкой.",
                    ephemeral=True
                )
        except discord.errors.NotFound:
            # Вебхук недействителен, отправляем в канал
            await interaction.channel.send(
                "❌ Произошла ошибка при создании сообщения с заявкой.",
                delete_after=10
            )
        except:
            pass


class FamilyApplications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pending_applications: Dict[int, Dict] = {}
        self.family_settings_cache: Dict[int, dict] = {}

    async def _safe_update_or_send(self, interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View):
        """Безопасно обновляет сообщение или отправляет новое, если вебхук недействителен"""
        try:
            original_message = await interaction.original_response()
            await original_message.edit(embed=embed, view=view)
        except discord.errors.NotFound:
            # Вебхук больше не действителен (например, после создания сообщения с заявкой)
            try:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except discord.errors.InteractionResponded:
                logger.warning("Не удалось отправить followup: взаимодействие уже обработано")
            except Exception as e:
                logger.warning(f"Не удалось отправить followup: {e}")
        except discord.errors.InteractionResponded:
            # Взаимодействие уже было обработано - логируем для отладки
            logger.debug("Взаимодействие уже обработано при попытке обновления")
        except Exception as edit_error:
            logger.warning(f"Не удалось обновить сообщение: {edit_error}")
            try:
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            except discord.errors.InteractionResponded:
                logger.warning("Не удалось отправить followup: взаимодействие уже обработано")
            except Exception as e:
                logger.warning(f"Не удалось отправить followup: {e}")

    async def cog_load(self):
        # Загружаем заявки из БД в кэш
        await self._load_pending_applications_from_db()
        # Восстанавливаем сообщения с заявками
        await self._restore_review_messages()
        # Восстанавливаем кнопки подачи заявок
        await self._restore_application_buttons()

    async def _load_pending_applications_from_db(self):
        """Загружает все ожидающие заявки из БД в кэш"""
        try:
            pending_apps = await fetch_all(
                "SELECT * FROM family_applications WHERE status = 'pending'"
            )
            for app in pending_apps:
                self.pending_applications[app['user_id']] = {
                    'user_id': app['user_id'],
                    'guild_id': app['guild_id'],
                    'status': app['status'],
                    'review_message_id': app.get('review_message_id'),
                    'reason': app['reason'],
                    'experience': app.get('experience'),
                    'availability': app['availability'],
                    'submitted_at': app['submitted_at']
                }
        except Exception as e:
            logger.error(f"Ошибка загрузки заявок из БД: {e}")
    async def _restore_review_messages(self):
        for guild in self.bot.guilds:
            try:
                settings = await self._get_family_settings(guild.id)
                review_channel_id = settings.get('review_channel_id')
                if not review_channel_id:
                    logger.debug(f"Канал рассмотрения не настроен для гильдии {guild.id}")
                    continue

                review_channel = guild.get_channel(review_channel_id)
                if not review_channel:
                    logger.warning(f"Канал рассмотрения {review_channel_id} не найден для гильдии {guild.id}")
                    continue

                perms = review_channel.permissions_for(guild.me)
                if not (perms.send_messages and perms.manage_messages):
                    logger.warning(f"Нет прав для редактирования сообщений в канале {review_channel_id} гильдии {guild.id}")
                    continue

                # Используем безопасную функцию fetch_all
                pending_apps = await fetch_all(
                    "SELECT user_id, review_message_id FROM family_applications "
                    "WHERE guild_id = %s AND status = 'pending' AND review_message_id IS NOT NULL "
                    "ORDER BY submitted_at DESC LIMIT 100",
                    (guild.id,)
                )

                for app in pending_apps:
                    if app['user_id'] not in self.pending_applications:
                        self.pending_applications[app['user_id']] = {
                    'user_id': app['user_id'],
                    'guild_id': guild.id,
                    'status': 'pending',
                    'review_message_id': app['review_message_id']
                }

                for app in pending_apps:
                    await self._restore_single_review_message(review_channel, app)
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Критическая ошибка при восстановлении сообщений для гильдии {guild.id}: {e}")

    async def _restore_single_review_message(self, channel: discord.TextChannel, app_data: dict):
        try:
            message_id = app_data.get('review_message_id')
            if not message_id:
                return
            message = await channel.fetch_message(message_id)
            # Создаём View с правильным applicant_id
            view = ApplicationReviewView(
                self,
                app_data['user_id'],
                channel.id
            )
            await message.edit(view=view)
            logger.debug(f"Восстановлены кнопки для заявки пользователя {app_data['user_id']}")
        except discord.NotFound:
            logger.warning(f"Сообщение {message_id} не найдено, пропускаем")
        except Exception as e:
            logger.error(f"Ошибка восстановления кнопок для {app_data['user_id']}: {e}")

    @commands.command(name="setup_family")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def setup_family(self, ctx):
        """Команда настройки системы заявок через select-меню"""
        guild_id = ctx.guild.id
        logger.info(f"Настройка системы заявок для гильдии {guild_id}")

        # Проверяем текущие настройки
        settings = await self._get_family_settings(guild_id)
        
        view = FamilySetupView(self, settings)
        
        embed = discord.Embed(
            title="⚙️ Настройка системы заявок",
            description="Выберите тип настройки из меню ниже:",
            color=0xffd700
        )
        
        if settings.get('applications_channel_id'):
            embed.add_field(
                name="✅ Канал заявок",
                value=f"<#{settings['applications_channel_id']}>",
                inline=False
            )
        else:
            embed.add_field(
                name="📥 Канал заявок",
                value="Не настроен",
                inline=False
            )
            
        if settings.get('review_channel_id'):
            embed.add_field(
                name="✅ Канал рассмотрения",
                value=f"<#{settings['review_channel_id']}>",
                inline=False
            )
        else:
            embed.add_field(
                name="📋 Канал рассмотрения",
                value="Не настроен",
                inline=False
            )
            
        if settings.get('accepted_role_id'):
            role = ctx.guild.get_role(settings['accepted_role_id'])
            embed.add_field(
                name="✅ Роль принятых",
                value=role.mention if role else "Роль не найдена",
                inline=False
            )
        else:
            embed.add_field(
                name="✅ Роль принятых",
                value="Не настроена",
                inline=False
            )
            
        if settings.get('audit_channel_id'):
            embed.add_field(
                name="✅ Канал аудита",
                value=f"<#{settings['audit_channel_id']}>",
                inline=False
            )
        else:
            embed.add_field(
                name="🔍 Канал аудита",
                value="Не настроен",
                inline=False
            )
        
        embed.set_footer(text=f"Запрос от: {ctx.author.display_name}")
        
        await ctx.send(embed=embed, view=view)

    async def _send_application_button(self, channel: discord.TextChannel):
        if not channel.permissions_for(channel.guild.me).send_messages:
            logger.warning(f"Нет прав на отправку в канал {channel.id}")
            return

        async for message in channel.history(limit=100):
            if message.author == self.bot.user and message.components:
                view = ApplicationButtonView(self)
                await message.edit(view=view)
                return

        view = ApplicationButtonView(self)
        embed = discord.Embed(
            title="👪 Заявка в семью",
            color=0xffd700,
            description="Нажмите кнопку ниже, чтобы подать заявку в нашу семью!"
        )
        await channel.send(embed=embed, view=view)
    async def _restore_application_buttons(self):
        for guild in self.bot.guilds:
            try:
                settings = await self._get_family_settings(guild.id)
                applications_channel_id = settings.get('applications_channel_id')
                if not applications_channel_id:
                    logger.debug(f"Канал подачи заявок не настроен для гильдии {guild.id}")
                    continue

                channel = guild.get_channel(applications_channel_id)
                if not channel:
                    logger.warning(f"Канал подачи заявок {applications_channel_id} не найден для гильдии {guild.id}")
                    continue

                # Проверяем права бота
                perms = channel.permissions_for(guild.me)
                if not (perms.read_message_history and perms.send_messages and perms.manage_messages):
                    logger.warning(
                        f"Нет прав для восстановления кнопок в канале {channel.id} гильдии {guild.id}"
                    )
                    continue

                # Ищем сообщения бота с компонентами
                has_target_button = False
                target_message = None
                
                async for message in channel.history(limit=100):
                    if (message.author == self.bot.user
                            and message.components):

                        # Проверяем, есть ли кнопка с нужным custom_id
                        has_target_button = any(
                    component.custom_id == "application_button"
                    for action_row in message.components
                    for component in action_row.children
                )
                        
                        if has_target_button:
                            target_message = message
                            break

                if has_target_button and target_message:
                    # Создаём новый View
                    view = ApplicationButtonView(self)
                    # Редактируем сообщение, заменяя View
                    await target_message.edit(view=view)
                else:
                    # Сообщение не найдено или было удалено - создаем новое
                    await self._create_application_message(channel, settings)

            except discord.Forbidden:
                logger.warning(f"Нет доступа к каналу {applications_channel_id} гильдии {guild.id}")
            except discord.HTTPException as e:
                logger.error(f"HTTP ошибка при восстановлении кнопок гильдии {guild.id}: {e}")
            except Exception as e:
                logger.error(f"Критическая ошибка при восстановлении кнопок гильдии {guild.id}: {e}", exc_info=True)

            # Задержка между гильдиями для соблюдения лимитов API
            await asyncio.sleep(0.5)

    async def _create_application_message(self, channel: discord.TextChannel, settings: dict):
        """Создает новое сообщение с кнопкой подачи заявки, используя сохраненные данные"""
        try:
            view = ApplicationButtonView(self)
            
            embed = discord.Embed(
                title=settings.get('application_title', '👪 Заявка в семью'),
                description=settings.get('application_text', 'Нажмите кнопку ниже, чтобы подать заявку в нашу семью!'),
                color=0xffd700
            )
            
            image_url = settings.get('application_image_url')
            if image_url:
                embed.set_image(url=image_url)
            
            message = await channel.send(embed=embed, view=view)
            
            # Сохраняем ID сообщения в базу данных вместе с текстом и изображением
            await execute_query(
                """INSERT INTO family_settings (guild_id, application_message_id, application_title, application_text, application_image_url, enabled)
                VALUES (%s, %s, %s, %s, %s, TRUE)
                ON DUPLICATE KEY UPDATE 
                    application_message_id = %s,
                    application_title = %s,
                    application_text = %s,
                    application_image_url = %s""",
                (channel.guild.id, message.id, settings.get('application_title'), 
                 settings.get('application_text'), settings.get('application_image_url'),
                 message.id, settings.get('application_title'), 
                 settings.get('application_text'), settings.get('application_image_url'))
            )
            
            # Инвалидируем кэш
            await self._invalidate_settings_cache(channel.guild.id)
            
            logger.info(f"Создано новое сообщение с заявкой в канале {channel.id} гильдии {channel.guild.id}, message_id={message.id}")
            
        except Exception as e:
            logger.error(f"Ошибка при создании сообщения с заявкой: {e}", exc_info=True)

    async def _check_and_create_application_message(self, guild_id: int):
        """Проверяет наличие сообщения с кнопкой заявки и создает его при необходимости"""
        try:
            settings = await self._get_family_settings(guild_id)
            applications_channel_id = settings.get('applications_channel_id')
            
            if not applications_channel_id:
                logger.debug(f"Канал заявок не настроен для гильдии {guild_id}")
                return
            
            guild = self.bot.get_guild(guild_id)
            if not guild:
                logger.warning(f"Гильдия {guild_id} не найдена")
                return
                
            channel = guild.get_channel(applications_channel_id)
            if not channel:
                logger.warning(f"Канал заявок {applications_channel_id} не найден для гильдии {guild_id}")
                return
            
            # Проверяем права бота
            perms = channel.permissions_for(guild.me)
            if not (perms.read_message_history and perms.send_messages and perms.manage_messages):
                logger.warning(f"Нет прав для создания сообщения с заявкой в канале {channel.id} гильдии {guild_id}")
                return
            
            # Ищем существующее сообщение с кнопкой
            has_target_button = False
            
            # Увеличиваем limit до 100 для более надежного поиска
            async for message in channel.history(limit=100):
                if (message.author == self.bot.user and message.components):
                    # Проверяем, есть ли кнопка с нужным custom_id
                    has_target_button = any(
                        component.custom_id == "application_button"
                        for action_row in message.components
                        for component in action_row.children
                    )
                    if has_target_button:
                        break
            
            if not has_target_button:
                # Сообщение не найдено или было удалено - создаем новое
                await self._create_application_message(channel, settings)
                
        except Exception as e:
            logger.error(f"Ошибка при проверке/создании сообщения с заявкой: {e}", exc_info=True)


    async def _get_family_settings(self, guild_id: int) -> dict:
        if guild_id in self.family_settings_cache:
            return self.family_settings_cache[guild_id]


        try:
            result = await fetch_one(
                "SELECT * FROM family_settings WHERE guild_id = %s AND enabled = TRUE",
                (guild_id,)
            )
            if result:
                settings = {
                    'applications_channel_id': result.get('applications_channel_id'),
                    'review_channel_id': result.get('review_channel_id'),
                    'enabled': result.get('enabled', False),
                    'accepted_role_id': result.get('accepted_role_id'),
                    'audit_channel_id': result.get('audit_channel_id'),
                    'application_message_id': result.get('application_message_id'),
                    'application_title': result.get('application_title', '👪 Заявка в семью'),
                    'application_text': result.get('application_text', 'Нажмите кнопку ниже, чтобы подать заявку в нашу семью!'),
                    'application_image_url': result.get('application_image_url')
                }
                self.family_settings_cache[guild_id] = settings
                return settings
            else:
                # Сохраняем пустой результат в кэш, чтобы не делать повторные запросы
                empty_settings = {
                    'applications_channel_id': None,
                    'review_channel_id': None,
                    'enabled': False,
                    'accepted_role_id': None,
                    'audit_channel_id': None,
                    'application_message_id': None,
                    'application_title': '👪 Заявка в семью',
                    'application_text': 'Нажмите кнопку ниже, чтобы подать заявку в нашу семью!',
                    'application_image_url': None
                }
                self.family_settings_cache[guild_id] = empty_settings
                return empty_settings
        except Exception as e:
            logger.exception(f"Ошибка получения настроек семьи {guild_id}: {e}")
            # Сохраняем в кэш с enabled=False
            error_settings = {
                'applications_channel_id': None,
                'review_channel_id': None,
                'enabled': False,
                'accepted_role_id': None,
                'audit_channel_id': None,
                'application_message_id': None,
                'application_title': '👪 Заявка в семью',
                'application_text': 'Нажмите кнопку ниже, чтобы подать заявку в нашу семью!',
                'application_image_url': None
            }
            self.family_settings_cache[guild_id] = error_settings
            return error_settings

    @commands.command(name="family_status")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def family_status(self, ctx):
        """Показывает статус системы заявок в семью"""
        guild_id = ctx.guild.id
        settings = await self._get_family_settings(guild_id)

        embed = discord.Embed(title="Статус системы заявок в семью", color=0x00ff00)
        embed.add_field(
            name="Канал подачи заявок",
            value=f"<#{settings['applications_channel_id']}>" if settings['applications_channel_id'] else "Не настроен",
            inline=False
        )
        embed.add_field(
            name="Канал рассмотрения заявок",
            value=f"<#{settings['review_channel_id']}>" if settings['review_channel_id'] else "Не настроен",
            inline=False
        )
        embed.add_field(
            name="Статус",
            value="Включена" if settings['enabled'] else "Выключена",
            inline=True
        )
        # Подсчёт ожидающих заявок
        pending_count = len([
            app for app in self.pending_applications.values()
            if app.get('guild_id') == guild_id
        ])
        embed.add_field(
            name="Ожидающих заявок",
            value=pending_count,
            inline=True
        )

        # Дополнительно показываем количество заявок в БД
        try:
            db_pending = await fetch_all(
                "SELECT COUNT(*) as count FROM family_applications WHERE guild_id = %s AND status = 'pending'",
                (guild_id,)
            )
            if db_pending:
                db_count = db_pending[0].get('count', 0)
                embed.add_field(
                    name="Заявок в БД",
            value=db_count,
            inline=True
                )
        except Exception as e:
            logger.warning(f"Не удалось получить количество заявок из БД: {e}")

        await ctx.send(embed=embed)

    @commands.command(name="family_applications")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def family_applications(self, ctx, member: Optional[discord.Member] = None):
        """Показывает все ожидающие заявки. Можно указать пользователя для фильтрации."""
        guild_id = ctx.guild.id

        # Логируем вызов команды
        logger.info(f"Пользователь {ctx.author.id} запросил список заявок для гильдии {guild_id}")

        try:
            # Формируем запрос с фильтрацией по пользователю, если он указан
            if member:
                applications = await fetch_all(
                    "SELECT * FROM family_applications "
                    "WHERE guild_id = %s AND user_id = %s AND status = 'pending' "
                    "ORDER BY submitted_at DESC LIMIT 100",
                    (guild_id, member.id)
                )
                title = f"👥 Заявки пользователя {member.display_name}"
                description = "Показываются только активные заявки этого пользователя"
            else:
                applications = await fetch_all(
                    "SELECT * FROM family_applications "
                    "WHERE guild_id = %s AND status = 'pending' "
                    "ORDER BY submitted_at DESC LIMIT 100",
                    (guild_id,)
                )
                title = "👥 Ожидающие заявки в семью"
                description = "Показываются только активные заявки сервера"

            if not applications:
                embed = discord.Embed(
                    title=title,
                    color=0x00ff00,
                    description=description
                )
                await ctx.send(embed=embed)
                return

            settings = await self._get_family_settings(guild_id)
            review_channel_id = settings.get('review_channel_id')

            # Создаём пагинатор
            paginator = ApplicationPaginator(
                ctx,
                applications,
                review_channel_id,
                title,
                len(applications)
            )

            await paginator.start()

        except Exception as e:
            logger.error(f"Ошибка при получении заявок для гильдии {guild_id}: {e}")
            await ctx.send("❌ Произошла ошибка при получении списка заявок.")

    async def _notify_admins(self, guild: discord.Guild, application_data: Dict):
        """Уведомляет администраторов о новой заявке"""
        try:
            settings = await self._get_family_settings(guild.id)
            if not settings or not settings['enabled']:
                logger.warning(f"Настройки семьи не найдены или отключены для гильдии {guild.id}")
                return
            
            review_channel_id = settings.get('review_channel_id')

            if not review_channel_id:
                # Если канал рассмотрения не настроен, используем канал подачи заявок
                applications_channel_id = settings.get('applications_channel_id')
                if not applications_channel_id:
                    logger.warning(f"Ни канал рассмотрения, ни канал заявок не настроены для гильдии {guild.id}")
                    return
                channel = guild.get_channel(applications_channel_id)
            else:
                channel = guild.get_channel(review_channel_id)

            if not channel:
                logger.warning(f"Канал {review_channel_id or 'заявок'} не найден")
                return

            user = guild.get_member(application_data['user_id'])
            if not user:
                logger.warning(f"Пользователь {application_data['user_id']} не найден на сервере")
                return

            embed = discord.Embed(
                title="🆕 Новая заявка в семью!",
                color=0xffd700,
                timestamp=datetime.now()
            )
            embed.add_field(
                name="Пользователь",
                value=user.mention,
                inline=True
            )
            embed.add_field(
                name="ID",
                value=str(user.id),
                inline=True
            )
            embed.add_field(
                name="Причина",
                value=application_data['reason'],
                inline=False
            )
            embed.add_field(
                name="Опыт",
                value=application_data.get('experience', 'Не указан'),
                inline=False
            )
            embed.add_field(
                name="Доступность",
                value=application_data['availability'],
                inline=False
            )
            embed.set_thumbnail(url=user.display_avatar.url if user.display_avatar else None)

            # Добавляем кнопки для принятия/отклонения
            view = ApplicationReviewView(self, application_data['user_id'], review_channel_id)

            # Отправляем сообщение и сохраняем его ID
            message = await channel.send(embed=embed, view=view)
            message_id = message.id

            # Обновляем БД, добавляя ID сообщения
            await execute_query(
                "UPDATE family_applications SET review_message_id = %s WHERE user_id = %s AND guild_id = %s",
                (message_id, application_data['user_id'], guild.id)
            )

        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления о заявке {application_data['user_id']}: {e}")

    async def _add_audit_reaction(self, interaction: discord.Interaction, emoji: str, applicant: discord.Member, action: str):
        """Отправляет сообщение в канал аудита БЕЗ реакции"""
        settings = await self._get_family_settings(interaction.guild.id)
        audit_channel_id = settings.get('audit_channel_id')

        if not audit_channel_id:
            logger.warning(f"Канал аудита не настроен для гильдии {interaction.guild.id}")
            return

        audit_channel = interaction.guild.get_channel(audit_channel_id)

        if not audit_channel:
            logger.warning(f"Канал аудита с ID {audit_channel_id} не найден")
            return

        # Проверяем права бота
        perms = audit_channel.permissions_for(interaction.guild.me)
        if not perms.send_messages:
            logger.warning(f"Нет прав для отправки в канал аудита {audit_channel_id}")
            return

        try:
            embed = discord.Embed(
                title="📋 Аудит заявок в семью",
                color=0x7289da,
                timestamp=datetime.now()
            )
            embed.add_field(name="Действие", value=action, inline=True)
            embed.add_field(name="Пользователь", value=applicant.mention, inline=True)
            embed.add_field(name="Администратор", value=interaction.user.mention, inline=True)

            # Отправляем сообщение БЕЗ добавления реакции
            await audit_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"Ошибка при отправке в аудит для гильдии {interaction.guild.id}: {e}")

    async def _invalidate_settings_cache(self, guild_id: int):
        """Инвалидация кэша настроек для гильдии"""
        if guild_id in self.family_settings_cache:
            del self.family_settings_cache[guild_id]
        # Также инвалидируем TTL если он есть
        if hasattr(self, '_settings_cache_ttl') and guild_id in self._settings_cache_ttl:
            del self._settings_cache_ttl[guild_id]

class ApplicationPaginator:
    def __init__(self, ctx, applications, review_channel_id, title, total_count):
        self.ctx = ctx
        self.applications = applications
        self.review_channel_id = review_channel_id
        self.title = title
        self.total_count = total_count
        self.current_page = 0
        self.per_page = 1  # По 1 заявке на страницу (оригинальное поведение)
        self.total_pages = max(1, (total_count + self.per_page - 1) // self.per_page)

    async def start(self):
        embed = await self.create_embed()
        view = PaginationView(self)
        self.message = await self.ctx.send(embed=embed, view=view)

    async def create_embed(self):
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_apps = self.applications[start:end]

        embed = discord.Embed(
            title=f"{self.title} | Страница {self.current_page + 1}/{self.total_pages}",
            color=0xffd700
        )

        # Добавляем информацию о канале рассмотрения
        if self.review_channel_id:
            channel = self.ctx.guild.get_channel(self.review_channel_id)
            if channel:
                embed.set_footer(text=f"Заявки рассматриваются в канале: #{channel.name}")

        # Обрабатываем несколько заявок на странице
        for idx, app in enumerate(page_apps, 1):
            user = self.ctx.guild.get_member(app['user_id'])
            user_display = user.mention if user else f"Пользователь ID {app['user_id']}"

            embed.add_field(
                name=f"#{start + idx} {user_display}",
                value=f"**Причина:** {app['reason'][:100]}\n**Опыт:** {app.get('experience', 'Не указан')[:50]}\n**Доступность:** {app['availability']}\n**Подано:** {app['submitted_at'].strftime('%d.%m.%Y %H:%M')}",
                inline=False
            )

            # Если есть аватар пользователя и это первая заявка, добавляем миниатюру
            if idx == 1 and user and user.display_avatar:
                embed.set_thumbnail(url=user.display_avatar.url)

        return embed
    async def update_page(self, interaction: discord.Interaction, page_change: int):
        self.current_page += page_change
        # Проверка границ
        if self.current_page < 0:
            self.current_page = 0
        elif self.current_page >= self.total_pages:
            self.current_page = max(0, self.total_pages - 1)  # Защита от отрицательных значений
        embed = await self.create_embed()
        return embed


class PaginationView(discord.ui.View):
    def __init__(self, paginator: ApplicationPaginator):
        super().__init__(timeout=300)
        self.paginator = paginator
        self._update_buttons()

    def _update_buttons(self):
        self.previous_button.disabled = self.paginator.current_page <= 0
        self.next_button.disabled = self.paginator.current_page >= self.paginator.total_pages - 1

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.primary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await self.paginator.update_page(interaction, -1)
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Вперёд", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = await self.paginator.update_page(interaction, 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=embed, view=self)
    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            # Редактируем сообщение: отключаем кнопки
            await self.paginator.message.edit(view=self)
            # Ждём 2 секунды, затем удаляем
            await asyncio.sleep(2)
            await self.paginator.message.delete()
        except discord.NotFound:
            pass  # Сообщение уже удалено
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")

class RejectionReasonModal(ui.Modal, title="Причина отклонения заявки"):
    def __init__(self, cog: FamilyApplications, applicant: discord.Member):
        super().__init__()
        self.cog = cog  # Сохраняем ссылку на Cog
        self.applicant = applicant
    reason = ui.TextInput(
        label="Причина отклонения",
        style=discord.TextStyle.paragraph,
        placeholder="Объясните, почему заявка отклонена...",
        required=True,
        max_length=1000
    )
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        try:
            # Проверяем инициализацию self.applicant
            if self.applicant is None:
                logger.error("self.applicant не инициализирован")
                await interaction.followup.send("❌ Ошибка: данные пользователя недоступны.", ephemeral=True)
                return

            applicant_id = self.applicant.id
            logger.debug(f"Получен applicant_id: {applicant_id}")

            # Получаем ID сообщения из БД
            logger.debug(f"Запрос review_message_id для пользователя {applicant_id} в гильдии {interaction.guild.id}")
            try:
                app_data = await fetch_one(
                    "SELECT review_message_id FROM family_applications WHERE user_id = %s AND guild_id = %s",
                    (applicant_id, interaction.guild.id)
                )
                logger.debug(f"Результат запроса БД: {app_data}")
            except Exception as e:
                logger.error(f"Критическая ошибка БД при получении review_message_id: {e}", exc_info=True)
                await interaction.followup.send("❌ Критическая ошибка базы данных. Обратитесь к администратору.", ephemeral=True)
                return

            if not app_data:
                logger.error(f"Заявка пользователя {applicant_id} не найдена в БД")
                await interaction.followup.send("❌ Заявка не найдена в базе данных.", ephemeral=True)
                return

            review_message_id = app_data.get('review_message_id')
            # Обновляем статус в БД с указанием причины отклонения
            logger.debug(f"Обновление статуса заявки {applicant_id} до 'rejected' с причиной: '{self.reason.value}'")
            try:
                await execute_query(
                    """UPDATE family_applications
                    SET status = 'rejected', rejection_reason = %s, reviewed_at = %s
                    WHERE user_id = %s AND guild_id = %s""",
                    (self.reason.value, datetime.now(), applicant_id, interaction.guild.id)
                )
            except Exception as e:
                logger.error(f"Ошибка обновления статуса заявки {applicant_id} в БД: {e}", exc_info=True)
                await interaction.followup.send("❌ Ошибка обновления статуса заявки в базе данных.", ephemeral=True)
                return

            # --- НАЧАЛО ДОБАВЛЕННОГО БЛОКА ---
            # Отправляем сообщение в канал аудита
            logger.debug(f"Отправка записи в аудит для отклонённой заявки {applicant_id}")
            try:
                await self.cog._add_audit_reaction(
                    interaction,
                    '❌',
                    self.applicant,
                    f"Заявка отклонена. Причина: {self.reason.value}"
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке в аудит для заявки {applicant_id}: {e}", exc_info=True)
            # --- КОНЕЦ ДОБАВЛЕННОГО БЛОКА ---

            # Удаляем заявку из кэша
            if applicant_id in self.cog.pending_applications:
                del self.cog.pending_applications[applicant_id]
                logger.info(f"Заявка {applicant_id} удалена из кэша")
            else:
                logger.warning(f"Заявка {applicant_id} не найдена в кэше (возможно, уже обработана)")

            # Уведомляем пользователя о отклонении
            logger.debug(f"Отправка уведомления пользователю {applicant_id} о отклонении заявки")
            try:
                embed = discord.Embed(
                    title="❌ Заявка отклонена",
                    color=0xff0000,
                    description=f"Ваша заявка в семью была отклонена.\n\n**Причина:** {self.reason.value}"
                )
                await self.applicant.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Не удалось отправить ЛС пользователю {applicant_id}: ЛС закрыты или пользователь заблокировал бота")
                await interaction.followup.send(
                    f"❌ Не удалось отправить уведомление пользователю {self.applicant.mention} (ЛС закрыты).",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Неожиданная ошибка при отправке ЛС пользователю {applicant_id}: {e}", exc_info=True)

            # Редактируем сообщение с заявкой в канале рассмотрения
            if review_message_id:
                logger.debug(f"Редактирование сообщения {review_message_id} в канале рассмотрения")
                try:
                    settings = await self.cog._get_family_settings(interaction.guild.id)
                    review_channel_id = settings.get('review_channel_id')

                    if not review_channel_id:
                        logger.warning(f"Канал рассмотрения не настроен для гильдии {interaction.guild.id}")
                    else:
                        review_channel = interaction.guild.get_channel(review_channel_id)
                        if not review_channel:
                            logger.warning(f"Канал рассмотрения {review_channel_id} не найден для гильдии {interaction.guild.id}")
                        else:
                            # Проверяем права бота
                            perms = review_channel.permissions_for(interaction.guild.me)
                            logger.debug(f"Права бота в канале {review_channel_id}: send_messages={perms.send_messages}, manage_messages={perms.manage_messages}, add_reactions={perms.add_reactions}")

                            if not (perms.send_messages and perms.manage_messages):
                                logger.error(
                                    f"Нет прав для редактирования сообщений в канале {review_channel_id} гильдии {interaction.guild.id}"
                                )
                            else:
                                try:
                                    review_message = await review_channel.fetch_message(review_message_id)
                                    await review_message.edit(view=None)  # Убираем кнопки

                                    # Добавляем реакцию ❌
                                    try:
                                        await review_message.add_reaction('❌')
                                    except discord.Forbidden:
                                        logger.warning(
                                            f"Нет прав на добавление реакций в канале {review_channel_id} гильдии {interaction.guild.id}"
                                        )
                                    except discord.HTTPException as e:
                                        logger.error(f"HTTP ошибка при добавлении реакции к сообщению {review_message_id}: {e}")
                                    except Exception as e:
                                        logger.error(f"Неожиданная ошибка при добавлении реакции ❌ к сообщению {review_message_id}: {e}")
                                except discord.NotFound:
                                    logger.warning(f"Сообщение с ID {review_message_id} не найдено в гильдии {interaction.guild.id}")
                                except discord.Forbidden:
                                    logger.error(
                                        f"Нет доступа к сообщению {review_message_id} в канале {review_channel_id} гильдии {interaction.guild.id}"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"Ошибка при редактировании сообщения {review_message_id} "
                                        f"в гильдии {interaction.guild.id}: {e}"
                                    )
                except Exception as e:
                    logger.error(f"Общая ошибка при редактировании сообщения: {e}", exc_info=True)
            else:
                logger.warning(f"review_message_id отсутствует для заявки {applicant_id}, пропуск редактирования сообщения")

            # Убираем view из текущего сообщения взаимодействия
            logger.debug("Попытка убрать view из текущего сообщения взаимодействия")
            try:
                await interaction.message.edit(view=None)
            except Exception as e:
                logger.warning(f"Не удалось отредактировать сообщение взаимодействия: {e}")

            # Используем followup для финального ответа
            logger.debug("Отправка финального подтверждения администратору")
            await interaction.followup.send(
                f"❌ Заявка пользователя {self.applicant.mention} отклонена!",
                ephemeral=True
            )
        except Exception as e:
            logger.critical(
                f"Критическая ошибка при отклонении заявки пользователя {self.applicant.id}: {e}",
                exc_info=True
            )

            # Логируем полный стек ошибки, если включён DEBUG
            if logger.level <= logging.DEBUG:
                import traceback
                traceback_str = ''.join(traceback.format_tb(e.__traceback__))
                logger.debug(f"Полный стек ошибки:\n{traceback_str}")

            try:
                await interaction.followup.send(
                    "❌ Произошла критическая ошибка при обработке заявки. "
                    "Пожалуйста, обратитесь к администратору для уточнения деталей.",
                    ephemeral=True
                )
            except discord.Forbidden:
                logger.warning("Не удалось отправить сообщение об ошибке пользователю — у бота нет прав.")
            except discord.HTTPException as http_error:
                logger.error(f"Ошибка HTTP при отправке сообщения об ошибке: {http_error}")
            except Exception as followup_error:
                logger.error(f"Не удалось отправить followup-сообщение: {followup_error}")

            # Финальный лог — ошибка
            logger.error(
                f"Обработка команды on_submit завершена с ошибкой для пользователя {self.applicant.id}"
            )
        finally:
            # Код, который выполняется всегда (опционально)
            pass  # Здесь можно добавить очистку ресурсов, если нужно


class ApplicationButtonView(ui.View):
    def __init__(self, cog: FamilyApplications):
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(label="Подать заявку", style=discord.ButtonStyle.primary, emoji="👤", custom_id="application_button")
    async def apply_button(self, interaction: discord.Interaction, button: ui.Button):
        user = interaction.user
        guild = interaction.guild

        # Проверка в БД: есть ли активные заявки у пользователя (защита от гонки условий)
        try:
            existing_app = await fetch_one(
                "SELECT id FROM family_applications "
                "WHERE user_id = %s AND guild_id = %s AND status = 'pending'",
                (user.id, guild.id)
            )

            if existing_app:
                await interaction.response.send_message(
                    "❌ У вас уже есть ожидающая заявка в этой гильдии!",
                    ephemeral=True
                )
                return
            
            # Дополнительная проверка: была ли недавно отклонена заявка (cooldown 24 часа)
            rejected_app = await fetch_one(
                "SELECT reviewed_at FROM family_applications "
                "WHERE user_id = %s AND guild_id = %s AND status = 'rejected' "
                "ORDER BY reviewed_at DESC LIMIT 1",
                (user.id, guild.id)
            )
            
            if rejected_app and rejected_app.get('reviewed_at'):
                from datetime import timedelta
                time_since_rejection = datetime.now() - rejected_app['reviewed_at']
                if time_since_rejection < timedelta(hours=24):
                    hours_left = int((timedelta(hours=24) - time_since_rejection).total_seconds() / 3600) + 1
                    await interaction.response.send_message(
                        f"❌ Ваша последняя заявка была отклонена. Вы можете подать новую через {hours_left} ч.",
                        ephemeral=True
                    )
                    return
        except Exception as e:
            logger.error(f"Ошибка проверки существующих заявок: {e}")
            await interaction.response.send_message(
                "❌ Произошла ошибка при проверке вашей заявки. Попробуйте позже.",
                ephemeral=True
            )
            return

        application_data = {
            'user_id': user.id,
            'guild_id': guild.id,
            'timestamp': datetime.now(),
            'status': 'pending'
        }
        self.cog.pending_applications[user.id] = application_data

        modal = ApplicationModal(self.cog, user)  # Передаём cog
        await interaction.response.send_modal(modal)

class ApplicationModal(ui.Modal, title="Заявка в семью"):
    def __init__(self, cog: FamilyApplications, user: discord.Member):
        super().__init__()
        self.cog = cog  # Добавляем ссылку на Cog
        self.user = user

    reason = ui.TextInput(
        label="Почему вы хотите вступить в нашу семью?",
        style=discord.TextStyle.paragraph,
        placeholder="Расскажите о себе и своих намерениях...",
        required=True,
        max_length=1000
    )

    experience = ui.TextInput(
        label="Ваш игровой опыт",
        style=discord.TextStyle.paragraph,
        placeholder="Опишите свой опыт в играх/сервере...",
        required=False,
        max_length=500
    )

    availability = ui.TextInput(
        label="Ваша доступность",
        style=discord.TextStyle.short,
        placeholder="Например: 3–4 часа в день, вечер/утро...",
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Сначала закрываем модальное окно с помощью defer
            await interaction.response.defer(ephemeral=True)
            
            # Обработка необязательного поля experience
            experience_value = self.experience.value if self.experience.value else "Не указан"
            
            # Выполняем запрос к БД для сохранения заявки
            await execute_query(
                """INSERT INTO family_applications
                (user_id, guild_id, reason, experience, availability, status, submitted_at)
                VALUES (%s, %s, %s, %s, %s, 'pending', %s)""",
                (self.user.id, self.user.guild.id, self.reason.value,
                experience_value, self.availability.value, datetime.now())
            )

            # Создаём полную структуру данных заявки
            application_data = {
                'user_id': self.user.id,
                'guild_id': self.user.guild.id,
                'reason': self.reason.value,
                'experience': experience_value,
                'availability': self.availability.value,
                'timestamp': datetime.now(),
                'status': 'pending'
            }

            # Добавляем заявку в кэш *только после* успешного сохранения в БД
            self.cog.pending_applications[self.user.id] = application_data

            # Уведомляем администраторов о новой заявке (внутри try, чтобы ошибка не ломала ответ пользователю)
            try:
                await self.cog._notify_admins(interaction.guild, application_data)
            except Exception as notify_error:
                logger.error(f"Ошибка при уведомлении админов: {notify_error}")
                # Не прерываем выполнение, просто логируем ошибку

            # Отправляем подтверждение пользователю через followup
            await interaction.followup.send(
                "✅ Ваша заявка успешно отправлена! Администрация рассмотрит её в ближайшее время.",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Ошибка при обработке заявки: {e}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(
                        "❌ Произошла ошибка при отправке заявки. Попробуйте позже.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(
                        "❌ Произошла ошибка при отправке заявки. Попробуйте позже.",
                        ephemeral=True
                    )
            except:
                pass


class ApplicationReviewView(ui.View):
    def __init__(self, cog: FamilyApplications, applicant_id: int, review_channel_id: Optional[int]):
        super().__init__(timeout=None)
        self.cog = cog  # Сохраняем ссылку на Cog
        self.applicant_id = applicant_id
        self.review_channel_id = review_channel_id

    @ui.button(label="Вызвать на обзвон", style=discord.ButtonStyle.blurple, emoji="📞")
    async def call_for_interview(self, interaction: discord.Interaction, button: ui.Button):
        applicant = interaction.guild.get_member(self.applicant_id)
        if not applicant:
            await interaction.response.send_message("❌ Пользователь не найден.", ephemeral=True)
            return

        try:
            await applicant.send("Вы приглашены на обзвон по заявке. Вам необходимо зайти в канал ожидания.")
            await self.cog._add_audit_reaction(
                interaction,
                '📞',
                applicant,
                "Вызван на обзвон"
            )
            await interaction.response.send_message(
                f"📞 Пользователь {applicant.mention} вызван на обзвон!",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения пользователю {self.applicant_id}: {e}")
            await interaction.response.send_message("❌ Ошибка при отправке сообщения.", ephemeral=True)

    @ui.button(label="Принять", style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: ui.Button):
        # Шаг 1: сразу отправляем defer, чтобы продлить срок interaction
        await interaction.response.defer(ephemeral=True)
        applicant = interaction.guild.get_member(self.applicant_id)
        if not applicant:
            await interaction.followup.send("❌ Пользователь не найден.", ephemeral=True)
            return
        try:
            # Получаем настройки гильдии
            settings = await self.cog._get_family_settings(interaction.guild.id)
            accepted_role_id = settings.get('accepted_role_id')
            # Обновляем статус заявки в БД
            await execute_query(
                "UPDATE family_applications SET status = 'accepted', reviewed_at = %s WHERE user_id = %s AND guild_id = %s",
                (datetime.now(), self.applicant_id, interaction.guild.id)
            )
            # Удаляем заявку из кэша
            if self.applicant_id in self.cog.pending_applications:
                del self.cog.pending_applications[self.applicant_id]
            # Выдаём роль, если ID роли задан и роль существует
            if accepted_role_id:
                accepted_role = interaction.guild.get_role(accepted_role_id)
                if accepted_role:
                    await applicant.add_roles(accepted_role)
            # Отправляем уведомление пользователю
            embed = discord.Embed(
                title="🎉 Поздравляем!",
                color=0x00ff00,
                description="Ваша заявка в семью была **принята**!"
            )
            await applicant.send(embed=embed)
            # Логируем действие в канале аудита
            await self.cog._add_audit_reaction(
                interaction,
                '✅',
                applicant,
                "Заявка принята"
            )
            # Отключаем кнопки в оригинальном сообщении
            await interaction.message.edit(view=None)
            try:
                await interaction.message.add_reaction('✅')
            except Exception as e:
                logger.error(f"Ошибка при добавлении реакции ✅ к сообщению заявки {self.applicant_id}: {e}")
            # Шаг 2: используем followup вместо response для финального ответа
            await interaction.followup.send(
                f"✅ Заявка пользователя {applicant.mention} принята!",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Ошибка при принятии заявки {self.applicant_id}: {e}")
            # Шаг 3: в except тоже используем followup
            await interaction.followup.send("❌ Ошибка при обработке заявки.", ephemeral=True)

    @ui.button(label="Отклонить", style=discord.ButtonStyle.danger, emoji="❌")
    async def reject_button(self, interaction: discord.Interaction, button: ui.Button):
        applicant = interaction.guild.get_member(self.applicant_id)
        if not applicant:
            await interaction.response.send_message("❌ Пользователь не найден.", ephemeral=True)
            return
        # Проверяем права бота на отправку модальных окон
        perms = interaction.channel.permissions_for(interaction.guild.me)
        if not perms.send_messages:
            logger.warning(f"Нет прав на отправку сообщений в канале {interaction.channel.id}")
            await interaction.response.send_message(
                "❌ У бота нет прав для открытия окна отклонения заявки.",
                ephemeral=True
            )
            return
        try:
            modal = RejectionReasonModal(self.cog, applicant)
            await interaction.response.send_modal(modal)
        except discord.Forbidden as e:
            logger.error(f"Бот не имеет прав для отправки модального окна в гильдии {interaction.guild.id}: {e}")
            await interaction.response.send_message(
                "❌ У бота недостаточно прав для выполнения этой операции.",
                ephemeral=True
            )
        except discord.HTTPException as e:
            logger.error(f"HTTP ошибка при отправке модального окна для заявки {self.applicant_id}: {e}")
            await interaction.response.send_message(
                "❌ Произошла ошибка при открытии окна отклонения заявки (HTTP).",
                ephemeral=True
            )
        except Exception as e:
            logger.critical(f"Критическая ошибка при открытии модального окна для {self.applicant_id}: {e}", exc_info=True)
            await interaction.response.send_message(
                "❌ Критическая ошибка при обработке заявки. Обратитесь к администратору.",
                ephemeral=True
            )
            
    def cog_unload(self):
        """Очистка кэша при выгрузке кога."""
        self.pending_applications.clear()
        self.family_settings_cache.clear()
        logger.info("Выгрузка кога 'FamilyApplications', очистка кэша...")


async def setup(bot):
    cog = FamilyApplications(bot)
    await bot.add_cog(cog)
    # Ждём полной инициализации
    await asyncio.sleep(1)
    # Регистрируем View для persistent кнопок
    bot.add_view(ApplicationButtonView(cog))
    # Дополнительная проверка регистрации View
    if not bot.persistent_views:
        logger.warning("Persistent View не зарегистрирован, повторная попытка...")
        bot.add_view(ApplicationButtonView(cog))