import discord
from discord.ext import commands
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

class ProfanityFilter(commands.Cog):
    """Ког для фильтрации матерных слов и нежелательного контента."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Список запрещённых слов теперь загружается из БД (на сервер)
        self.banned_words: dict[int, set[str]] = {}  # guild_id -> set of words
        # Паттерны для поиска замаскированных слов
        self.banned_patterns = [
            r'м[а@*4]\\т',  # Пример паттерна для мата
            r'п[и1!|]д[а@*4]л',  # Пример паттерна
        ]
        # Кэш статуса фильтра по серверам
        self.filter_enabled: dict[int, bool] = {}

    async def cog_load(self):
        """Загрузка настроек из БД при старте."""
        logger.info("Загрузка настроек фильтра мата...")
        # Таблицы уже созданы в main.py через init_all_tables()
    
    async def _load_banned_words(self, guild_id: int):
        """Загружает список запрещённых слов для сервера из БД."""
        from database.mysql_connector import get_profanity_words
        try:
            words = await get_profanity_words(guild_id)
            self.banned_words[guild_id] = set(words)
            logger.debug(f"Загружено {len(self.banned_words[guild_id])} запрещённых слов для сервера {guild_id}")
        except Exception as e:
            logger.error(f"Ошибка загрузки запрещённых слов для сервера {guild_id}: {e}")
            self.banned_words[guild_id] = set()
    
    async def _load_filter_status(self, guild_id: int):
        """Загружает статус фильтра для сервера."""
        from database.mysql_connector import is_profanity_filter_enabled
        try:
            self.filter_enabled[guild_id] = await is_profanity_filter_enabled(guild_id)
            logger.debug(f"Статус фильтра для сервера {guild_id}: {'включен' if self.filter_enabled[guild_id] else 'выключен'}")
        except Exception as e:
            logger.error(f"Ошибка загрузки статуса фильтра для сервера {guild_id}: {e}")
            self.filter_enabled[guild_id] = True
    
    async def _ensure_guild_loaded(self, guild_id: int):
        """Убеждается, что настройки сервера загружены."""
        if guild_id not in self.banned_words:
            await self._load_banned_words(guild_id)
        if guild_id not in self.filter_enabled:
            await self._load_filter_status(guild_id)
    
    def contains_profanity(self, text: str, guild_id: int) -> Optional[str]:
        """
        Проверяет текст на наличие запрещённых слов для конкретного сервера.
        Возвращает найденное запрещённое слово или None.
        """
        text_lower = text.lower()
        
        # Убеждаемся, что слова загружены
        if guild_id not in self.banned_words:
            return None
        
        # Проверка по списку слов
        for word in self.banned_words[guild_id]:
            if word in text_lower:
                return word
        
        # Проверка по паттернам
        for pattern in self.banned_patterns:
            match = re.search(pattern, text_lower, re.IGNORECASE)
            if match:
                return match.group(0)
        
        return None
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Проверка сообщений на запрещённый контент."""
        # Игнорируем ботов и сообщения без текста
        if message.author.bot or not message.content:
            return
        
        # Игнорируем DM
        if not message.guild:
            return
        
        guild_id = message.guild.id
        
        # Убеждаемся, что настройки сервера загружены
        await self._ensure_guild_loaded(guild_id)
        
        # Проверяем, включен ли фильтр на этом сервере
        if not self.filter_enabled.get(guild_id, True):
            return
        
        # Проверяем сообщение
        banned_word = self.contains_profanity(message.content, guild_id)
        
        if banned_word:
            logger.warning(
                f"🚫 ОБНАРУЖЕН МАТ | Пользователь: {message.author} ({message.author.id}) | "
                f"Канал: {message.channel.name} ({message.channel.id}) | Слово: {banned_word}"
            )
            
            try:
                # Удаляем сообщение
                await message.delete()
                
                # Отправляем предупреждение пользователю
                warning_embed = discord.Embed(
                    title="⚠️ Нарушение правил",
                    description=(
                        f"{message.author.mention}, ваше сообщение было удалено, "
                        f"так как оно содержит запрещённую лексику.\n\n"
                        f"**Пожалуйста, соблюдайте правила сервера!**"
                    ),
                    color=discord.Color.orange()
                )
                warning_embed.set_footer(text=f"Канал: #{message.channel.name}")
                
                sent_warning = await message.channel.send(embed=warning_embed, delete_after=10)
                
                # Логируем действие
                logger.info(f"Сообщение от {message.author.id} удалено за нарушение правил")
                
            except discord.Forbidden:
                logger.error(f"Нет прав на удаление сообщения в канале {message.channel.id}")
            except discord.NotFound:
                logger.warning(f"Сообщение уже удалено до попытки удаления ботом")
            except Exception as e:
                logger.error(f"Ошибка при обработке нарушения: {e}")
    
    @commands.command(name='add_banned_word')
    @commands.has_permissions(administrator=True)
    async def add_banned_word(self, ctx, *, word: str):
        """Добавить слово в список запрещённых на сервере."""
        guild_id = ctx.guild.id
        word_lower = word.lower().strip()
        
        # Убеждаемся, что настройки загружены
        await self._ensure_guild_loaded(guild_id)
        
        if word_lower in self.banned_words.get(guild_id, set()):
            await ctx.send(f"❌ Слово '{word}' уже в списке запрещённых.")
            return
        
        from database.mysql_connector import add_profanity_word
        success = await add_profanity_word(guild_id, word_lower)
        
        if success:
            if guild_id not in self.banned_words:
                self.banned_words[guild_id] = set()
            self.banned_words[guild_id].add(word_lower)
            logger.info(f"Администратор {ctx.author} добавил слово '{word}' в бан-лист БД для сервера {guild_id}")
            await ctx.send(f"✅ Слово '{word}' добавлено в список запрещённых.")
        else:
            await ctx.send(f"❌ Ошибка при добавлении слова в базу данных.")
    
    @commands.command(name='remove_banned_word')
    @commands.has_permissions(administrator=True)
    async def remove_banned_word(self, ctx, *, word: str):
        """Удалить слово из списка запрещённых на сервере."""
        guild_id = ctx.guild.id
        word_lower = word.lower().strip()
        
        # Убеждаемся, что настройки загружены
        await self._ensure_guild_loaded(guild_id)
        
        if word_lower not in self.banned_words.get(guild_id, set()):
            await ctx.send(f"❌ Слово '{word}' не найдено в списке запрещённых.")
            return
        
        from database.mysql_connector import remove_profanity_word
        success = await remove_profanity_word(guild_id, word_lower)
        
        if success:
            self.banned_words[guild_id].discard(word_lower)
            logger.info(f"Администратор {ctx.author} удалил слово '{word}' из бан-листа БД для сервера {guild_id}")
            await ctx.send(f"✅ Слово '{word}' удалено из списка запрещённых.")
        else:
            await ctx.send(f"❌ Ошибка при удалении слова из базы данных.")
    
    @commands.command(name='list_banned_words')
    @commands.has_permissions(administrator=True)
    async def list_banned_words(self, ctx):
        """Показать список запрещённых слов на сервере."""
        guild_id = ctx.guild.id
        
        # Перезагружаем список из БД для актуальности
        await self._load_banned_words(guild_id)
        
        if not self.banned_words.get(guild_id):
            await ctx.send("ℹ️ Список запрещённых слов пуст.")
            return
        
        words_list = '\n'.join(f"• `{word}`" for word in sorted(self.banned_words[guild_id]))
        
        embed = discord.Embed(
            title="📋 Список запрещённых слов",
            description=words_list,
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Всего слов: {len(self.banned_words[guild_id])}")
        
        await ctx.send(embed=embed)
    
    @commands.command(name='toggle_profanity')
    @commands.has_permissions(administrator=True)
    async def toggle_profanity(self, ctx):
        """Включить/выключить фильтр матов для всего сервера."""
        guild_id = ctx.guild.id
        author_id = ctx.author.id
        
        # Убеждаемся, что настройки загружены
        await self._ensure_guild_loaded(guild_id)
        
        from database.mysql_connector import toggle_profanity_filter
        
        is_enabled = self.filter_enabled.get(guild_id, True)
        
        # Переключаем статус
        new_status = not is_enabled
        success = await toggle_profanity_filter(guild_id, new_status, author_id)
        
        if success:
            self.filter_enabled[guild_id] = new_status
            status_text = "включён" if new_status else "выключен"
            emoji = "✅" if new_status else "⚠️"
            await ctx.send(f"{emoji} Фильтр матов **{status_text}** для всего сервера!")
            logger.info(f"Фильтр матов {status_text} для сервера {guild_id} администратором {ctx.author}")
        else:
            await ctx.send("❌ Ошибка при обновлении настроек фильтра.")
    
    @commands.command(name='profanity_stats')
    async def profanity_stats(self, ctx):
        """Показать статистику фильтра."""
        guild_id = ctx.guild.id
        
        # Убеждаемся, что настройки загружены
        await self._ensure_guild_loaded(guild_id)
        
        words_count = len(self.banned_words.get(guild_id, set()))
        is_enabled = self.filter_enabled.get(guild_id, True)
        
        embed = discord.Embed(
            title="📊 Статистика фильтра мата",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Запрещённых слов",
            value=str(words_count),
            inline=True
        )
        embed.add_field(
            name="Статус фильтра",
            value="✅ Включен" if is_enabled else "❌ Выключен",
            inline=True
        )
        embed.add_field(
            name="Сервер",
            value=ctx.guild.name,
            inline=True
        )
        
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ProfanityFilter(bot))
