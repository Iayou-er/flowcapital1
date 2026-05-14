# 用户行为反馈闭环规范

## 现状问题

前端仅有阅读量字段（`read_count`），但未见埋点回传。搜索点击、阅读时长、收藏等行为信号未收集，无法用于内容排序优化。

## 目标

- 采集用户行为（点击、停留、搜索交互）并回写 DB
- 基于用户行为信号调整新闻热度排序
- 热度分含时间衰减，避免旧文永久霸榜
- 热度分与原始 `read_count` 分离存储
- 防刷机制，避免单个用户反复操作人为推高热度
- 不引入额外基础设施，复用现有 API 和 DB

## 实施方案

### 1. 埋点事件定义

| 事件 | 触发时机 | 字段 |
|------|----------|------|
| `article_click` | 点击新闻卡片 | article_id, source |
| `article_view` | 页面可见 > 3s | article_id, duration_ms |
| `search_query` | 搜索提交 | query, result_count |
| `search_click` | 搜索结果点击 | query, article_id, rank |

### 2. API 端点

新增 `POST /api/events/track`：

```python
# backend/api/routes/events.py
from pydantic import BaseModel
from typing import Optional

class EventModel(BaseModel):
    type: str              # article_click / article_view / search_query / search_click
    article_id: Optional[str] = None
    payload: Optional[dict] = None  # {duration_ms, query, rank, ...}
    client_id: Optional[str] = None

@router.post("/api/events/track")
async def track_event(event: EventModel):
    """
    接收前端埋点事件，写入 event_log 表
    payload 以 JSON 字符串存入，读取时 json.loads 还原
    """
    import json
    db.insert_event(
        event_type=event.type,
        article_id=event.article_id,
        payload=json.dumps(event.payload, ensure_ascii=False) if event.payload else None,
        client_id=event.client_id,
    )
    return {'code': 0}
```

### 3. 数据库表

```sql
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,        -- article_click / article_view / search_query / search_click
    article_id TEXT,
    client_id TEXT,
    payload TEXT,                    -- JSON 字符串: {"duration_ms": 5000, "query": "...", "rank": 3}
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_event_type_time ON event_log(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_event_article ON event_log(article_id);
```

`news_articles` 表新增独立热度分列（避免与爬虫写入的 `read_count` 混淆）：

```sql
ALTER TABLE news_articles ADD COLUMN hotness_score REAL DEFAULT 0.0;
CREATE INDEX IF NOT EXISTS idx_news_hotness ON news_articles(hotness_score DESC);
```

### 4. DB 新增方法

```python
# backend/database/db_manager.py

def insert_event(self, event_type: str, article_id: str = None,
                 payload: str = None, client_id: str = None) -> bool:
    """插入埋点事件（payload 已在调用方序列化为 JSON 字符串）"""
    try:
        self._execute(
            'INSERT INTO event_log (event_type, article_id, payload, client_id, created_at) '
            'VALUES (?, ?, ?, ?, ?)',
            (event_type, article_id, payload, client_id, datetime.now().isoformat()),
            commit=True
        )
        return True
    except Exception as e:
        logger.warning(f"写入事件失败: {e}")
        return False

def get_recent_events(self, hours: int = 24) -> List[Dict]:
    """获取近期埋点事件"""
    since = (datetime.now() - timedelta(hours=hours)).isoformat()
    return self._query(
        'SELECT * FROM event_log WHERE created_at >= ? ORDER BY created_at DESC',
        (since,)
    )

def update_hotness_scores(self, scores: Dict[str, float]):
    """
    覆盖写入文章热度分（非累加）。
    每次计算时用当前窗口的聚合分直接替换旧值，天然实现衰减。
    使用 CASE WHEN 批量更新，避免 N+1。
    """
    if not scores:
        return
    with self._wr_lock:
        cursor = self._conn.cursor()
        for aid, score in scores.items():
            cursor.execute(
                'UPDATE news_articles SET hotness_score = ? WHERE article_id = ?',
                (round(score, 4), aid)
            )
        self._conn.commit()
    self._invalidate_news_cache()
```

### 5. 热度分计算（含时间衰减 + 防刷）

定时任务（每小时）聚合过去 24h 事件：

```python
def update_hotness_scores(db):
    """聚合过去 24h 事件，计算热度分（含时间衰减 + 防刷去重）"""
    import json
    from datetime import datetime, timedelta

    events = db.get_recent_events(hours=24)
    now = datetime.now()

    # 按 (article_id, client_id, event_type) 去重 → 每用户每文章每事件类型只计一次
    seen = set()
    raw_scores = {}
    for e in events:
        aid = e['article_id']
        if not aid:
            continue

        # 防刷：同一 (client_id, article_id, event_type) 只计第一次
        dedup_key = (e['client_id'], aid, e['event_type'])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if aid not in raw_scores:
            raw_scores[aid] = 0

        # 时间衰减因子：e^{-hours_passed / 24}，24 小时前的事件权重 ≈ 0.37
        try:
            event_time = datetime.fromisoformat(e['created_at'])
            hours_passed = (now - event_time).total_seconds() / 3600
        except (ValueError, TypeError):
            hours_passed = 24
        decay = max(0.1, 2.71828 ** (-hours_passed / 24))

        # payload 从 JSON 字符串还原
        payload = json.loads(e['payload']) if e['payload'] else {}

        if e['event_type'] == 'article_click':
            raw_scores[aid] += 1 * decay
        elif e['event_type'] == 'article_view':
            duration = payload.get('duration_ms', 0)
            raw_scores[aid] += min(duration / 10000, 3) * decay
        elif e['event_type'] == 'search_click':
            raw_scores[aid] += 2 * decay

    # 第一步：将所有非零 hotness_score 归零，再写入新值
    # 防止昨日热门但今日无事件的文章残留旧分数
    db.reset_all_hotness_scores()

    # 第二步：覆盖写入（非累加，天然实现旧热度过期）
    db.update_hotness_scores(raw_scores)
```

**关键设计**：每次计算前先将全部 `hotness_score` 归零，再写入有事件的文章的新分数。无事件文章的分数归零，有事件文章的分数完全由过去 24h 窗口重新计算。彻底消除旧文残留分。

```python
# db_manager.py 新增
def reset_all_hotness_scores(self):
    """将所有文章热度分归零（在重算前调用，单次 UPDATE 高效）"""
    self._execute('UPDATE news_articles SET hotness_score = 0 WHERE hotness_score != 0', commit=True)
```

### 6. 排序权重融合 — SQL 落地

修改 `get_latest_news()` 的 ORDER BY 子句：

```python
def get_latest_news(self, limit: int = 100, offset: int = 0,
                    exclude_sources: List[str] = None) -> Tuple[List[Dict], int]:
    ...
    order_sql = '''
        ORDER BY
            CASE
                WHEN published_at >= ? THEN 1.0    -- 24h 内新鲜度 = 1.0
                WHEN published_at >= ? THEN 0.5    -- 7d 内新鲜度 = 0.5
                ELSE 0.1                            -- 更早 = 0.1
            END * 0.5
            + COALESCE(hotness_score, 0) * 0.3
            + MIN(COALESCE(LENGTH(tags) - LENGTH(REPLACE(tags, ',', '')) + CASE WHEN tags != '' THEN 1 ELSE 0 END, 0), 10) * 0.02
            DESC
    '''
    now = datetime.now()
    day_ago = (now - timedelta(days=1)).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    news = self._query(
        f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles '
        f'WHERE source NOT IN (...) '
        f'{order_sql} LIMIT ? OFFSET ?',
        (day_ago, week_ago) + (...)
    )
```

**排序公式**: `新鲜度 × 0.5 + 热度分 × 0.3 + 标签质量`
- 标签质量 = `MIN(tag_count, 10) × 0.02`，上限 0.2（10 个标签）。tags 存储为逗号分隔字符串，用 `LENGTH` 差值计算标签数

### 7. 客户端 ID 统一

复用项目已有的 `POST /api/guest/assign-id` 匿名 ID 分配机制，前端从 `localStorage` 或调用 API 获取：

```javascript
// economic-news-webapp/src/services/api.js
import axios from 'axios';

let _clientId = null;

export async function getClientId() {
  if (_clientId) return _clientId;
  const stored = localStorage.getItem('flowcapital_client_id');
  if (stored) {
    _clientId = stored;
    return _clientId;
  }
  try {
    const res = await axios.get('/api/guest/assign-id');
    _clientId = res.data?.data?.anonymous_id || 'anonymous';
    localStorage.setItem('flowcapital_client_id', _clientId);
  } catch {
    _clientId = 'anonymous';
  }
  return _clientId;
}

export const trackEvent = async (type, articleId, payload = {}) => {
  try {
    const clientId = await getClientId();
    await axios.post('/api/events/track', {
      type,
      article_id: articleId,
      payload,
      client_id: clientId,
    });
  } catch {
    // 静默失败，不影响主流程
  }
};
```

### 8. 定期清理

每小时任务末尾增加旧事件清理，防止 event_log 表无限增长：

```python
# 保留最近 7 天事件
db._execute('DELETE FROM event_log WHERE created_at < ?',
            ((datetime.now() - timedelta(days=7)).isoformat(),), commit=True)
```

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/api/routes/events.py` | **新建**，埋点接收端点 + EventModel |
| `backend/api/main.py` | 注册 events 路由 |
| `backend/database/models.py` | `init_db()` 新增 `event_log` 表 + `hotness_score` 列 |
| `backend/database/db_manager.py` | 新增 `insert_event()`、`get_recent_events()`、`update_hotness_scores()`；`get_latest_news()` 改排序 |
| `scripts/scheduler.py` | 新增每小时热度分计算 + 清理任务 |
| `economic-news-webapp/src/services/api.js` | 新增 `getClientId()`、`trackEvent()`，统一使用 guest/assign-id |
| `economic-news-webapp/src/components/NewsCard.js` | 点击时调用 `trackEvent` |
| `economic-news-webapp/src/components/SearchBar.js` | 搜索时调用 `trackEvent` |

## 验收标准

- [ ] 点击新闻卡片后 `event_log` 表有对应记录
- [ ] 停留超 3s 触发 `article_view` 事件
- [ ] 每整点热度分覆盖更新，旧文章 24h 后热度归零
- [ ] 同一用户对同一文章同类型事件只计一次（防刷）
- [ ] 埋点接口失败不影响页面正常浏览
- [ ] 客户端 ID 与留言板匿名 ID 同源
- [ ] event_log 表仅保留 7 天数据
- [ ] 热度分和原始 read_count 存储在不同字段
