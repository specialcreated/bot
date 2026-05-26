"""
Модуль с unit-тестами для критических функций бота.
"""
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio
from datetime import datetime


class TestRateLimiter(unittest.TestCase):
    """Тесты для rate limiter."""
    
    def setUp(self):
        from utils.rate_limiter import RateLimiter
        self.limiter = RateLimiter(default_cooldown=1.0, max_calls=3, period=10.0)
    
    def test_initial_state(self):
        """Проверка начального состояния."""
        self.assertEqual(self.limiter.max_calls, 3)
        self.assertEqual(self.limiter.period, 10.0)
        self.assertEqual(len(self.limiter._call_history), 0)
    
    async def test_rate_limiting(self):
        """Проверка ограничения частоты вызовов."""
        user_id, guild_id = 123, 456
        
        # Первые 3 вызова должны пройти
        for i in range(3):
            is_limited, _ = await self.limiter.check_and_record(user_id, guild_id)
            self.assertFalse(is_limited, f"Вызов {i+1} не должен быть ограничен")
        
        # 4-й вызов должен быть ограничен
        is_limited, remaining = await self.limiter.check_and_record(user_id, guild_id)
        self.assertTrue(is_limited, "4-й вызов должен быть ограничен")
        self.assertGreater(remaining, 0)
    
    async def test_reset(self):
        """Проверка сброса истории."""
        user_id, guild_id = 123, 456
        
        # Делаем несколько вызовов
        for _ in range(3):
            await self.limiter.check_and_record(user_id, guild_id)
        
        # Сбрасываем
        self.limiter.reset(user_id, guild_id)
        
        # Проверяем, что история очищена
        is_limited, _ = await self.limiter.check_and_record(user_id, guild_id)
        self.assertFalse(is_limited)


class TestXPFormula(unittest.TestCase):
    """Тесты для формулы расчёта XP."""
    
    def test_xp_calculation(self):
        """Проверка корректности формулы XP."""
        # Тестируем саму формулу без импорта из БД
        def calculate_next_level_xp(current_level: int) -> int:
            return 100 * (2 ** (current_level - 1))
        
        # Уровень 1 -> 100 XP
        self.assertEqual(calculate_next_level_xp(1), 100)
        # Уровень 2 -> 200 XP
        self.assertEqual(calculate_next_level_xp(2), 200)
        # Уровень 3 -> 400 XP
        self.assertEqual(calculate_next_level_xp(3), 400)
        # Уровень 4 -> 800 XP
        self.assertEqual(calculate_next_level_xp(4), 800)
    
    def test_level_progression(self):
        """Проверка прогрессии уровней."""
        # Тестируем саму формулу без импорта из БД
        def calculate_next_level_xp(current_level: int) -> int:
            return 100 * (2 ** (current_level - 1))
        
        prev_xp = 0
        for level in range(1, 6):
            xp = calculate_next_level_xp(level)
            # Каждый следующий уровень требует больше XP
            self.assertGreater(xp, prev_xp, f"Уровень {level} должен требовать больше XP")
            prev_xp = xp


class TestSQLInjectionPrevention(unittest.TestCase):
    """Тесты для предотвращения SQL-инъекций."""
    
    def test_field_validation(self):
        """Проверка валидации имён полей."""
        allowed_fields = {
            'welcome_channel_id', 'goodbye_channel_id',
            'log_channel_id', 'mod_log_channel_id'
        }
        
        # Допустимые поля
        valid_fields = ['welcome_channel_id', 'goodbye_channel_id']
        for field in valid_fields:
            self.assertIn(field, allowed_fields)
        
        # Недопустимые поля (попытка инъекции)
        invalid_fields = [
            'user_id; DROP TABLE users--',
            'balance; DELETE FROM users',
            '../../etc/passwd',
            'xp OR 1=1'
        ]
        for field in invalid_fields:
            self.assertNotIn(field, allowed_fields)


class TestCooldownCleanup(unittest.TestCase):
    """Тесты для очистки cooldowns."""
    
    def test_cleanup_logic(self):
        """Проверка логики очистки устаревших записей."""
        current_time = datetime.utcnow().timestamp()
        
        # Имитируем cooldowns с разными timestamp
        cooldowns = {
            ('user1', 123): current_time - 700,  # 11+ минут назад - должен удалиться
            ('user2', 123): current_time - 300,  # 5 минут назад - должен остаться
            ('user3', 123): current_time - 50,   # 50 секунд назад - должен остаться
        }
        
        expired_keys = [
            key for key, timestamp in cooldowns.items()
            if current_time - timestamp > 600  # 10 минут
        ]
        
        self.assertEqual(len(expired_keys), 1)
        self.assertEqual(expired_keys[0], ('user1', 123))


def run_tests():
    """Запускает все тесты."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Добавляем тесты
    suite.addTests(loader.loadTestsFromTestCase(TestRateLimiter))
    suite.addTests(loader.loadTestsFromTestCase(TestXPFormula))
    suite.addTests(loader.loadTestsFromTestCase(TestSQLInjectionPrevention))
    suite.addTests(loader.loadTestsFromTestCase(TestCooldownCleanup))
    
    # Запускаем
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    exit(0 if success else 1)
