"""SQLite 写锁自动重试装饰器"""
import asyncio
import logging
import sqlite3
from functools import wraps

from sqlalchemy import exc as sa_exc

logger = logging.getLogger(__name__)


def retry_on_lock(max_retries: int = 3, delay: float = 0.1):
    """SQLite database is locked 自动重试

    匹配策略：先按异常类（sqlite3.OperationalError / sqlalchemy.exc.OperationalError），
    再 fallback 字符串匹配，覆盖不同驱动封装的差异。
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (sqlite3.OperationalError, sa_exc.OperationalError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(
                            "数据库锁，重试 %d/%d: %s", attempt + 1, max_retries, e
                        )
                        await asyncio.sleep(delay * (2 ** attempt))
                        continue
                    raise
                except Exception as e:
                    # fallback: aiosqlite 或其他封装可能只透传字符串消息
                    msg = str(e).lower()
                    if "database is locked" in msg or "operationalerror" in msg:
                        if attempt < max_retries - 1:
                            logger.warning(
                                "数据库锁(字符串匹配)，重试 %d/%d: %s",
                                attempt + 1, max_retries, e,
                            )
                            await asyncio.sleep(delay * (2 ** attempt))
                            continue
                    raise
        return wrapper
    return decorator
