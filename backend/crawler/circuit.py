"""数据源断路器（线程安全，含半开状态）"""
import time
import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """数据源断路器：连续失败 N 次后退避，冷却后半开探测"""

    def __init__(self, name: str = "", failure_threshold: int = 3,
                 cooldown_seconds: int = 300, probe_timeout: int = 30):
        self.name = name
        self.threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self.probe_timeout = probe_timeout
        self._failures: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}
        self._half_open: Dict[str, bool] = {}
        self._probe_started: Dict[str, float] = {}
        self._open_until: Dict[str, float] = {}  # 探测失败后强制 open 到此时间
        self._lock = threading.Lock()

    def is_open(self, name: str = None) -> bool:
        key = name or self.name
        with self._lock:
            # 探测失败后强制 open 期间
            open_until = self._open_until.get(key, 0)
            if time.time() < open_until:
                return True

            failures = self._failures.get(key, 0)
            if failures < self.threshold:
                return False

            elapsed = time.time() - self._last_failure_time.get(key, 0)
            if elapsed < self.cooldown:
                return True

            # 冷却期结束，进入半开状态
            if key in self._half_open:
                # 探测已发出，检查是否超时
                if time.time() - self._probe_started.get(key, 0) > self.probe_timeout:
                    del self._half_open[key]
                    self._open_until[key] = time.time() + self.cooldown
                    return True  # 探测超时，重回 open
                return True  # 探测进行中，拦截其他请求

            # 发起探测（放行 1 个请求）
            self._half_open[key] = True
            self._probe_started[key] = time.time()
            return False

    def record_failure(self, name: str = None):
        key = name or self.name
        with self._lock:
            was_half_open = key in self._half_open
            self._failures[key] = self._failures.get(key, 0) + 1
            self._last_failure_time[key] = time.time()
            self._half_open.pop(key, None)
            self._probe_started.pop(key, None)
            # 探测失败：强制 open 一个冷却周期，防止立即重试
            if was_half_open:
                self._open_until[key] = time.time() + self.cooldown

    def record_success(self, name: str = None):
        key = name or self.name
        with self._lock:
            self._failures[key] = 0
            self._half_open.pop(key, None)
            self._probe_started.pop(key, None)
            self._open_until.pop(key, None)
