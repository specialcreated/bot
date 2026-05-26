"""
Модуль для rate limiting (ограничения частоты использования команд).
Защищает от спама командами.
"""
import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Класс для ограничения частоты вызовов команд.
    
    Атрибуты:
        default_cooldown: Базовое время задержки в секундах (по умолчанию 3 сек)
        max_calls: Максимальное количество вызовов за период (по умолчанию 5)
        period: Период времени в секундах для подсчёта вызовов (по умолчанию 60 сек)
    """
    
    def __init__(self, default_cooldown: float = 3.0, max_calls: int = 5, period: float = 60.0):
        self.default_cooldown = default_cooldown
        self.max_calls = max_calls
        self.period = period
        # Храним timestamps вызовов: {(user_id, guild_id): [timestamp1, timestamp2, ...]}
        self._call_history: Dict[tuple, list] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start_cleanup_task(self):
        """Запускает фоновую задачу по очистке устаревших записей."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    async def _cleanup_loop(self):
        """Периодически очищает устаревшие записи (каждые 2 минуты)."""
        try:
            while True:
                await asyncio.sleep(120)
                current_time = datetime.utcnow().timestamp()
                async with self._lock:
                    keys_to_delete = []
                    for key, timestamps in self._call_history.items():
                        # Оставляем только недавние вызовы
                        self._call_history[key] = [
                            ts for ts in timestamps 
                            if current_time - ts < self.period * 2
                        ]
                        if not self._call_history[key]:
                            keys_to_delete.append(key)
                    
                    for key in keys_to_delete:
                        del self._call_history[key]
                    
                    if keys_to_delete:
                        logger.debug(f"Очищено {len(keys_to_delete)} пустых записей rate limiter")
        except asyncio.CancelledError:
            logger.info("Задача очистки rate limiter остановлена")
            raise
        except Exception as e:
            logger.error(f"Ошибка в задаче очистки rate limiter: {e}")
    
    async def stop_cleanup_task(self):
        """Останавливает задачу очистки."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
    
    async def is_rate_limited(self, user_id: int, guild_id: int) -> Tuple[bool, Optional[float]]:
        """
        Проверяет, превышен ли лимит вызовов для пользователя.
        
        Returns:
            Tuple[bool, Optional[float]]: (is_limited, remaining_cooldown)
            - is_limited: True если пользователь заблокирован
            - remaining_cooldown: оставшееся время задержки в секундах (если заблокирован)
        """
        key = (user_id, guild_id)
        current_time = datetime.utcnow().timestamp()
        
        async with self._lock:
            # Очищаем старые вызовы за пределами периода
            self._call_history[key] = [
                ts for ts in self._call_history[key]
                if current_time - ts < self.period
            ]
            
            # Проверяем количество вызовов за период
            if len(self._call_history[key]) >= self.max_calls:
                # Вычисляем оставшееся время до сброса
                oldest_call = min(self._call_history[key])
                remaining = self.period - (current_time - oldest_call)
                return True, max(0, remaining)
            
            return False, None
    
    async def record_call(self, user_id: int, guild_id: int):
        """Регистрирует вызов команды пользователем."""
        key = (user_id, guild_id)
        current_time = datetime.utcnow().timestamp()
        
        async with self._lock:
            self._call_history[key].append(current_time)
    
    async def check_and_record(self, user_id: int, guild_id: int) -> Tuple[bool, Optional[float]]:
        """
        Проверяет лимит и регистрирует вызов если не превышен.
        
        Returns:
            Tuple[bool, Optional[float]]: (is_limited, remaining_cooldown)
        """
        is_limited, cooldown = await self.is_rate_limited(user_id, guild_id)
        if not is_limited:
            await self.record_call(user_id, guild_id)
        return is_limited, cooldown
    
    def reset(self, user_id: int, guild_id: int):
        """Сбрасывает историю вызовов для конкретного пользователя."""
        key = (user_id, guild_id)
        if key in self._call_history:
            del self._call_history[key]


# Глобальный экземпляр rate limiter для использования в боте
global_rate_limiter = RateLimiter(default_cooldown=3.0, max_calls=5, period=60.0)


def rate_limit_check(cooldown: float = 3.0, max_calls: int = 5, period: float = 60.0):
    """
    Декоратор для проверки rate limit в командах.
    
    Пример использования:
        @commands.command()
        @rate_limit_check(cooldown=5.0, max_calls=3, period=30.0)
        async def my_command(self, ctx):
            ...
    """
    from discord.ext import commands
    
    async def predicate(ctx):
        limiter = global_rate_limiter
        is_limited, remaining = await limiter.check_and_record(ctx.author.id, ctx.guild.id)
        
        if is_limited:
            await ctx.send(
                f"⏳ Слишком много запросов! Подождите {remaining:.1f} сек.",
                delete_after=5
            )
            return False
        return True
    
    return commands.check(predicate)
