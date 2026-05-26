#=====================[ Основные импорты ] =====================
import discord
import logging
import time
import os
import importlib.util
import sys
from pathlib import Path
from typing import Optional, Dict, Callable, Awaitable

#=====================[ Дополнительные импорты ] =====================
from discord.ext import commands
from dotenv import load_dotenv

#=====================[ Настройки бота ] =====================
intents = discord.Intents.default()
intents.message_content = True # Для отслеживания сообщений пользователей
intents.members = True # Для отслеживания пользователей
intents.voice_states = True  # Для отслеживания голосовых каналов
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=None,
    case_insensitive=True
)

#=====================[ Словари ] =====================
voice_times = {}  # Пустой словарь для хранения времени входа в каналы (теперь не используется, перемещён в Profile cog)

#=====================[ Загрузка ENV ] =====================
load_dotenv()

#=====================[ Импорт функций БД ] =====================
from database.mysql_connector import init_all_tables

#=====================[ Настройки логов ] =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot_logs.txt', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

#=====================[ Загрузка Cogs ] =====================
async def load_cogs():
    successful_cogs = []
    failed_cogs = []


    # ИЗМЕНЕНИЕ: указываем папку 'commands' вместо 'cogs'
    commands_dir = Path('./commands')


    for filename in commands_dir.glob('*.py'):
        if filename.name.startswith('__'):
            continue

        cog_name = filename.stem  # Имя файла без .py
        module_path = f'commands.{cog_name}'  # ИЗМЕНЕНИЕ: пространство имён теперь commands.*

        try:
            # Импортируем модуль динамически
            spec = importlib.util.spec_from_file_location(module_path, filename)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_path] = module
            spec.loader.exec_module(module)

            # Ищем функцию setup() в модуле
            if hasattr(module, 'setup') and callable(module.setup):
                await module.setup(bot)
                successful_cogs.append(cog_name)
            else:
                raise AttributeError(f"Нет функции setup() в {cog_name}")

        except Exception as e:
            logger.error(f"❌ Ошибка загрузки {cog_name}: {type(e).__name__}: {e}")
            failed_cogs.append((cog_name, str(e)))

    # Вывод результатов
    print('📊 Итоги загрузки команд:')
    print(f'✅ Успешно: {len(successful_cogs)}')
    print(f'❌ С ошибками: {len(failed_cogs)}')

    if failed_cogs:
        print('\nСписок cogs с ошибками:')
        for cog, error in failed_cogs:
            print(f'  • {cog}: {error}')

#=====================[ Инфо-подключение бота ] =====================
@bot.event
async def on_ready():
    print(f'✓ Бот {bot.user} запущен!')
    print(f'✓ ID: {bot.user.id}')
    print(f'✓ Серверов: {len(bot.guilds)}')

    # Инициализация всех таблиц БД
    try:
        await init_all_tables()
        print('✓ Таблицы базы данных проверены/созданы')
    except Exception as e:
        logger.error(f'❌ Ошибка инициализации таблиц БД: {e}')

    try:
        await load_cogs()
        print(f'✓ Загружено cogs: {list(bot.cogs.keys())}')
    except Exception as e:
        logger.error(f'❌ Ошибка инициализации: {e}')

#=====================[ Инфо-использования-команд ] =====================
@bot.event
async def on_command(ctx):
    if ctx.guild:
        guild_id = str(ctx.guild.id)
        user_id = str(ctx.author.id)
        logger.info(f'✓ КОМАНДА ВЫПОЛНЕНА | Пользователь: {ctx.author} ({user_id}) | Сервер: {ctx.guild.name} | Команда: {ctx.command.name}')

#=====================[ Функция при возникновении ошибок при использовании команд ] =====================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        logger.error(f'❌ КОМАНДА НЕ НАЙДЕНА | Пользователь: {ctx.author} ({ctx.author.id}) | Сервер: {ctx.guild.name if ctx.guild else "DM"} | Введено: {ctx.message.content}')
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        logger.error(f'❌ ОТСУТСТВУЮТ ПАРАМЕТРЫ | Пользователь: {ctx.author} ({ctx.author.id}) | Сервер: {ctx.guild.name if ctx.guild else "DM"} | Команда: {ctx.command.name} | Требуемый параметр: {error.param.name}')
        await ctx.send(f"❌ Не указаны параметры", delete_after=5)
    elif isinstance(error, commands.MissingPermissions):
        logger.warning(f'⚠️ НЕДОСТАТОЧНО ПРАВ | Пользователь: {ctx.author} ({ctx.author.id}) | Сервер: {ctx.guild.name if ctx.guild else "DM"} | Команда: {ctx.command.name} | Требуется: {error.missing_permissions}')
        await ctx.send(f"❌ Недостаточно прав!", delete_after=5)
    elif isinstance(error, commands.BotMissingPermissions):
        logger.error(f'❌ БОТ НЕ ИМЕЕТ ПРАВ | Сервер: {ctx.guild.name if ctx.guild else "DM"} | Команда: {ctx.command.name} | Требуется: {error.missing_permissions}')
        await ctx.send(f"❌ У бота недостаточно прав!", delete_after=5)
    elif isinstance(error, commands.BadArgument):
        logger.error(f'❌ НЕВЕРНЫЙ АРГУМЕНТ | Пользователь: {ctx.author} ({ctx.author.id}) | Команда: {ctx.command.name} | Ошибка: {str(error)}')
        await ctx.send(f"❌ Неверный формат аргумента!", delete_after=5)
    else:
        logger.error(f'❌ НЕПРЕДВИДЕННАЯ ОШИБКА | Пользователь: {ctx.author} ({ctx.author.id}) | Сервер: {ctx.guild.name if ctx.guild else "DM"} | Команда: {ctx.command.name if ctx.command else "N/A"} | Ошибка: {type(error).__name__}: {str(error)}')
        await ctx.send(f"❌ Произошла ошибка при выполнении команды. Обратитесь к администратору.", delete_after=5)


#=====================[ Запуск бота ] =====================
bot.start_time = time.time()
if __name__ == '__main__':
    # Получаем токен из переменных окружения
    TOKEN = os.getenv('DISCORD_TOKEN')

    if not TOKEN:
        logger.error('❌ DISCORD_TOKEN не найден! Добавьте его в переменные окружения или в файл .env')
        print('❌ ОШИБКА: DISCORD_TOKEN не найден!')
        print('Пожалуйста, установите DISCORD_TOKEN в:')
        print('  1. Переменные окружения системы, или')
        print('  2. Файл .env в корне проекта')
        exit(1)

    try:
        logger.info('✓ Запуск бота...')
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f'❌ Ошибка запуска бота: {e}')
        print(f"❌ Ошибка: {e}")
