"""测试 CircuitBreaker（含半开状态）"""
import time
import threading
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

    def test_cooldown_enters_half_open(self):
        """冷却后第一次调用放行（探测），第二次拦截"""
        cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=0)
        cb.record_failure()
        # 冷却=0，立即进入半开
        assert not cb.is_open()   # 放行探测请求
        assert cb.is_open()       # 探测期间拦截其他请求

    def test_probe_success_closes_breaker(self):
        """探测成功 → 断路器关闭"""
        cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=0)
        cb.record_failure()
        assert not cb.is_open()   # 探测放行
        cb.record_success()       # 探测成功
        assert not cb.is_open()   # 断路器已关闭

    def test_probe_failure_reopens_breaker(self):
        """探测失败 → 断路器重新打开（强制冷却期内不允许新探测）"""
        cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=0.01)
        cb.record_failure()
        time.sleep(0.02)          # 等冷却期结束
        assert not cb.is_open()   # 探测放行
        cb.record_failure()       # 探测失败
        assert cb.is_open()       # 重新打开，冷却期内不允许新探测

    def test_probe_timeout_reopens(self):
        """探测超时 → 断路器重新打开"""
        cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_seconds=0, probe_timeout=0)
        cb.record_failure()
        assert not cb.is_open()   # 探测放行
        # probe_timeout=0，下次检查时探测已超时
        time.sleep(0.01)
        assert cb.is_open()       # 超时重回 open

    def test_thread_safety(self):
        """多线程并发调用不崩溃"""
        cb = CircuitBreaker(name="test", failure_threshold=5, cooldown_seconds=1)
        errors = []

        def worker():
            try:
                for _ in range(100):
                    cb.record_failure()
                    cb.is_open()
                    cb.record_success()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
