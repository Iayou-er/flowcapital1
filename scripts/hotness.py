"""热度计算：时间衰减公式 + 批量更新"""
import json
import logging
import math
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def compute_hotness(events: list, article_id: str, now: datetime = None) -> float:
    """热度 = 时间衰减 × (点击 + 阅读时长 + 搜索点击)

    衰减因子: e^(-hours_passed / 24)，最小 0.1
    """
    now = now or datetime.now()
    score = 0.0

    for e in events:
        if e.get('article_id') != article_id:
            continue

        try:
            event_time = datetime.fromisoformat(e['created_at'])
            hours_passed = (now - event_time).total_seconds() / 3600
        except (ValueError, TypeError, KeyError):
            hours_passed = 24

        decay = max(0.1, math.e ** (-hours_passed / 24))
        payload = json.loads(e['payload']) if e.get('payload') else {}

        if e['event_type'] == 'article_click':
            score += decay
        elif e['event_type'] == 'article_view':
            duration = payload.get('duration_ms', 0)
            score += min(duration / 10000, 3) * decay
        elif e['event_type'] == 'search_click':
            score += 2 * decay

    return round(score, 4)


async def update_hotness_scores(db, prometheus_metric=None):
    """聚合过去 24h 事件，计算热度分（含时间衰减 + 防刷去重）"""
    events = await db.get_recent_events(hours=24)
    now = datetime.now()

    seen = set()
    raw_scores = {}
    for e in events:
        aid = e['article_id']
        if not aid:
            continue

        dedup_key = (e['client_id'], aid, e['event_type'])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if aid not in raw_scores:
            raw_scores[aid] = 0

        try:
            event_time = datetime.fromisoformat(e['created_at'])
            hours_passed = (now - event_time).total_seconds() / 3600
        except (ValueError, TypeError, KeyError):
            hours_passed = 24

        decay = max(0.1, math.e ** (-hours_passed / 24))
        payload = json.loads(e['payload']) if e.get('payload') else {}

        if e['event_type'] == 'article_click':
            raw_scores[aid] += decay
        elif e['event_type'] == 'article_view':
            duration = payload.get('duration_ms', 0)
            raw_scores[aid] += min(duration / 10000, 3) * decay
        elif e['event_type'] == 'search_click':
            raw_scores[aid] += 2 * decay

    await db.reset_all_hotness_scores()
    await db.update_hotness_scores(raw_scores)

    if raw_scores:
        logger.info("热度分更新完成: %d 篇文章", len(raw_scores))

    if prometheus_metric:
        prometheus_metric.set(len(raw_scores))

    # 清理 7 天前的旧事件
    await db.execute_write(
        'DELETE FROM event_log WHERE created_at < :cutoff',
        {"cutoff": (datetime.now() - timedelta(days=7)).isoformat()}
    )
