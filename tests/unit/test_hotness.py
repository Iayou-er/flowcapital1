"""测试热度计算"""
from scripts.hotness import compute_hotness


class TestComputeHotness:
    def test_no_events(self):
        assert compute_hotness([], "aid1") == 0.0

    def test_article_click(self):
        events = [{
            "article_id": "aid1",
            "event_type": "article_click",
            "client_id": "c1",
            "payload": None,
            "created_at": "2025-01-01T00:00:00",
        }]
        from datetime import datetime
        now = datetime(2025, 1, 1, 1, 0, 0)
        score = compute_hotness(events, "aid1", now=now)
        assert score > 0

    def test_different_article(self):
        events = [{
            "article_id": "aid2",
            "event_type": "article_click",
            "client_id": "c1",
            "payload": None,
            "created_at": "2025-01-01T00:00:00",
        }]
        assert compute_hotness(events, "aid1") == 0.0
