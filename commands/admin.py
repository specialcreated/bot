import discord
import asyncio
import logging
from discord.ext import commands

# Логирование
logger = logging.getLogger(__name__)

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='clear', aliases=['purge', 'очистить'])
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx, amount: int = 10):
        if amount <= 0 or amount > 100:
            await ctx.send("❌ Количество сообщений должно быть от 1 до 100!")
            return

        try:
            deleted = await ctx.channel.purge(limit=amount + 1)
            logger.info(f'🗑️ ОЧИСТКА ЧАТА | Модератор: {ctx.author} ({ctx.author.id}) | Канал: {ctx.channel.name} | Удалено сообщений: {len(deleted) - 1}')
            msg = await ctx.send(f"✅ Удалено **{len(deleted) - 1}** сообщений!")
            await asyncio.sleep(3)
            await msg.delete()
        except discord.Forbidden:
            logger.error(f'❌ НЕДОСТАТОЧНО ПРАВ | Модератор: {ctx.author} ({ctx.author.id}) | Канал: {ctx.channel.name}')
            await ctx.send("❌ У бота нет прав для удаления сообщений!")
        except Exception as e:
            logger.error(f'❌ ОШИБКА CLEAR | Модератор: {ctx.author} ({ctx.author.id}) | Канал: {ctx.channel.name} | Ошибка: {str(e)}')

    @commands.command(name="setwelcome")
    @commands.has_permissions(administrator=True)
    async def set_welcome_channel(self, ctx, channel: discord.TextChannel):
        """Устанавливает канал для приветствий новых участников."""
        try:
            # Сохраняем в БД
            success = await self.save_channel_to_db(ctx.guild.id, "welcome_channel_id", channel.id)

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

            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Ошибка в команде setwelcome для гильдии {ctx.guild.id}: {e}")
            await ctx.send("Произошла ошибка при установке канала.")

    @commands.command(name="setgoodbye")
    @commands.has_permissions(administrator=True)
    async def set_goodbye_channel(self, ctx, channel: discord.TextChannel):
        """Устанавливает канал для сообщений о выходе участников."""
        try:
            # Сохраняем в БД
            success = await self.save_channel_to_db(ctx.guild.id, "goodbye_channel_id", channel.id)

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

            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Ошибка в команде setgoodbye для гильдии {ctx.guild.id}: {e}")
            await ctx.send("Произошла ошибка при установке канала.")

    async def save_channel_to_db(self, guild_id: int, channel_type: str, channel_id: int) -> bool:
        """Сохраняет ID канала в базу данных."""
        from database.mysql_connector import update_server_settings
        return await update_server_settings(guild_id, {channel_type: channel_id})
    
    @commands.command(name="sendmessage", aliases=["sendmsg", "msg"])
    @commands.has_permissions(administrator=True)
    async def send_message(self, ctx, channel: discord.TextChannel, *, message_content: str = None):
        """
        Отправляет сообщение в указанный канал. Можно добавить фото через вложение.
        Использование: !sendmessage #канал Текст сообщения [прикрепить фото]
        """
        try:
            # Проверяем, есть ли вложение (фото/видео)
            if ctx.message.attachments:
                attachment = ctx.message.attachments[0]
                # Проверяем формат файла (только изображения)
                if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif']):
                    file = await attachment.to_file()
                else:
                    await ctx.send("❌ Можно прикреплять только изображения (PNG, JPG, JPEG, GIF)")
                    return
            else:
                file = None

            # Отправляем сообщение
            sent_message = await channel.send(
                content=message_content,
                file=file
            )

            # Логируем действие
            logger.info(
                f'📩 ОТПРАВКА СООБЩЕНИЯ | Модератор: {ctx.author} ({ctx.author.id}) | '
                f'Канал: {channel.name} | ID сообщения: {sent_message.id}'
            )

            # Подтверждение пользователю
            confirmation_embed = discord.Embed(
                title="✅ Сообщение отправлено",
                color=discord.Color.green(),
                description=f"Сообщение успешно отправлено в {channel.mention}"
            )
            if message_content:
                confirmation_embed.add_field(
                    name="Текст сообщения",
                    value=message_content[:1000] + ("..." if len(message_content) > 1000 else ""),
                    inline=False
                )
            await ctx.reply(embed=confirmation_embed, mention_author=False)

        except discord.Forbidden:
            logger.error(
                f'❌ НЕДОСТАТОЧНО ПРАВ | Модератор: {ctx.author} ({ctx.author.id}) | Канал: {channel.name}'
            )
            await ctx.send("❌ У бота нет прав для отправки сообщений в этот канал!")
        except Exception as e:
            logger.error(
                f'❌ ОШИБКА SENDMESSAGE | Модератор: {ctx.author} ({ctx.author.id}) | Канал: {channel.name} | Ошибка: {str(e)}'
            )
            await ctx.send("❌ Ошибка при отправке сообщения!")

# Функция загрузки Cog — принимает все аргументы, но использует только bot
async def setup(bot, db_pool=None, get_user_profile=None, create_user_record=None):
    await bot.add_cog(Admin(bot))
