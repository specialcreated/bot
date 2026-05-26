import discord
from discord.ext import commands
from datetime import datetime
import logging
import os
from database.mysql_connector import get_server_channels
logger = logging.getLogger(__name__)

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        # Получаем настройки сервера из MySQL (через функцию из mysql_connector.py)
        server_settings = get_server_channels(member.guild.id)

        if not server_settings:
            logger.warning(f"Настройки для сервера {member.guild.id} не найдены в базе данных")
            return

        WELCOME_CHANNEL_ID = server_settings['welcome_channel_id']
        channel = member.guild.get_channel(WELCOME_CHANNEL_ID)

        if channel is None:
            logger.warning(f"Канал для приветствий с ID {WELCOME_CHANNEL_ID} не найден!")
            return

        # Используем встроенный счётчик участников сервера
        total_members = member.guild.member_count

        # Создаём Embed для нового пользователя
        embed = discord.Embed(
            title="✅ Добро пожаловать!",
            description=f"{member.mention} присоединился(-ась) к серверу!",
            color=discord.Color.green()  # Зелёный цвет для приветствия
        )

        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)  # Аватар пользователя
        embed.set_footer(text=f"🏘️ Всего участников: {total_members}")

        # Отправляем Embed в канал приветствий
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        # Получаем настройки сервера из MySQL (через функцию из mysql_connector.py)
        server_settings = get_server_channels(member.guild.id)

        if not server_settings:
            logger.warning(f"Настройки для сервера {member.guild.id} не найдены в базе данных")
            return

        GOODBYE_CHANNEL_ID = server_settings['goodbye_channel_id']
        channel = member.guild.get_channel(GOODBYE_CHANNEL_ID)

        if channel is None:
            logger.warning(f"Канал для прощаний с ID {GOODBYE_CHANNEL_ID} не найден!")
            return

        # Используем встроенный счётчик участников сервера (уже учитывает уход пользователя)
        total_members = member.guild.member_count

        # Создаём Embed для ушедшего пользователя
        embed = discord.Embed(
            title="👋 До свидания!",
            description=f"{member.mention} покинул(-а) сервер.",
            color=discord.Color.red()  # Красный цвет для ухода
        )

        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)  # Аватар пользователя
        embed.set_footer(text=f"🏘️ Всего участников: {total_members}")

        # Отправляем Embed в канал прощаний
        await channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Events(bot))
