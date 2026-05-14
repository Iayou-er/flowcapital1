"""
简易内存频率限制器
基于客户端 IP 地址进行限流，无需外部依赖
"""
import time
import logging
from collections import defaultdict
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """滑动窗口频率限制器"""

    def __init__(self):
        # {ip: [(timestamp, count), ...]}
        self._requests: dict = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, ip: str, limit: int = 60, window: int = 60) -> bool:
        """
        检查请求是否允许
        :param ip: 客户端IP
        :param limit: 时间窗口内允许的请求数
        :param window: 时间窗口（秒）
        :return: 是否允许
        """
        now = time.time()
        cutoff = now - window

        with self._lock:
            # 清理过期记录
            self._requests[ip] = [t for t in self._requests[ip] if t > cutoff]

            if len(self._requests[ip]) >= limit:
                return False

            self._requests[ip].append(now)
            return True

    def remaining(self, ip: str, limit: int = 60, window: int = 60) -> int:
        """返回剩余可请求次数"""
        now = time.time()
        cutoff = now - window
        with self._lock:
            current = len([t for t in self._requests[ip] if t > cutoff])
        return max(0, limit - current)

    def retry_after(self, ip: str, window: int = 60) -> int:
        """返回最早重试时间（秒）"""
        now = time.time()
        cutoff = now - window
        with self._lock:
            timestamps = [t for t in self._requests.get(ip, []) if t > cutoff]
        if not timestamps:
            return 1
        return max(1, int(timestamps[0] + window - now))

    def cleanup(self):
        """清理所有过期数据"""
        now = time.time()
        with self._lock:
            for ip in list(self._requests.keys()):
                self._requests[ip] = [t for t in self._requests[ip] if t > now - 300]
                if not self._requests[ip]:
                    del self._requests[ip]


# 全局单例
rate_limiter = RateLimiter()


# 不同端点的频率限制配置
# (limit, window_seconds)
RATE_LIMIT_CONFIG = {
    "/api/guest/submit": (5, 60),         # 匿名留言: 5次/分钟, 防刷
    "/api/guest/message": (5, 60),        # 留言提交: 5次/分钟, 防刷
    "/api/guest/login": (10, 60),         # 登录: 10次/分钟
    "/api/guest/assign-id": (20, 60),     # 分配ID: 20次/分钟
    "/api/news/latest": (60, 60),         # 最新新闻: 60次/分钟
    "/api/news/search": (30, 60),         # 搜索: 30次/分钟
    "/api/analysis": (30, 60),            # 分析: 30次/分钟
    "/api/graph-rag/query": (20, 60),     # GraphRAG: 20次/分钟
    "/api/graph-rag/entity-timeline": (20, 60),  # 实体时间线: 20次/分钟
    "/api/health": (120, 60),             # 健康检查: 120次/分钟
}
# 默认限制（未配置的端点）
DEFAULT_RATE_LIMIT = (100, 60)
