# 跨批次去重规范

## 现状问题

SimHash 去重仅在单次爬取批次内生效（`deduplicator.deduplicate(news_list)`），无法拦截跨批次重复——同一新闻隔 15 分钟再次爬取仍会入库。

## 目标

- 写库前做跨批次比对，已存在的新闻跳过
- 兼顾性能，不阻塞高频写入
- 输出去重指标，便于发现异常（如某源大量重复爬取）

## 实施方案

### 1. URL 唯一索引（主防线）

在 `news_articles` 表的 `url` 列添加唯一索引（仅非空值）：

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_url ON news_articles(url) WHERE url IS NOT NULL AND url != '';
```

**注意**: 保持现有 `INSERT OR REPLACE` 语义不变——当 `article_id` 冲突时更新内容。URL 唯一索引在极罕见场景（不同 URL 的 MD5[:16] 碰撞产生相同 article_id）下触发 REPLACE 替换旧行。`INSERT OR REPLACE` 不会抛 IntegrityError，逐条改为仅为了输出 `rowcount` 计数，无需捕获异常。

```python
for article in batch_insert:
    cursor.execute('INSERT OR REPLACE INTO news_articles (...) VALUES (...)')
    if cursor.rowcount > 0:
        count += 1
```

### 2. SimHash 跨批次比对（辅助防线）

对无 URL 的新闻（部分源不提供 URL），在写库前做跨批次 SimHash。利用 `RedisClient` 现有 `set`/`get` 方法，按 `simhash:{article_id}` 存储指纹：

```python
def is_duplicate_cross_batch(self, text: str, redis_client, db) -> bool:
    """
    与近期（24h内）已入库新闻做 SimHash 比对。
    从 DB 查询最近 500 条新闻的 article_id，再从 Redis 批量获取指纹。

    注意：此方法仅对当前批次中无 url 的新闻调用；
    有 URL 的新闻跨批次由 INSERT OR REPLACE 自动处理。
    """
    fingerprint = self._compute_fingerprint(text)

    # 从 DB 获取最近入库的 article_id 列表（轻量查询，不含 content）
    recent_ids = db.get_recent_article_ids(limit=500)

    # 从 Redis 批量获取指纹（RedisClient 没有 mget，逐个 get）
    for aid in recent_ids:
        fp_str = redis_client.get(f'simhash:{aid}')
        if fp_str and self._hamming_distance(fingerprint, int(fp_str)) <= 3:
            return True
    return False
```

### 3. 指纹缓存（仅用现有 RedisClient API）

利用 `RedisClient` 已有的 `set` 方法，每篇入库新闻单独存一条指纹：

```python
# 每篇入库新闻计算指纹并写入 Redis（TTL 24h = 86400s）
fingerprint = deduplicator._compute_fingerprint(title + ' ' + (content or summary or ''))
redis_client.set(f'simhash:{article_id}', str(fingerprint), ttl=86400)
```

无需新增 `lpush`/`ltrim`。跨批次比对时通过 `db.get_recent_article_ids()` 获取 ID 列表再逐个查 Redis。500 次 Redis get 在单次爬取中延迟可控（<50ms）。

### 4. DB 新增方法

```python
# backend/database/db_manager.py
def get_recent_article_ids(self, limit: int = 500) -> List[str]:
    """获取最近入库的 article_id 列表（用于去重比对，不含 content）"""
    rows = self._query(
        'SELECT article_id FROM news_articles ORDER BY id DESC LIMIT ?',
        (limit,)
    )
    return [r['article_id'] for r in rows]

def url_exists(self, url: str) -> bool:
    """检查 URL 是否已存在（空 URL 直接返回 False）。
    注意：仅用于监控/统计，不用于去重流程。有 URL 的文章跨批次由 INSERT OR REPLACE 自动处理。"""
    if not url or not url.strip():
        return False
    row = self._query_one(
        'SELECT 1 FROM news_articles WHERE url = ? LIMIT 1',
        (url,)
    )
    return row is not None
```

### 5. 去重流程调整

```
原流程: crawl → simhash(批次内) → save
新流程: crawl → simhash(批次内) → url批次内去重 → simhash(跨批次,仅无url新闻) → save(写指纹到Redis)
```

**关键设计决策**: 不做 DB 查询的 URL 跨批次过滤。因为 `article_id = MD5(url)`，同 URL 必同 article_id。`INSERT OR REPLACE` 在 article_id 冲突时自动更新内容——这意味着：
- 同一 URL 再次爬取 → 同一 article_id → REPLACE 更新内容（正确，文章可能有更新）
- 同一 URL 在同一批次内出现多次 → 仅保留一条（批次内去重）
- URL 唯一索引仅在 coding 错误导致不同 URL 产生相同 article_id 时作为最后防线

`crawl_and_analyze_news()` 中：

```python
# 1.5.1 SimHash 批次内去重
deduplicator = NewsDeduplicator(threshold=4)
before_batch = len(news_list)
news_list = deduplicator.deduplicate(news_list)
batch_deduped = before_batch - len(news_list)

# 1.5.2 URL 批次内去重（同批次中相同 URL 只保留一条）
before_url = len(news_list)
seen_urls = set()
url_unique = []
for news in news_list:
    url = news.get('url', '')
    if url and url in seen_urls:
        continue
    if url:
        seen_urls.add(url)
    url_unique.append(news)
news_list = url_unique
url_deduped = before_url - len(news_list)

# 1.5.3 SimHash 跨批次去重（仅对无 URL 的新闻）
from backend.database.redis_client import redis_client
before_cross = len(news_list)
filtered = []
for news in news_list:
    url = news.get('url', '')
    if url:
        filtered.append(news)  # 有 URL 的由 INSERT OR REPLACE 自动更新
    elif not deduplicator.is_duplicate_cross_batch(
        news.get('title', '') + ' ' + (news.get('content', '') or news.get('summary', '')),
        redis_client, db
    ):
        filtered.append(news)
news_list = filtered
cross_deduped = before_cross - len(news_list)

# 输出去重指标
logger.info(f"去重统计: 批次内SimHash={batch_deduped}, 批次内URL={url_deduped}, 跨批次SimHash={cross_deduped}, 剩余={len(news_list)}")
```

**说明**: 跨批次 SimHash 指纹在第一次写入后才写入 Redis，因此无 URL 新闻首次出现不会被拦截，第二次爬取时方可命中。有 URL 的新闻跨批次由 INSERT OR REPLACE 自动更新内容——不做预过滤，不会阻断内容更新。

### 6. 写库时写入指纹

在 `save_news_articles` 成功后立即写入 SimHash 指纹：

```python
# scheduler.py 中 save_news_articles 调用之后
for news in news_list[:]:
    url = news.get('url', '')
    text = news.get('title', '') + ' ' + (news.get('content', '') or news.get('summary', ''))
    if text.strip():
        fp = deduplicator._compute_fingerprint(text)
        # 直接用 article_id 作为 key，与 is_duplicate_cross_batch 查询 key 一致
        redis_client.set(f'simhash:{article_id}', str(fp), ttl=86400)
```

### 7. 去重率监控

在 `crawl_and_analyze_news()` 的汇总日志中增加去重率：

```python
total_crawled = batch_deduped + len(news_list)
dedup_rate = (batch_deduped + url_deduped + cross_deduped) / max(total_crawled, 1) * 100
logger.info(f"爬取 {total_crawled} 条，去重 {batch_deduped + url_deduped + cross_deduped} 条 ({dedup_rate:.1f}%)，入库 {len(news_list)} 条")
```

去重率持续 > 50% 说明数据源重复严重，去重率持续 0% 说明去重可能失效——均可作为运维告警信号。

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/database/models.py` | `url` 字段加唯一索引（init_db 中） |
| `backend/database/db_manager.py` | `save_news_articles()` 改为逐条并捕获 IntegrityError；新增 `url_exists()`、`get_recent_article_ids()` |
| `backend/analyzer/dedup.py` | 新增 `is_duplicate_cross_batch()` |
| `scripts/scheduler.py` | 调整去重流程 + 去重指标日志；写库后写指纹到 Redis |

## 验收标准

- [ ] 同一 URL 第二次爬取时被 `url_exists` 过滤掉，日志记录跳过数量和分类
- [ ] 同内容不同 URL 在第二次爬取时被 SimHash 跨批次命中并跳过
- [ ] 空 URL 新闻不会全部被 url_exists 过滤
- [ ] 跨批次比对延迟 < 100ms
- [ ] 24h 以前的 SimHash 指纹自动过期
- [ ] 日志中输出去重率，可据此判断去重健康度
