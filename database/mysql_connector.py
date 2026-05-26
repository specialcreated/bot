import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv
from typing import Optional, Dict, List
import asyncio
import logging
from datetime import datetime
from mysql.connector.pooling import MySQLConnectionPool
import json
import threading
from collections import defaultdict

# Настройка логгера (без basicConfig, чтобы не переопределять настройки из main.py)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Конфигурация пула подключений к БД
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", 3306)),
    "database": os.getenv("DB_NAME"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "connection_timeout": 30
}

# Создание пула подключений (размер пула — 5)
connection_pool = MySQLConnectionPool(
    pool_name="bot_pool",
    pool_size=5,
    pool_reset_session=True,
    **DB_CONFIG
)

# Блокировки для предотвращения race condition
_user_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_global_db_lock = threading.Lock()

def get_db_connection():
    """Получает соединение из пула."""
    try:
        connection = connection_pool.get_connection()
        if connection.is_connected():
            return connection
    except Error as e:
        logger.error(f"Ошибка получения соединения из пула: {e}")
        return None
def calculate_next_level_xp(current_level: int) -> int:
    """Рассчитывает XP для следующего уровня (100, 200, 400...)"""
    return 100 * (2 ** (current_level - 1))

async def get_user_profile(user_id: int, guild_id: int) -> Optional[Dict]:
    """Асинхронно получает профиль пользователя из БД."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        _sync_get_user_profile,
        user_id,
        guild_id
    )
    return result
def _sync_get_user_profile(user_id: int, guild_id: int) -> Optional[Dict]:
    connection = get_db_connection()
    if not connection:
        return None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_id, discord_tag, created_at, joined_at, guild_id, "
            "voice_time_total, message_count, balance, level, xp, next_level_xp "
            "FROM users WHERE user_id = %s AND guild_id = %s",
            (user_id, guild_id)
        )
        result = cursor.fetchone()
        logger.debug(f"Получен профиль для user_id={user_id}, guild_id={guild_id}: {result}")
        return result
    except Error as e:
        logger.error(f"Ошибка при получении профиля user_id={user_id}: {e}")
        return None
    finally:
        if connection.is_connected():
            connection.close()
async def update_user_xp(user_id: int, guild_id: int, xp_gain: int) -> Optional[Dict]:
    """Асинхронно обновляет XP пользователя с блокировкой для предотвращения race condition."""
    # Получаем блокировку для конкретного пользователя
    lock = _user_locks[user_id]
    
    async with lock:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _sync_update_user_xp,
            user_id,
            guild_id,
            xp_gain
        )
        return result

def _sync_update_user_xp(user_id: int, guild_id: int, xp_gain: int) -> Optional[Dict]:
    connection = get_db_connection()
    if not connection:
        logger.error(f"❌ Ошибка подключения к БД при обновлении XP для {user_id}")
        return None

    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT level, xp, next_level_xp FROM users WHERE user_id = %s AND guild_id = %s",
            (user_id, guild_id)
        )
        user = cursor.fetchone()

        if not user:
            return None

        current_level = user['level']
        current_xp = user['xp']
        next_level_xp = user['next_level_xp']

        new_xp = current_xp + xp_gain
        level_up = False

        while new_xp >= next_level_xp:
            current_level += 1
            new_xp -= next_level_xp
            next_level_xp = calculate_next_level_xp(current_level)
            level_up = True

        cursor.execute(
            "UPDATE users SET level = %s, xp = %s, next_level_xp = %s "
            "WHERE user_id = %s AND guild_id = %s",
            (current_level, new_xp, next_level_xp, user_id, guild_id)
        )
        connection.commit()

        return {
            'level': current_level,
            'xp': new_xp,
            'next_level_xp': next_level_xp,
            'level_up': level_up
        }
    except Error as e:
        logger.error(f"Ошибка при обновлении XP для пользователя {user_id}: {e}")
        connection.rollback()
        return None
    finally:
        if connection.is_connected():
            connection.close()
            
async def create_user_record(user_data: Dict) -> bool:
    """Асинхронно создаёт запись пользователя в БД. Возвращает True при успехе."""
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, _sync_create_user_record, user_data)
    return success
def _sync_create_user_record(user_data: Dict) -> bool:
    """Синхронный метод для создания записи пользователя. Возвращает True при успехе."""
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Ошибка подключения к БД при создании записи")
        return False

    try:
        cursor = connection.cursor()

        # Добавляем поля уровня по умолчанию, если их нет
        if 'level' not in user_data:
            user_data['level'] = 1
        if 'xp' not in user_data:
            user_data['xp'] = 0
        if 'next_level_xp' not in user_data:
            user_data['next_level_xp'] = calculate_next_level_xp(1)  # 100 XP

        cursor.execute(
            "INSERT INTO users "
            "(user_id, discord_tag, created_at, joined_at, guild_id, voice_time_total, message_count, balance, level, xp, next_level_xp) "
            "VALUES (%(user_id)s, %(discord_tag)s, %(created_at)s, %(joined_at)s, %(guild_id)s, "
            "%(voice_time_total)s, %(message_count)s, %(balance)s, %(level)s, %(xp)s, %(next_level_xp)s) "
            "ON DUPLICATE KEY UPDATE user_id = user_id",
            user_data
        )
        connection.commit()
        logger.info(f"Создана запись для пользователя {user_data['user_id']}")
        return True

    except Error as e:
        logger.error(f"Ошибка при создании записи пользователя {user_data['user_id']}: {e}")
        connection.rollback()
        return False
    finally:
        if connection and connection.is_connected():
            connection.close()

def _update_balance(balance: int, user_id: int, guild_id: int) -> int:
    """Синхронный метод для обновления баланса."""
    connection = get_db_connection()
    if not connection:
        raise Exception("Не удалось подключиться к БД")
    try:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE users SET balance = %s WHERE user_id = %s AND guild_id = %s",
            (balance, user_id, guild_id)
        )
        connection.commit()
        rows_affected = cursor.rowcount
        logger.info(f"Баланс обновлён для {user_id} до {balance}, затронуто строк: {rows_affected}")
        return rows_affected
    except Error as e:
        logger.error(f"Ошибка при обновлении баланса для {user_id}: {e}")
        connection.rollback()
        raise
    finally:
        if connection.is_connected():
            connection.close()

def _sync_update_voice_time(user_id: int, guild_id: int, duration: float) -> bool:
    """Синхронный метод для обновления времени в голосовых каналах. Возвращает True при успехе."""
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Ошибка подключения к БД при обновлении voice_time_total")
        return False
    try:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE users SET voice_time_total = voice_time_total + %s "
            "WHERE user_id = %s AND guild_id = %s",
            (duration, user_id, guild_id)
        )
        connection.commit()
        success = cursor.rowcount > 0
        return success
    except Error as e:
        logger.error(f"Ошибка при обновлении voice_time_total для {user_id}: {e}")
        connection.rollback()
        return False
    finally:
        if connection.is_connected():
            connection.close()

async def increment_message_count(user_id: int, guild_id: int) -> bool:
    """Асинхронно увеличивает счётчик сообщений на 1."""
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(
        None,
        _sync_increment_message_count,
        user_id,
        guild_id
    )
    return success

def _sync_increment_message_count(user_id: int, guild_id: int) -> bool:
    """Синхронный метод для увеличения счётчика сообщений."""
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Ошибка подключения к БД при обновлении message_count")
        return False
    try:
        cursor = connection.cursor()
        cursor.execute(
            "UPDATE users SET message_count = message_count + 1 "
            "WHERE user_id = %s AND guild_id = %s",
            (user_id, guild_id)
        )
        connection.commit()
        success = cursor.rowcount > 0
        if success:
            logger.debug(f"Увеличен message_count для {user_id} в гильдии {guild_id}")
        else:
            logger.warning(f"Пользователь {user_id} не найден для обновления message_count")
        return success
    except Error as e:
        logger.error(f"Ошибка при обновлении message_count для {user_id}: {e}")
        connection.rollback()
        return False
    finally:
        if connection.is_connected():
            connection.close()

def get_server_channels(guild_id: int) -> Optional[Dict]:
    """Получает ID каналов из базы данных по ID сервера."""
    connection = get_db_connection()
    if not connection:
        return None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT welcome_channel_id, goodbye_channel_id "
            "FROM server_settings "
            "WHERE guild_id = %s",
            (guild_id,)
        )
        result = cursor.fetchone()
        logger.debug(f"Получены настройки каналов для guild_id={guild_id}: {result}")
        return result
    except Error as e:
        logger.error(f"Ошибка при получении настроек каналов для guild_id={guild_id}: {e}")
        return None
    finally:
        if connection.is_connected():
            connection.close()

async def update_server_settings(guild_id: int, settings: Dict[str, int]) -> bool:
    """
    Обновляет настройки сервера в базе данных.

    Если запись для guild_id существует — обновляет указанные поля.
    Если записи нет — создаёт новую запись с указанными полями и остальными значениями по умолчанию.

    Args:
        guild_id (int): ID сервера Discord.
        settings (Dict[str, int]): Словарь с настройками для обновления, например:
            {"welcome_channel_id": 123456789, "goodbye_channel_id": 987654321}

    Returns:
        bool: True при успешном обновлении/создании, False при ошибке.
    """
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(
        None,
        _sync_update_server_settings,
        guild_id,
        settings
    )
    return success


def _sync_update_server_settings(guild_id: int, settings: Dict[str, int]) -> bool:
    """Синхронный метод для обновления настроек сервера."""
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Ошибка подключения к БД при обновлении настроек сервера")
        return False

    try:
        cursor = connection.cursor()

        # Проверяем, существует ли запись для этого сервера
        cursor.execute(
            "SELECT guild_id FROM server_settings WHERE guild_id = %s",
            (guild_id,)
        )
        exists = cursor.fetchone() is not None

        # Валидация имён полей для предотвращения SQL-инъекций
        allowed_fields = {
            'welcome_channel_id', 'goodbye_channel_id', 
            'log_channel_id', 'mod_log_channel_id'
        }
        
        validated_settings = {}
        for field, value in settings.items():
            if field not in allowed_fields:
                logger.warning(f"Попытка обновления недопустимого поля: {field}")
                continue
            if not isinstance(value, int):
                logger.warning(f"Недопустимое значение для поля {field}: {value}")
                continue
            validated_settings[field] = value

        if not validated_settings:
            logger.error("Нет допустимых полей для обновления")
            return False

        if exists:
            # Обновляем существующие настройки с валидированными полями
            set_clause = ", ".join([f"{field} = %s" for field in validated_settings.keys()])
            values = list(validated_settings.values())
            values.append(guild_id)

            cursor.execute(
                f"UPDATE server_settings SET {set_clause} WHERE guild_id = %s",
                values
            )
            logger.info(f"Обновлены настройки сервера {guild_id}: {validated_settings}")
        else:
            # Создаём новую запись с валидированными полями
            fields = ["guild_id"] + list(validated_settings.keys())
            placeholders = ", ".join(["%s"] * len(fields))
            values = [guild_id] + list(validated_settings.values())

            cursor.execute(
                f"INSERT INTO server_settings ({', '.join(fields)}) VALUES ({placeholders})",
                values
            )
            logger.info(f"Созданы новые настройки сервера {guild_id}: {validated_settings}")

        connection.commit()
        return True

    except Error as e:
        logger.error(f"Ошибка при обновлении настроек сервера {guild_id}: {e}")
        connection.rollback()
        return False
    finally:
        if connection.is_connected():
            connection.close()

async def update_user_balance(user_id: int, guild_id: int, balance: int) -> bool:
    """Асинхронно обновляет баланс пользователя."""
    loop = asyncio.get_event_loop()
    try:
        rows_affected = await loop.run_in_executor(
            None,
            _update_balance,
            balance,
            user_id,
            guild_id
        )
        return rows_affected > 0
    except Exception as e:
        logger.error(f"Ошибка при обновлении баланса для user_id={user_id}: {e}")
        return False

async def update_voice_time(user_id: int, guild_id: int, duration: float) -> bool:
    """Асинхронно обновляет время в голосовых каналах."""
    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(
        None,
        _sync_update_voice_time,
        user_id,
        guild_id,
        duration
    )
    return success

def validate_user_data(user_data: Dict) -> bool:
    """Валидирует данные пользователя перед сохранением."""
    required_fields = [
        'user_id', 'discord_tag', 'created_at',
        'joined_at', 'guild_id', 'voice_time_total',
        'message_count', 'balance'
    ]
    for field in required_fields:
        if field not in user_data:
            logger.error(f"Отсутствует обязательное поле: {field}")
            return False
        if user_data[field] is None:
            logger.error(f"Поле {field} имеет значение None")
            return False
    return True
async def ensure_user_exists(user_id: int, discord_tag: str, guild_id: int) -> bool:
    profile = await get_user_profile(user_id, guild_id)
    if profile:
        return True

    user_data = {
        'user_id': user_id,
        'discord_tag': discord_tag,
        'created_at': datetime.now(),
        'joined_at': datetime.now(),
        'guild_id': guild_id,
        'voice_time_total': 0,
        'message_count': 0,
        'balance': 0,
        'level': 1,
        'xp': 0,
        'next_level_xp': calculate_next_level_xp(1)
    }
    
    if not validate_user_data(user_data):
        logger.error(f"Невалидные данные для создания пользователя: {user_data}")
        return False
    
    success = await create_user_record(user_data)
    if success:
        logger.info(f"Создан новый пользователь в БД: {user_id}")
    else:
        logger.error(f"Не удалось создать пользователя: {user_id}")
    return success

async def fetch_one(query: str, params: tuple = None) -> Optional[dict]:
    connection = get_db_connection()
    if not connection:
        logger.warning("Не удалось получить соединение из пула в fetch_one")
        return None

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(query, params or ())
        result = cursor.fetchone()

        # Гарантированно читаем все оставшиеся результаты
        try:
            while cursor.nextset():
                pass
        except:
            pass

        return result
    except Error as e:
        logger.error(f"Ошибка выполнения запроса fetch_one: {query} с параметрами {params}: {e}")
        return None
    finally:
        if cursor:
            try:
                cursor.close()
            except Error:
                pass
        if connection and connection.is_connected():
            connection.close()

async def fetch_all(query: str, params: tuple = None) -> list:
    connection = get_db_connection()
    if not connection:
        return []

    cursor = None
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(query, params)
        results = cursor.fetchall()

        # Читаем все оставшиеся наборы результатов
        try:
            while cursor.nextset():
                pass
        except:
            pass

        return results
    except Error as e:
        logger.error(f"Ошибка выполнения запроса fetch_all: {query} с параметрами {params}: {e}")
        return []
    finally:
        if cursor:
            try:
                cursor.close()
            except Error:
                pass
        if connection and connection.is_connected():
            connection.close()

async def execute_query(query: str, params: tuple = None) -> bool:
    connection = get_db_connection()
    if not connection:
        logger.warning("Не удалось получить соединение из пула в execute_query")
        return False

    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(query, params)

        # Для DML-запросов читаем все результаты, чтобы очистить курсор
        try:
            while cursor.nextset():
                pass
        except:
            pass

        connection.commit()
        rows_affected = cursor.rowcount
        logger.debug(f"Запрос выполнен, затронуто строк: {rows_affected}")
        return True
    except Error as e:
        logger.error(f"Ошибка выполнения запроса execute_query: {query} с параметрами {params}: {e}")
        try:
            connection.rollback()
        except Error:
            pass
        return False
    finally:
        if cursor:
            try:
                cursor.close()
            except Error:
                pass
        if connection and connection.is_connected():
            connection.close()


# === Функции для фильтра мата (на уровне сервера) ===

async def ensure_profanity_tables_exist() -> bool:
    """Создаёт таблицы для фильтра мата, если они не существуют."""
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Ошибка подключения к БД при создании таблиц фильтра мата")
        return False
    
    try:
        cursor = connection.cursor()
        
        # Создаём таблицу profanity_words
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profanity_words (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                word VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_word_per_guild (guild_id, word),
                INDEX idx_guild_word (guild_id, word)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Создаём таблицу server_profanity_settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS server_profanity_settings (
                guild_id BIGINT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                updated_by BIGINT NULL,
                INDEX idx_guild_id (guild_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        connection.commit()
        logger.info("✅ Таблицы фильтра мата проверены/созданы")
        return True
        
    except Error as e:
        logger.error(f"Ошибка при создании таблиц фильтра мата: {e}")
        connection.rollback()
        return False
    finally:
        if connection.is_connected():
            connection.close()


async def init_all_tables() -> bool:
    """Инициализирует все таблицы базы данных, если они не существуют.
    
    Создаёт следующие таблицы:
    - users (профили пользователей)
    - server_settings (настройки сервера)
    - profanity_words (список запрещённых слов)
    - server_profanity_settings (настройки фильтра мата)
    - giveaways (розыгрыши)
    - family_settings (настройки системы заявок в семью)
    
    Returns:
        bool: True если все таблицы успешно созданы/проверены, False при ошибке
    """
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Ошибка подключения к БД при инициализации таблиц")
        return False
    
    try:
        cursor = connection.cursor()
        
        # Таблица users
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                discord_tag VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL,
                joined_at DATETIME NOT NULL,
                voice_time_total FLOAT DEFAULT 0,
                message_count INT DEFAULT 0,
                balance INT DEFAULT 0,
                level INT DEFAULT 1,
                xp INT DEFAULT 0,
                next_level_xp INT DEFAULT 100,
                PRIMARY KEY (user_id, guild_id),
                INDEX idx_guild_user (guild_id, user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        logger.debug("Таблица users проверена/создана")
        
        # Таблица server_settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS server_settings (
                guild_id BIGINT PRIMARY KEY,
                welcome_channel_id BIGINT NULL,
                goodbye_channel_id BIGINT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        logger.debug("Таблица server_settings проверена/создана")
        
        # Таблица profanity_words
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS profanity_words (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                word VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_word_per_guild (guild_id, word),
                INDEX idx_guild_word (guild_id, word)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        logger.debug("Таблица profanity_words проверена/создана")
        
        # Таблица server_profanity_settings
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS server_profanity_settings (
                guild_id BIGINT PRIMARY KEY,
                is_enabled BOOLEAN DEFAULT TRUE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                updated_by BIGINT NULL,
                INDEX idx_guild_id (guild_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        logger.debug("Таблица server_profanity_settings проверена/создана")
        
        # Таблица giveaways (для системы розыгрышей)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                id INT AUTO_INCREMENT PRIMARY KEY,
                message_id BIGINT,
                channel_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                creator_id BIGINT NOT NULL,
                prize TEXT NOT NULL,
                winners_count INT NOT NULL DEFAULT 1,
                start_time DATETIME NOT NULL,
                end_time DATETIME NOT NULL,
                status ENUM('active', 'completed', 'cancelled') NOT NULL DEFAULT 'active',
                requirements JSON,
                participants JSON,
                winners JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_guild_status (guild_id, status),
                INDEX idx_message_id (message_id),
                INDEX idx_end_time (end_time)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        logger.debug("Таблица giveaways проверена/создана")
        
        # Таблица family_settings (для системы заявок в семью)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS family_settings (
                guild_id BIGINT PRIMARY KEY,
                applications_channel_id BIGINT NULL,
                review_channel_id BIGINT NULL,
                audit_channel_id BIGINT NULL,
                accepted_role_id BIGINT NULL,
                application_message_id BIGINT NULL,
                application_title VARCHAR(255) DEFAULT '👪 Заявка в семью',
                application_text TEXT DEFAULT 'Нажмите кнопку ниже, чтобы подать заявку в нашу семью!',
                application_image_url VARCHAR(512) NULL,
                enabled BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Добавляем недостающие колонки если они не существуют (миграция для старых таблиц)
        cursor.execute("""
            ALTER TABLE family_settings 
            ADD COLUMN IF NOT EXISTS application_title VARCHAR(255) DEFAULT '👪 Заявка в семью'
        """)
        cursor.execute("""
            ALTER TABLE family_settings 
            ADD COLUMN IF NOT EXISTS application_text TEXT DEFAULT 'Нажмите кнопку ниже, чтобы подать заявку в нашу семью!'
        """)
        cursor.execute("""
            ALTER TABLE family_settings 
            ADD COLUMN IF NOT EXISTS application_image_url VARCHAR(512) NULL
        """)
        cursor.execute("""
            ALTER TABLE family_settings 
            ADD COLUMN IF NOT EXISTS application_message_id BIGINT NULL
        """)
        
        logger.debug("Таблица family_settings проверена/создана")
        
        connection.commit()
        logger.info("✅ Все таблицы базы данных проверены/созданы")
        return True
        
    except Error as e:
        logger.error(f"Ошибка при инициализации таблиц БД: {e}")
        connection.rollback()
        return False
    finally:
        if connection.is_connected():
            connection.close()


async def get_profanity_words(guild_id: int) -> list:
    """Получает список всех запрещённых слов для конкретного сервера."""
    result = await fetch_all(
        "SELECT word FROM profanity_words WHERE guild_id = %s ORDER BY id",
        (guild_id,)
    )
    return [row['word'] for row in result] if result else []

async def add_profanity_word(guild_id: int, word: str) -> bool:
    """Добавляет слово в список запрещённых для конкретного сервера."""
    # Сначала убеждаемся, что настройки сервера существуют
    await _ensure_server_settings(guild_id)
    
    return await execute_query(
        "INSERT INTO profanity_words (guild_id, word) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE word = word",
        (guild_id, word.lower())
    )

async def remove_profanity_word(guild_id: int, word: str) -> bool:
    """Удаляет слово из списка запрещённых для конкретного сервера."""
    return await execute_query(
        "DELETE FROM profanity_words WHERE guild_id = %s AND word = %s",
        (guild_id, word.lower())
    )

async def is_profanity_filter_enabled(guild_id: int) -> bool:
    """Проверяет, включен ли фильтр нецензурной лексики для сервера."""
    await _ensure_server_settings(guild_id)
    
    result = await fetch_one(
        "SELECT is_enabled FROM server_profanity_settings WHERE guild_id = %s",
        (guild_id,)
    )
    return result['is_enabled'] if result else True

async def toggle_profanity_filter(guild_id: int, enabled: bool, updated_by: int = None) -> bool:
    """Включает или выключает фильтр нецензурной лексики для всего сервера."""
    await _ensure_server_settings(guild_id)
    
    return await execute_query(
        "UPDATE server_profanity_settings SET is_enabled = %s, updated_by = %s WHERE guild_id = %s",
        (enabled, updated_by, guild_id)
    )

async def _ensure_server_settings(guild_id: int) -> bool:
    """Создает запись настроек сервера, если она не существует."""
    existing = await fetch_one(
        "SELECT guild_id FROM server_profanity_settings WHERE guild_id = %s",
        (guild_id,)
    )
    
    if not existing:
        return await execute_query(
            "INSERT INTO server_profanity_settings (guild_id, is_enabled) VALUES (%s, TRUE)",
            (guild_id,)
        )
    return True


# === Функции для системы розыгрышей (Giveaways) ===

async def create_giveaway(giveaway_data: dict) -> Optional[int]:
    """Создаёт новый розыгрыш в базе данных. Возвращает ID розыгрыша при успехе."""
    connection = get_db_connection()
    if not connection:
        logger.error("❌ Ошибка подключения к БД при создании розыгрыша")
        return None
    
    try:
        cursor = connection.cursor()
        
        # prize может быть строкой или списком - если список, сериализуем в JSON
        prize_value = giveaway_data['prize']
        if isinstance(prize_value, list):
            prize_value = json.dumps(prize_value, ensure_ascii=False)
        
        requirements_json = json.dumps(giveaway_data.get('requirements', {}), ensure_ascii=False)
        
        cursor.execute("""
            INSERT INTO giveaways 
            (channel_id, guild_id, creator_id, prize, winners_count, start_time, end_time, status, requirements, message_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            giveaway_data['channel_id'],
            giveaway_data['guild_id'],
            giveaway_data['creator_id'],
            prize_value,
            giveaway_data['winners_count'],
            giveaway_data['start_time'],
            giveaway_data['end_time'],
            giveaway_data.get('status', 'active'),
            requirements_json,
            giveaway_data.get('message_id')
        ))
        
        connection.commit()
        giveaway_id = cursor.lastrowid
        logger.info(f"Создан розыгрыш #{giveaway_id}")
        return giveaway_id
        
    except Error as e:
        logger.error(f"Ошибка при создании розыгрыша: {e}")
        connection.rollback()
        return None
    finally:
        if connection.is_connected():
            connection.close()


async def update_giveaway_message_id(giveaway_id: int, message_id: int) -> bool:
    """Обновляет ID сообщения розыгрыша."""
    return await execute_query(
        "UPDATE giveaways SET message_id = %s WHERE id = %s",
        (message_id, giveaway_id)
    )


async def get_giveaway_by_id(giveaway_id: int) -> Optional[dict]:
    """Получает данные розыгрыша по ID."""
    
    result = await fetch_one(
        "SELECT * FROM giveaways WHERE id = %s",
        (giveaway_id,)
    )
    
    if result:
        # Парсим JSON поля
        if result.get('requirements'):
            result['requirements'] = json.loads(result['requirements'])
        if result.get('participants'):
            result['participants'] = json.loads(result['participants'])
        if result.get('winners'):
            result['winners'] = json.loads(result['winners'])
        # Парсим prize если это JSON строка (несколько призов)
        if result.get('prize') and isinstance(result['prize'], str):
            try:
                result['prize'] = json.loads(result['prize'])
            except json.JSONDecodeError:
                pass  # Оставляем как строку если это не JSON
    
    return result


async def get_active_giveaways(guild_id: int) -> list:
    """Получает все активные розыгрыши для сервера."""
    
    results = await fetch_all(
        "SELECT * FROM giveaways WHERE guild_id = %s AND status = 'active' ORDER BY end_time ASC",
        (guild_id,)
    )
    
    giveaways = []
    for row in results:
        if row.get('requirements'):
            row['requirements'] = json.loads(row['requirements'])
        if row.get('participants'):
            row['participants'] = json.loads(row['participants'])
        if row.get('winners'):
            row['winners'] = json.loads(row['winners'])
        # Парсим prize если это JSON строка (несколько призов)
        if row.get('prize') and isinstance(row['prize'], str):
            try:
                row['prize'] = json.loads(row['prize'])
            except json.JSONDecodeError:
                pass  # Оставляем как строку если это не JSON
        giveaways.append(row)
    
    return giveaways


async def get_all_active_giveaways() -> list:
    """Получает все активные розыгрыши (для восстановления при старте бота)."""
    
    results = await fetch_all(
        "SELECT * FROM giveaways WHERE status = 'active' ORDER BY end_time ASC"
    )
    
    giveaways = []
    for row in results:
        if row.get('requirements'):
            row['requirements'] = json.loads(row['requirements'])
        if row.get('participants'):
            row['participants'] = json.loads(row['participants'])
        if row.get('winners'):
            row['winners'] = json.loads(row['winners'])
        # Парсим prize если это JSON строка (несколько призов)
        if row.get('prize') and isinstance(row['prize'], str):
            try:
                row['prize'] = json.loads(row['prize'])
            except json.JSONDecodeError:
                pass  # Оставляем как строку если это не JSON
        giveaways.append(row)
    
    return giveaways


async def add_giveaway_participant(giveaway_id: int, user_id: int) -> bool:
    """Добавляет участника в розыгрыш."""
    
    # Получаем текущих участников
    giveaway = await get_giveaway_by_id(giveaway_id)
    if not giveaway:
        return False
    
    participants = giveaway.get('participants', []) or []
    
    # Проверяем, нет ли уже такого участника
    if user_id in participants:
        return False
    
    participants.append(user_id)
    
    participants_json = json.dumps(participants)
    
    return await execute_query(
        "UPDATE giveaways SET participants = %s WHERE id = %s",
        (participants_json, giveaway_id)
    )


async def update_giveaway_winners(giveaway_id: int, winners: list) -> bool:
    """Обновляет список победителей розыгрыша."""
    
    winners_json = json.dumps(winners)
    
    return await execute_query(
        "UPDATE giveaways SET winners = %s WHERE id = %s",
        (winners_json, giveaway_id)
    )


async def update_giveaway_status(giveaway_id: int, status: str) -> bool:
    """Обновляет статус розыгрыша."""
    return await execute_query(
        "UPDATE giveaways SET status = %s WHERE id = %s",
        (status, giveaway_id)
    )


async def delete_giveaway(giveaway_id: int) -> bool:
    """Удаляет розыгрыш из БД."""
    return await execute_query(
        "DELETE FROM giveaways WHERE id = %s",
        (giveaway_id,)
    )


async def cancel_giveaway(giveaway_id: int) -> bool:
    """Отменяет розыгрыш (удаляет из БД)."""
    return await delete_giveaway(giveaway_id)


# Дополнительные утилиты для управления пулом подключений
def close_connection_pool():
    """Закрывает пул подключений к БД."""
    try:
        connection_pool.close()
        logger.info("Пул подключений к БД закрыт")
    except Exception as e:
        logger.error(f"Ошибка при закрытии пула подключений: {e}")

def ping_db() -> bool:
    """Проверяет доступность БД."""
    connection = get_db_connection()
    if not connection:
        return False
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        logger.debug("Проверка БД: соединение активно")
        return True
    except Error:
        logger.error("Проверка БД: соединение не активно")
        return False
    finally:
        if connection.is_connected():
            connection.close()
