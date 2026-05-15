"""数据源断路器"""
import time
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """数据源断路器：连续失败 N 次后暂退避"""

    def __init__(self, name: str = "", failure_threshold: int = 3, cooldown_seconds: int = 300):
        self.name = name
        self.threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self._failures: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}

    def is_open(self, name: str = None) -> bool:
        key = name or self.name
        failures = self._failures.get(key, 0)
        if failures >= self.threshold:
            elapsed = time.time() - self._last_failure_time.get(key, 0)
            if elapsed < self.cooldown:
                return True
            self._failures[key] = 0
        return False

    def record_failure(self, name: str = None):
        key = name or self.name
        self._failures[key] = self._failures.get(key, 0) + 1
        self._last_failure_time[key] = time.time()

    def record_success(self, name: str = None):
        key = name or self.name
        self._failures[key] = 0
