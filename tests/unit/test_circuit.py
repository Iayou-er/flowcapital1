"""测试 CircuitBreaker"""
import time
from backend.crawler.circuit import CircuitBreaker


class TestCircuitBreaker:
    def test_initial_state(self):
        cb = CircuitBreaker(name="test")
        assert not cb.is_open()

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, cooldown_seconds=300)
        cb.record_failure()
        assert not cb.is_open()
        cb.record_failure()
        assert cb.is_open()

    def test_record_success_resets(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, cooldown_seconds=300)
        cb.record_failure()
        cb.record_success()
        assert not cb.is_open()

    def test_cooldown_expires(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=0)
        cb.record_failure()
        assert not cb.is_open()
