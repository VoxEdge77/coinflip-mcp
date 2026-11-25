"""Работа с базой PostgreSQL: пул соединений и запросы."""

import logging
import os
import threading
from contextlib import contextmanager

from psycopg2 import errors
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger(__name__)

POOL_MIN = 1
POOL_MAX = 4
MAX_ATTEMPTS = 5
# Сколько секунд ждать свободного места в пуле.
SLOT_TIMEOUT = 3
# Предел выполнения одного запроса, миллисекунды.
STATEMENT_TIMEOUT_MS = 3000
# Общий ключ блокировки: пока одна транзакция выдаёт номер, остальные ждут.
FLIP_LOCK_KEY = 7_301_001

# Эти параметры дополняют строку DSN, чтобы запрос не зависал без связи.
CONNECT_OPTIONS = {
    "connect_timeout": 3,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
    "tcp_user_timeout": 3000,
}

_pool = None
_pool_lock = threading.Lock()
# Пул psycopg2 при нехватке соединений сразу выдаёт ошибку, а не ждёт.
_slots = threading.BoundedSemaphore(POOL_MAX)


class DatabaseUnavailable(Exception):
    """База не смогла выполнить запрос."""


def _get_pool():
    """Создаёт пул при первом обращении: приложение стартует и без базы."""
    global _pool
    if _pool is not None:
        return _pool

    dsn = os.environ.get("DSN", "").strip()
    if not dsn:
        logger.error("Переменная окружения DSN не задана")
        raise DatabaseUnavailable

    # Подключение идёт вне блокировки, чтобы запросы не ждали друг друга.
    pool = ThreadedConnectionPool(POOL_MIN, POOL_MAX, dsn, **CONNECT_OPTIONS)
    with _pool_lock:
        if _pool is None:
            _pool = pool
        else:
            pool.closeall()
        return _pool


def _checkout(pool):
    """Выдаёт живое соединение с начатой транзакцией."""
    # Все соединения пула могли оборваться, поэтому попыток на одну больше.
    for attempt in range(POOL_MAX + 1):
        connection = pool.getconn()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,)
                )
            return connection
        except Exception:
            pool.putconn(connection, close=True)
            if attempt == POOL_MAX:
                raise


@contextmanager
def _transaction():
    """Одна транзакция на соединении из пула.

    Наружу выходят только UniqueViolation и DatabaseUnavailable.
    """
    if not _slots.acquire(timeout=SLOT_TIMEOUT):
        logger.error("Нет свободного соединения с базой")
        raise DatabaseUnavailable
    try:
        pool = _get_pool()
        connection = _checkout(pool)
        broken = True
        try:
            with connection.cursor() as cursor:
                yield cursor
            connection.commit()
            broken = False
        except errors.UniqueViolation:
            connection.rollback()
            broken = False
            raise
        finally:
            pool.putconn(connection, close=broken)
    except (errors.UniqueViolation, DatabaseUnavailable):
        raise
    except Exception as error:
        # Текст ошибки не пишется: в нём может быть строка подключения.
        logger.error(
            "Ошибка базы: %s, SQLSTATE=%s",
            type(error).__name__,
            getattr(error, "pgcode", None) or "нет",
        )
        raise DatabaseUnavailable from None
    finally:
        _slots.release()


def record_flip(result):
    """Записывает бросок и возвращает пару (номер, время)."""
    # Повтор только после конфликта номера: такая транзакция точно отменена.
    # При обрыве связи повтора нет, бросок мог уже сохраниться.
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with _transaction() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (FLIP_LOCK_KEY,)
                )
                # Отдельный запрос после блокировки видит уже записанные броски.
                cursor.execute(
                    """
                    INSERT INTO public.coinflips
                        (result, flip_number, flipped_at)
                    SELECT %s, COALESCE(MAX(flip_number), 0) + 1,
                           clock_timestamp()
                    FROM public.coinflips
                    RETURNING flip_number, flipped_at
                    """,
                    (result,),
                )
                row = cursor.fetchone()
            return row
        except errors.UniqueViolation:
            logger.warning(
                "Конфликт номера броска, попытка %s из %s",
                attempt,
                MAX_ATTEMPTS,
            )
    logger.error("Бросок не записан: попытки закончились")
    raise DatabaseUnavailable


def get_stats():
    """Возвращает общую статистику бросков."""
    with _transaction() as cursor:
        cursor.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE result = 'heads'),
                   count(*) FILTER (WHERE result = 'tails')
            FROM public.coinflips
            """
        )
        total, heads, tails = cursor.fetchone()
    return {"total": total, "heads": heads, "tails": tails}


def check():
    """Проверяет, что таблица бросков доступна."""
    with _transaction() as cursor:
        cursor.execute("SELECT 1 FROM public.coinflips LIMIT 1")
