"""
Redis 客户端封装（异步）
支持缓存读写、连接管理和降级到内存模式
连接在后台进行，不阻塞请求路径
"""

import os
import json
import asyncio
import logging
import time
from typing import Optional, Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_MEM_CACHE_MAX_SIZE = 200


class RedisClient:
    """Redis 异步客户端，后台连接 + 内存降级，首请求零阻塞"""

    def __init__(
        self,
        host: str = None,
        port: int = None,
        db: int = None,
        password: str = None,
    ):
        self._redis: Optional[aioredis.Redis] = None
        self._mem_cache: dict = {}
        self._host = host or os.environ.get("REDIS_HOST", "localhost")
        self._port = int(port or os.environ.get("REDIS_PORT", 6379))
        self._db = int(db or os.environ.get("REDIS_DB", 0))
        self._password = password or os.environ.get("REDIS_PASSWORD") or None
        self._last_retry = 0
        self._connecting = False

    def _ensure_connection(self):
        """非阻塞：后台发起连接，不等待结果"""
        now = time.time()
        if self._redis is not None:
            return
        if self._connecting:
            return
        if now - self._last_retry < 60:
            return
        self._last_retry = now
        asyncio.create_task(self._connect())

    def _cleanup_expired(self):
        now = time.time()
        expired = [k for k, v in self._mem_cache.items() if v.get("expire", 0) < now]
        for k in expired:
            del self._mem_cache[k]

    async def _connect(self):
        self._connecting = True
        try:
            self._redis = aioredis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            await self._redis.ping()
            logger.info("Redis 已连接 %s:%s", self._host, self._port)
        except Exception as e:
            logger.warning("Redis 连接失败，使用内存缓存降级: %s", e)
            self._redis = None
        finally:
            self._connecting = False

    async def get(self, key: str) -> Any:
        self._ensure_connection()
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception as e:
                logger.warning("Redis GET 失败: %s", e)
                self._redis = None  # 标记断开，下次重连
            return None
        self._cleanup_expired()
        entry = self._mem_cache.get(key)
        if entry:
            if entry.get("expire", 0) < time.time():
                del self._mem_cache[key]
                return None
            return entry.get("value")
        return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        self._ensure_connection()
        if self._redis:
            try:
                raw = json.dumps(value, ensure_ascii=False)
                await self._redis.setex(key, ttl, raw)
                return True
            except Exception as e:
                logger.warning("Redis SET 失败: %s", e)
                self._redis = None
                return False
        if len(self._mem_cache) >= _MEM_CACHE_MAX_SIZE:
            self._cleanup_expired()
            if len(self._mem_cache) >= _MEM_CACHE_MAX_SIZE:
                oldest_key = next(iter(self._mem_cache))
                del self._mem_cache[oldest_key]
        self._mem_cache[key] = {"value": value, "expire": time.time() + ttl}
        return True

    async def delete(self, key: str) -> bool:
        self._ensure_connection()
        if self._redis:
            try:
                await self._redis.delete(key)
                return True
            except Exception as e:
                logger.warning("Redis DELETE 失败: %s", e)
                self._redis = None
                return False
        self._mem_cache.pop(key, None)
        return True

    async def delete_pattern(self, pattern: str) -> int:
        """删除匹配 pattern 的所有键"""
        self._ensure_connection()
        count = 0
        if self._redis:
            try:
                cursor = 0
                while True:
                    cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
                    if keys:
                        await self._redis.delete(*keys)
                        count += len(keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.warning("Redis DELETE pattern 失败: %s", e)
                self._redis = None
        prefix = pattern.rstrip("*")
        to_delete = [k for k in self._mem_cache if k.startswith(prefix)]
        for k in to_delete:
            del self._mem_cache[k]
        count += len(to_delete)
        return count

    async def is_available(self) -> bool:
        return self._redis is not None

    async def flushdb(self) -> bool:
        self._ensure_connection()
        if self._redis:
            try:
                await self._redis.flushdb()
                return True
            except Exception as e:
                logger.warning("Redis FLUSHDB 失败: %s", e)
                self._redis = None
                return False
        self._mem_cache.clear()
        return True

    # ── 同步兼容方法（仅内存缓存，仅供遗留同步代码使用） ──

    def get_sync(self, key: str) -> Any:
        self._cleanup_expired()
        entry = self._mem_cache.get(key)
        if entry:
            if entry.get("expire", 0) < time.time():
                del self._mem_cache[key]
                return None
            return entry.get("value")
        return None

    def set_sync(self, key: str, value: Any, ttl: int = 300) -> bool:
        if len(self._mem_cache) >= _MEM_CACHE_MAX_SIZE:
            self._cleanup_expired()
            if len(self._mem_cache) >= _MEM_CACHE_MAX_SIZE:
                oldest_key = next(iter(self._mem_cache))
                del self._mem_cache[oldest_key]
        self._mem_cache[key] = {"value": value, "expire": time.time() + ttl}
        return True

    def delete_sync(self, key: str) -> bool:
        self._mem_cache.pop(key, None)
        return True


redis_client = RedisClient()
