"""缓存失效管理"""
import logging

logger = logging.getLogger(__name__)

_CACHE_PREFIXES = ("news:latest:", "news:media:", "news:category:")


class CacheManager:
    @staticmethod
    async def invalidate_news_list():
        try:
            from .redis_client import redis_client
            for prefix in _CACHE_PREFIXES:
                await redis_client.delete_pattern(f"{prefix}*")
        except Exception as e:
            logger.debug("缓存失效忽略: %s", e)

    @staticmethod
    def invalidate_news_list_sync():
        """同步版本，供遗留同步代码使用（仅内存缓存）"""
        try:
            from .redis_client import redis_client
            for prefix in _CACHE_PREFIXES:
                redis_client.delete_sync(f"{prefix}*")
        except Exception as e:
            logger.debug("缓存失效忽略(同步): %s", e)
