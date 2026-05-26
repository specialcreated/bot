import discord
from discord.ext import commands
from discord.ui import View, Select, Button, Modal, TextInput
import logging

logger = logging.getLogger(__name__)


# ==================== Select Menu для выбора категории ====================

class CategorySelect(Select):
    """Select-меню для выбора категории управления."""
    
    def __init__(self, menu_cog: 'MenuCog'):
        options = [
            discord.SelectOption(
                label="Администрирование",
                description="setwelcome, sendmessage",
                emoji="🔧",
                value="admin"
            ),
            discord.SelectOption(
                label="Розыгрыши",
                description="Создать, завершить, отменить розыгрыш",
                emoji="🎁",
                value="giveaway"
            ),
            discord.SelectOption(
                label="Фильтр мата",
                description="Добавить слово, включить/выключить",
                emoji="🚫",
                value="profanity"
            )
        ]
        
        super().__init__(
            placeholder="Выберите категорию управления...",
            min_values=1,
            max_values=1,
            options=options
        )
        self.menu_cog = menu_cog
    
    async def callback(self, interaction: discord.Interaction):
        selected_category = self.values[0]
        
        # Отправляем ephemeral сообщение с кнопками выбранной категории
        view = CategoryActionsView(self.menu_cog, selected_category)
        
        category_names = {
            "admin": "🔧 Администрирование",
            "giveaway": "🎁 Розыгрыши",
            "profanity": "🚫 Фильтр мата"
        }
        
        embed = discord.Embed(
            title=f"{category_names.get(selected_category, 'Категория')}",
            description="Выберите действие:",
            color=discord.Color.blue()
        )
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ==================== View с кнопками действий для каждой категории ====================

class CategoryActionsView(View):
    """View с кнопками действий для выбранной категории."""
    
    def __init__(self, menu_cog: 'MenuCog', category: str):
        super().__init__(timeout=None)
        self.menu_cog = menu_cog
        self.category = category
        
        # Добавляем кнопки в зависимости от категории
        if category == "admin":
            self._add_admin_buttons()
        elif category == "giveaway":
            self._add_giveaway_buttons()
        elif category == "profanity":
            self._add_profanity_buttons()
    
    def _add_admin_buttons(self):
        """Кнопки администрирования."""
        # SetWelcome
        btn_welcome = Button(
            label="Канал приветствий",
            style=discord.ButtonStyle.primary,
            emoji="👋",
            custom_id="menu_admin_setwelcome"
        )
        btn_welcome.callback = self.callback_admin_setwelcome
        self.add_item(btn_welcome)
        
        # SetGoodbye
        btn_goodbye = Button(
            label="Канал прощаний",
            style=discord.ButtonStyle.primary,
            emoji="👋",
            custom_id="menu_admin_setgoodbye"
        )
        btn_goodbye.callback = self.callback_admin_setgoodbye
        self.add_item(btn_goodbye)
        
        # SendMessage
        btn_send = Button(
            label="Отправить сообщение",
            style=discord.ButtonStyle.success,
            emoji="📩",
            custom_id="menu_admin_sendmessage"
        )
        btn_send.callback = self.callback_admin_sendmessage
        self.add_item(btn_send)
    
    def _add_giveaway_buttons(self):
        """Кнопки розыгрышей."""
        # Create
        btn_create = Button(
            label="Создать розыгрыш",
            style=discord.ButtonStyle.success,
            emoji="🏆",
            custom_id="menu_giveaway_create"
        )
        btn_create.callback = self.callback_giveaway_create
        self.add_item(btn_create)
        
        # End
        btn_end = Button(
            label="Завершить розыгрыш",
            style=discord.ButtonStyle.primary,
            emoji="✅",
            custom_id="menu_giveaway_end"
        )
        btn_end.callback = self.callback_giveaway_end
        self.add_item(btn_end)
        
        # Cancel
        btn_cancel = Button(
            label="Отменить розыгрыш",
            style=discord.ButtonStyle.danger,
            emoji="❌",
            custom_id="menu_giveaway_cancel"
        )
        btn_cancel.callback = self.callback_giveaway_cancel
        self.add_item(btn_cancel)
    
    def _add_profanity_buttons(self):
        """Кнопки фильтра мата."""
        # Add word
        btn_add = Button(
            label="Добавить слово",
            style=discord.ButtonStyle.success,
            emoji="➕",
            custom_id="menu_profanity_add"
        )
        btn_add.callback = self.callback_profanity_add
        self.add_item(btn_add)
        
        # Toggle
        btn_toggle = Button(
            label="Вкл/Выкл фильтр",
            style=discord.ButtonStyle.primary,
            emoji="🔄",
            custom_id="menu_profanity_toggle"
        )
        btn_toggle.callback = self.callback_profanity_toggle
        self.add_item(btn_toggle)


# ==================== Callback функции для кнопок ====================

    async def callback_admin_setwelcome(self, interaction: discord.Interaction):
        """Обработчик кнопки установки канала приветствий."""
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title="❌ Недостаточно прав",
                description="Вам нужны права `administrator` для использования этой функции.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        modal = SetWelcomeModal(self.menu_cog)
        await interaction.response.send_modal(modal)
    
    async def callback_admin_sendmessage(self, interaction: discord.Interaction):
        """Обработчик кнопки отправки сообщения."""
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title="❌ Недостаточно прав",
                description="Вам нужны права `administrator` для использования этой функции.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        modal = SendMessageModal(self.menu_cog)
        await interaction.response.send_modal(modal)
    
    async def callback_admin_setgoodbye(self, interaction: discord.Interaction):
        """Обработчик кнопки установки канала прощаний."""
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title="❌ Недостаточно прав",
                description="Вам нужны права `administrator` для использования этой функции.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        modal = SetGoodbyeModal(self.menu_cog)
        await interaction.response.send_modal(modal)
    
    async def callback_giveaway_create(self, interaction: discord.Interaction):
        """Обработчик кнопки создания розыгрыша."""
        giveaway_cog = self.menu_cog.bot.get_cog('Giveaway')
        if not giveaway_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль розыгрышей не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Открываем главное меню розыгрышей из модуля giveaway
        view = giveaway_cog.GiveawayMainView(giveaway_cog, interaction.guild)
        embed = discord.Embed(
            title="🏆 Управление розыгрышами",
            description="Выберите действие:",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def callback_giveaway_end(self, interaction: discord.Interaction):
        """Обработчик кнопки завершения розыгрыша."""
        giveaway_cog = self.menu_cog.bot.get_cog('Giveaway')
        if not giveaway_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль розыгрышей не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Получаем активные розыгрыши и показываем Select
        db = await giveaway_cog.get_db_module()
        active_giveaways = await db.get_active_giveaways(interaction.guild.id)
        
        if not active_giveaways:
            embed = discord.Embed(
                title="ℹ️ Нет активных розыгрышей",
                description="На этом сервере нет активных розыгрышей.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        options = []
        for gw in active_giveaways[:25]:  # Максимум 25 опций
            options.append(discord.SelectOption(
                label=f"Розыгрыш #{gw['id']}",
                description=gw['prize'][:50],
                value=str(gw['id'])
            ))
        
        view = giveaway_cog.GiveawayEndSelectView(giveaway_cog, options)
        embed = discord.Embed(
            title="✅ Завершение розыгрыша",
            description="Выберите розыгрыш для завершения:",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def callback_giveaway_cancel(self, interaction: discord.Interaction):
        """Обработчик кнопки отмены розыгрыша."""
        giveaway_cog = self.menu_cog.bot.get_cog('Giveaway')
        if not giveaway_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль розыгрышей не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        db = await giveaway_cog.get_db_module()
        active_giveaways = await db.get_active_giveaways(interaction.guild.id)
        
        if not active_giveaways:
            embed = discord.Embed(
                title="ℹ️ Нет активных розыгрышей",
                description="На этом сервере нет активных розыгрышей.",
                color=discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        options = []
        for gw in active_giveaways[:25]:
            options.append(discord.SelectOption(
                label=f"Розыгрыш #{gw['id']}",
                description=gw['prize'][:50],
                value=str(gw['id'])
            ))
        
        view = giveaway_cog.GiveawayCancelSelectView(giveaway_cog, options)
        embed = discord.Embed(
            title="❌ Отмена розыгрыша",
            description="Выберите розыгрыш для отмены:",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def callback_profanity_add(self, interaction: discord.Interaction):
        """Обработчик кнопки добавления запрещённого слова."""
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title="❌ Недостаточно прав",
                description="Вам нужны права `administrator` для использования этой функции.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        modal = AddBannedWordModal(self.menu_cog)
        await interaction.response.send_modal(modal)
    
    async def callback_profanity_toggle(self, interaction: discord.Interaction):
        """Обработчик кнопки включения/выключения фильтра."""
        if not interaction.user.guild_permissions.administrator:
            embed = discord.Embed(
                title="❌ Недостаточно прав",
                description="Вам нужны права `administrator` для использования этой функции.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        profanity_cog = self.menu_cog.bot.get_cog('ProfanityFilter')
        if not profanity_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль фильтра мата не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Переключаем статус
        guild_id = interaction.guild.id
        await profanity_cog._ensure_guild_loaded(guild_id)
        
        from database.mysql_connector import toggle_profanity_filter
        is_enabled = profanity_cog.filter_enabled.get(guild_id, True)
        new_status = not is_enabled
        success = await toggle_profanity_filter(guild_id, new_status, interaction.user.id)
        
        if success:
            profanity_cog.filter_enabled[guild_id] = new_status
            status_text = "включён" if new_status else "выключен"
            emoji = "✅" if new_status else "⚠️"
            embed = discord.Embed(
                title=f"{emoji} Фильтр мата",
                description=f"Фильтр мата **{status_text}** для всего сервера!",
                color=discord.Color.green() if new_status else discord.Color.orange()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Не удалось обновить настройки фильтра.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== Модальные окна ====================

class SetWelcomeModal(Modal, title="👋 Канал приветствий"):
    def __init__(self, menu_cog: 'MenuCog'):
        super().__init__()
        self.menu_cog = menu_cog
        self.add_item(TextInput(
            label="ID канала или упоминание",
            placeholder="#general или 123456789",
            min_length=1,
            max_length=100
        ))
    
    async def on_submit(self, interaction: discord.Interaction):
        user_input = self.children[0].value.strip()
        channel = None
        
        # Пробуем найти по упоминанию
        if user_input.startswith("<#") and user_input.endswith(">"):
            channel_id = int(user_input.strip("<#>"))
            channel = interaction.guild.get_channel(channel_id)
        else:
            # Пробуем по ID
            try:
                channel_id = int(user_input)
                channel = interaction.guild.get_channel(channel_id)
            except ValueError:
                pass
        
        if not channel or not isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Канал не найден! Укажите корректный ID или упоминание текстового канала.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        admin_cog = self.menu_cog.bot.get_cog('Admin')
        if not admin_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль администрирования не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        success = await admin_cog.save_channel_to_db(interaction.guild.id, "welcome_channel_id", channel.id)
        
        if success:
            embed = discord.Embed(
                title="✅ Канал приветствий установлен",
                description=f"Новые участники будут приветствоваться в {channel.mention}",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Не удалось сохранить настройки в базе данных.",
                color=discord.Color.red()
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SendMessageModal(Modal, title="📩 Отправка сообщения"):
    def __init__(self, menu_cog: 'MenuCog'):
        super().__init__()
        self.menu_cog = menu_cog
        self.add_item(TextInput(
            label="ID канала или упоминание",
            placeholder="#general или 123456789",
            min_length=1,
            max_length=100
        ))
        self.add_item(TextInput(
            label="Текст сообщения",
            placeholder="Введите текст сообщения...",
            min_length=1,
            max_length=2000,
            style=discord.TextStyle.paragraph
        ))
        self.add_item(TextInput(
            label="Ссылка на изображение (необязательно)",
            placeholder="https://example.com/image.jpg",
            min_length=0,
            max_length=500,
            required=False,
            style=discord.TextStyle.short
        ))
    
    async def on_submit(self, interaction: discord.Interaction):
        channel_input = self.children[0].value.strip()
        message_content = self.children[1].value.strip()
        image_url = self.children[2].value.strip()
        
        channel = None
        
        if channel_input.startswith("<#") and channel_input.endswith(">"):
            channel_id = int(channel_input.strip("<#>"))
            channel = interaction.guild.get_channel(channel_id)
        else:
            try:
                channel_id = int(channel_input)
                channel = interaction.guild.get_channel(channel_id)
            except ValueError:
                pass
        
        if not channel or not isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Канал не найден!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        admin_cog = self.menu_cog.bot.get_cog('Admin')
        if not admin_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль администрирования не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        try:
            # Формируем сообщение с изображением или без
            if image_url:
                embed = discord.Embed(description=message_content, color=discord.Color.blue())
                embed.set_image(url=image_url)
                await channel.send(embed=embed)
            else:
                await channel.send(content=message_content)
            
            embed = discord.Embed(
                title="✅ Сообщение отправлено",
                description=f"Сообщение успешно отправлено в {channel.mention}",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.Forbidden:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="У бота нет прав для отправки сообщений в этот канал!",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Не удалось отправить сообщение: {str(e)}",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


class SetGoodbyeModal(Modal, title="👋 Канал прощаний"):
    def __init__(self, menu_cog: 'MenuCog'):
        super().__init__()
        self.menu_cog = menu_cog
        self.add_item(TextInput(
            label="ID канала или упоминание",
            placeholder="#general или 123456789",
            min_length=1,
            max_length=100
        ))
    
    async def on_submit(self, interaction: discord.Interaction):
        user_input = self.children[0].value.strip()
        channel = None
        
        # Пробуем найти по упоминанию
        if user_input.startswith("<#") and user_input.endswith(">"):
            channel_id = int(user_input.strip("<#>"))
            channel = interaction.guild.get_channel(channel_id)
        else:
            # Пробуем по ID
            try:
                channel_id = int(user_input)
                channel = interaction.guild.get_channel(channel_id)
            except ValueError:
                pass
        
        if not channel or not isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Канал не найден! Укажите корректный ID или упоминание текстового канала.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        admin_cog = self.menu_cog.bot.get_cog('Admin')
        if not admin_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль администрирования не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        success = await admin_cog.save_channel_to_db(interaction.guild.id, "goodbye_channel_id", channel.id)
        
        if success:
            embed = discord.Embed(
                title="✅ Канал прощаний установлен",
                description=f"Сообщения о выходе участников будут отправляться в {channel.mention}",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Не удалось сохранить настройки в базе данных.",
                color=discord.Color.red()
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


class AddBannedWordModal(Modal, title="🚫 Добавить запрещённое слово"):
    def __init__(self, menu_cog: 'MenuCog'):
        super().__init__()
        self.menu_cog = menu_cog
        self.add_item(TextInput(
            label="Запрещённое слово",
            placeholder="Введите слово...",
            min_length=1,
            max_length=100
        ))
    
    async def on_submit(self, interaction: discord.Interaction):
        word = self.children[0].value.strip().lower()
        
        profanity_cog = self.menu_cog.bot.get_cog('ProfanityFilter')
        if not profanity_cog:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Модуль фильтра мата не загружен.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        await profanity_cog._ensure_guild_loaded(guild_id)
        
        if word in profanity_cog.banned_words.get(guild_id, set()):
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Слово '{word}' уже в списке запрещённых.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        from database.mysql_connector import add_profanity_word
        success = await add_profanity_word(guild_id, word)
        
        if success:
            if guild_id not in profanity_cog.banned_words:
                profanity_cog.banned_words[guild_id] = set()
            profanity_cog.banned_words[guild_id].add(word)
            embed = discord.Embed(
                title="✅ Слово добавлено",
                description=f"Слово '{word}' добавлено в список запрещённых.",
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Не удалось добавить слово в базу данных.",
                color=discord.Color.red()
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== Главное меню ====================

class MainMenuView(View):
    """Главное меню управления ботом."""
    
    def __init__(self, menu_cog: 'MenuCog'):
        super().__init__(timeout=None)
        self.menu_cog = menu_cog
        self.add_item(CategorySelect(menu_cog))


# ==================== Cog главного меню ====================

class MenuCog(commands.Cog):
    """Ког для главного меню управления."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @commands.command(name='menu', aliases=['меню'])
    async def show_menu(self, ctx):
        """Показать главное меню управления."""
        embed = discord.Embed(
            title="🎛️ Главное меню управления",
            description="Выберите категорию для управления функциями бота:",
            color=discord.Color.blue()
        )
        embed.set_footer(text="Используйте Select-меню ниже для выбора категории")
        
        view = MainMenuView(self)
        await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(MenuCog(bot))
