import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Modal, TextInput
import logging
import os
from datetime import datetime
from typing import Optional

# Загрузка переменных окружения
from dotenv import load_dotenv
load_dotenv()

# =====================[ КОНФИГУРАЦИЯ ] =====================
# Вынеси в начало кода переменные для конфигурации
TOKEN = os.getenv('DISCORD_TOKEN')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_USER = os.getenv('DB_USER')
DB_PASSWORD = os.getenv('DB_PASSWORD')
DB_NAME = os.getenv('DB_NAME')
MOD_CHANNEL_ID = int(os.getenv('MOD_CHANNEL_ID', 0))  # ID канала модерации
NEWS_CHANNEL_ID = int(os.getenv('NEWS_CHANNEL_ID', 0))  # ID канала публикаций
MODERATOR_ROLE_ID = int(os.getenv('MODERATOR_ROLE_ID', 0))  # ID роли модератора
EVERYONE_TAG = os.getenv('EVERYONE_TAG', '@everyone')  # Тег для уведомления (настраиваемый)

logger = logging.getLogger(__name__)


class NewsSubmitModal(Modal, title="📰 Подать новость"):
    """Модальное окно для подачи новости"""
    
    def __init__(self, content: str = ""):
        super().__init__()
        self.news_content = TextInput(
            label="Текст новости",
            style=discord.TextStyle.paragraph,
            placeholder="Введите текст вашей новости...",
            min_length=10,
            max_length=2000,
            default=content
        )
        self.add_item(self.news_content)
    
    async def on_submit(self, interaction: discord.Interaction):
        """Обработка отправки модального окна"""
        from database.mysql_connector import create_news_request
        
        news_text = self.news_content.value
        
        # Проверяем наличие вложения (картинки)
        image_url = None
        if hasattr(interaction.message, 'attachments') and interaction.message.attachments:
            attachment = interaction.message.attachments[0]
            if attachment.content_type and attachment.content_type.startswith('image/'):
                image_url = attachment.url
        
        # Сохраняем заявку в БД
        request_id = await create_news_request(
            author_id=interaction.user.id,
            content=news_text,
            image_url=image_url
        )
        
        if request_id:
            embed = discord.Embed(
                title="✅ Заявка отправлена",
                description="Ваша новость отправлена на модерацию.",
                color=discord.Color.green()
            )
            embed.add_field(name="ID заявки", value=f"`{request_id}`", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Отправляем уведомление в канал модерации
            await send_to_moderation_channel(
                request_id=request_id,
                author=interaction.user,
                content=news_text,
                image_url=image_url,
                bot=interaction.client
            )
        else:
            embed = discord.Embed(
                title="❌ Ошибка",
                description="Не удалось сохранить заявку. Попробуйте позже.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
    
    async def on_error(self, interaction: discord.Interaction, error: Exception):
        """Обработка ошибок в модальном окне"""
        logger.error(f"Ошибка в модальном окне новости: {error}")
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Произошла ошибка при отправке новости.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class ModerationView(View):
    """Кнопки модерации для заявок"""
    
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.request_id = request_id
    
    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green, emoji="✅", custom_id="news_approve")
    async def approve_button(self, interaction: discord.Interaction, button: Button):
        """Обработка кнопки одобрения"""
        from database.mysql_connector import update_news_status, get_news_request
        
        # Проверка роли модератора
        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав модератора!", 
                ephemeral=True
            )
            return
        
        # Получаем данные заявки
        news_data = await get_news_request(self.request_id)
        if not news_data:
            await interaction.response.send_message(
                "❌ Заявка не найдена!", 
                ephemeral=True
            )
            return
        
        # Обновляем статус в БД
        success = await update_news_status(
            request_id=self.request_id,
            status='approved',
            moderator_id=interaction.user.id
        )
        
        if success:
            # Обновляем сообщение в канале модерации
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.set_footer(text=f"✓ Одобрено | Модератор: {interaction.user.name}")
            
            # Удаляем кнопки
            for item in interaction.message.components:
                for comp in item.children:
                    comp.disabled = True
            
            await interaction.message.edit(embed=embed, view=None)
            
            # Отправляем новость в канал публикаций
            await publish_news(
                content=news_data['content'],
                image_url=news_data['image_url'],
                channel_id=NEWS_CHANNEL_ID,
                bot=interaction.client
            )
            
            # Уведомляем автора в ЛС
            await notify_author(
                author_id=news_data['author_id'],
                status='approved',
                moderator_name=interaction.user.name,
                bot=interaction.client
            )
            
            logger.info(
                f"✅ Новость #{self.request_id} одобрена модератором {interaction.user.name}"
            )
            
            await interaction.response.send_message(
                "✅ Новость одобрена и опубликована!", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Не удалось обновить статус заявки!", 
                ephemeral=True
            )
    
    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.red, emoji="❌", custom_id="news_reject")
    async def reject_button(self, interaction: discord.Interaction, button: Button):
        """Обработка кнопки отклонения"""
        from database.mysql_connector import update_news_status, get_news_request
        
        # Проверка роли модератора
        if not is_moderator(interaction.user):
            await interaction.response.send_message(
                "❌ У вас нет прав модератора!", 
                ephemeral=True
            )
            return
        
        # Получаем данные заявки
        news_data = await get_news_request(self.request_id)
        if not news_data:
            await interaction.response.send_message(
                "❌ Заявка не найдена!", 
                ephemeral=True
            )
            return
        
        # Обновляем статус в БД
        success = await update_news_status(
            request_id=self.request_id,
            status='rejected',
            moderator_id=interaction.user.id
        )
        
        if success:
            # Обновляем сообщение в канале модерации
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.set_footer(text=f"✗ Отклонено | Модератор: {interaction.user.name}")
            
            # Удаляем кнопки
            for item in interaction.message.components:
                for comp in item.children:
                    comp.disabled = True
            
            await interaction.message.edit(embed=embed, view=None)
            
            # Уведомляем автора в ЛС
            await notify_author(
                author_id=news_data['author_id'],
                status='rejected',
                moderator_name=interaction.user.name,
                bot=interaction.client
            )
            
            logger.info(
                f"❌ Новость #{self.request_id} отклонена модератором {interaction.user.name}"
            )
            
            await interaction.response.send_message(
                "❌ Новость отклонена!", 
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Не удалось обновить статус заявки!", 
                ephemeral=True
            )


def is_moderator(user: discord.Member) -> bool:
    """Проверяет, есть ли у пользователя роль модератора"""
    if isinstance(user, discord.User):
        return False
    return any(role.id == MODERATOR_ROLE_ID for role in user.roles)


async def send_to_moderation_channel(
    request_id: int,
    author: discord.User,
    content: str,
    image_url: Optional[str],
    bot: commands.Bot
):
    """Отправляет заявку в канал модерации"""
    if MOD_CHANNEL_ID == 0:
        logger.error("MOD_CHANNEL_ID не настроен!")
        return
    
    try:
        mod_channel = bot.get_channel(MOD_CHANNEL_ID)
        if not mod_channel:
            mod_channel = await bot.fetch_channel(MOD_CHANNEL_ID)
        
        # Создаём Embed с информацией о новости
        embed = discord.Embed(
            title="📰 Новая заявка на публикацию",
            description=content[:4000],  # Ограничение Discord
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Автор", value=f"{author.mention} (`{author.id}`)", inline=True)
        embed.add_field(name="ID заявки", value=f"`{request_id}`", inline=True)
        embed.set_thumbnail(url=author.display_avatar.url)
        
        # Добавляем картинку если есть
        if image_url:
            embed.set_image(url=image_url)
        
        # Создаём кнопки модерации
        view = ModerationView(request_id=request_id)
        
        # Отправляем сообщение в канал модерации
        message = await mod_channel.send(embed=embed, view=view)
        
        # Сохраняем ID сообщения в БД
        from database.mysql_connector import update_news_channel_message_id
        await update_news_channel_message_id(request_id, message.id)
        
        logger.info(f"Заявка #{request_id} отправлена в канал модерации")
        
    except Exception as e:
        logger.error(f"Ошибка отправки в канал модерации: {e}")


async def publish_news(
    content: str,
    image_url: Optional[str],
    channel_id: int,
    bot: commands.Bot
):
    """Публикует новость в канале публикаций"""
    if channel_id == 0:
        logger.error("NEWS_CHANNEL_ID не настроен!")
        return
    
    try:
        news_channel = bot.get_channel(channel_id)
        if not news_channel:
            news_channel = await bot.fetch_channel(channel_id)
        
        # Формируем сообщение с тегом уведомления
        full_content = f"{EVERYONE_TAG}\n\n{content}"
        
        # Отправляем сообщение
        if image_url:
            await news_channel.send(content=full_content)
            # Отдельно отправляем картинку (Discord не позволяет embed + картинка в одном сообщении так просто)
            await news_channel.send(file=discord.File(await download_image(image_url), filename="news_image.png"))
        else:
            await news_channel.send(content=full_content)
        
        logger.info(f"Новость опубликована в канале {channel_id}")
        
    except Exception as e:
        logger.error(f"Ошибка публикации новости: {e}")


async def download_image(url: str):
    """Скачивает изображение по URL"""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.read()
    return None


async def notify_author(
    author_id: int,
    status: str,
    moderator_name: str,
    bot: commands.Bot
):
    """Отправляет уведомление автору новости в ЛС"""
    try:
        author = await bot.fetch_user(author_id)
        
        if status == 'approved':
            embed = discord.Embed(
                title="✅ Ваша новость опубликована!",
                description="Модераторы одобрили вашу новость, и она была опубликована в канале новостей.",
                color=discord.Color.green()
            )
        else:  # rejected
            embed = discord.Embed(
                title="❌ Ваша новость отклонена",
                description=f"Модератор **{moderator_name}** отклонил вашу новость.",
                color=discord.Color.red()
            )
        
        await author.send(embed=embed)
        logger.info(f"Уведомление отправлено пользователю {author_id}")
        
    except discord.Forbidden:
        logger.warning(f"Не удалось отправить ЛС пользователю {author_id} (ЛС закрыты)")
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления автору: {e}")


class NewsModeration(commands.Cog):
    """Ког для системы модерации новостей"""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name="submit_news", description="Подать новость на модерацию")
    async def submit_news(self, interaction: discord.Interaction):
        """Команда подачи новости через слэш-команду"""
        modal = NewsSubmitModal()
        await interaction.response.send_modal(modal)
    
    @commands.command(name="submit", aliases=["подать"])
    async def submit_legacy(self, ctx, *, content: str = None):
        """
        Legacy команда для подачи новости.
        Использование: !submit news <текст> + опциональное вложение (фото/картинка)
        """
        # Проверяем, что это команда "submit news"
        args = content.split() if content else []
        if not args or args[0].lower() != 'news':
            await ctx.send(
                "❌ Используйте команду правильно: `!submit news <текст>`\n"
                "Вы также можете прикрепить картинку к сообщению."
            )
            return
        
        # Получаем текст новости (всё после "news")
        news_text = ' '.join(args[1:]) if len(args) > 1 else ""
        
        # Проверяем наличие вложения
        image_url = None
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            if attachment.content_type and attachment.content_type.startswith('image/'):
                image_url = attachment.url
        
        # Если текста нет и нет картинки - просим ввести
        if not news_text and not image_url:
            await ctx.send("❌ Укажите текст новости или прикрепите картинку!")
            return
        
        # Сохраняем заявку в БД
        from database.mysql_connector import create_news_request
        
        request_id = await create_news_request(
            author_id=ctx.author.id,
            content=news_text,
            image_url=image_url
        )
        
        if request_id:
            await ctx.send("✅ Заявка отправлена на модерацию")
            
            # Отправляем уведомление в канал модерации
            await send_to_moderation_channel(
                request_id=request_id,
                author=ctx.author,
                content=news_text,
                image_url=image_url,
                bot=self.bot
            )
            
            logger.info(f"Новость подана пользователем {ctx.author} (ID: {request_id})")
        else:
            await ctx.send("❌ Не удалось сохранить заявку. Попробуйте позже.")
    
    @commands.command(name="mynews", aliases=["mynews", "моиновости"])
    async def my_news(self, ctx):
        """Показать статус ваших последних заявок"""
        from database.mysql_connector import get_user_news_requests
        
        requests = await get_user_news_requests(ctx.author.id, limit=5)
        
        if not requests:
            await ctx.send("📭 У вас пока нет заявок на новости.")
            return
        
        embed = discord.Embed(
            title="📰 Ваши последние заявки",
            color=discord.Color.blue()
        )
        
        status_emoji = {
            'pending': '⏳',
            'approved': '✅',
            'rejected': '❌'
        }
        
        for req in requests:
            status = req['status']
            emoji = status_emoji.get(status, '❓')
            embed.add_field(
                name=f"{emoji} Заявка #{req['id']} ({req['created_at'].strftime('%d.%m.%Y %H:%M')})",
                value=f"Статус: **{status}**\n" + 
                      (f"Модератор: <@{req['moderator_id']}>" if req['moderator_id'] else "Ожидает проверки"),
                inline=False
            )
        
        await ctx.send(embed=embed)


async def setup(bot):
    """Функция загрузки Cog"""
    await bot.add_cog(NewsModeration(bot))
