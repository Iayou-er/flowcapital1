"""
Redis 客户端封装
支持缓存读写、连接管理和降级到内存模式
"""

import os
import json
import logging
import time
from typing import Optional, Any

import redis

logger = logging.getLogger(__name__)

_MEM_CACHE_MAX_SIZE = 200


class RedisClient:
    """Redis 客户端，连接失败自动降级到内存模式"""

    def __init__(
        self,
        host: str = None,
        port: int = None,
        db: int = None,
        password: str = None,
    ):
        self._redis: Optional[redis.Redis] = None
        self._mem_cache: dict = {}  # key -> {"value": ..., "expire": timestamp}
        self._host = host or os.environ.get("REDIS_HOST", "localhost")
        self._port = int(port or os.environ.get("REDIS_PORT", 6379))
        self._db = int(db or os.environ.get("REDIS_DB", 0))
        self._password = password or os.environ.get("REDIS_PASSWORD") or None
        self._connected = False
        self._tried_connect = False
        self._last_retry = 0

    def _ensure_connection(self):
        """懒连接，失败后每 60s 重试一次"""
        now = time.time()
        if self._tried_connect:
            # 已连接或距上次重试不足 60s
            if self._redis or now - self._last_retry < 60:
                return
        self._tried_connect = True
        self._last_retry = now
        self._connect()

    def _cleanup_expired(self):
        """清理过期内存缓存条目"""
        now = time.time()
        expired = [k for k, v in self._mem_cache.items() if v.get("expire", 0) < now]
        for k in expired:
            del self._mem_cache[k]

    def _connect(self):
        try:
            self._redis = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                password=self._password,
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            )
            self._redis.ping()
            logger.info(f"Redis 已连接 {self._host}:{self._port}")
        except Exception as e:
            logger.warning(f"Redis 连接失败，使用内存缓存降级: {e}")
            self._redis = None

    def get(self, key: str) -> Any:
        """获取缓存值"""
        self._ensure_connection()
        if self._redis:
            try:
                raw = self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception as e:
                logger.warning(f"Redis GET 失败: {e}")
            return None
        self._cleanup_expired()
        entry = self._mem_cache.get(key)
        if entry:
            if entry.get("expire", 0) < time.time():
                del self._mem_cache[key]
                return None
            return entry.get("value")
        return None

    def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """设置缓存值，ttl 单位秒"""
        self._ensure_connection()
        if self._redis:
            try:
                raw = json.dumps(value, ensure_ascii=False)
                self._redis.setex(key, ttl, raw)
                return True
            except Exception as e:
                logger.warning(f"Redis SET 失败: {e}")
                return False
        # 达到上限时清理过期+最老条目
        if len(self._mem_cache) >= _MEM_CACHE_MAX_SIZE:
            self._cleanup_expired()
            if len(self._mem_cache) >= _MEM_CACHE_MAX_SIZE:
                oldest_key = next(iter(self._mem_cache))
                del self._mem_cache[oldest_key]
        self._mem_cache[key] = {"value": value, "expire": time.time() + ttl}
        return True

    def delete(self, key: str) -> bool:
        self._ensure_connection()
        if self._redis:
            try:
                self._redis.delete(key)
                return True
            except Exception as e:
                logger.warning(f"Redis DELETE 失败: {e}")
                return False
        self._mem_cache.pop(key, None)
        return True

    def is_available(self) -> bool:
        return self._redis is not None

    def flushdb(self) -> bool:
        """清空当前数据库（调试用）"""
        self._ensure_connection()
        if self._redis:
            try:
                self._redis.flushdb()
                return True
            except Exception as e:
                logger.warning(f"Redis FLUSHDB 失败: {e}")
                return False
        self._mem_cache.clear()
        return True


# 全局单例
redis_client = RedisClient()
