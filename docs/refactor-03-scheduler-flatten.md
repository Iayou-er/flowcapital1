# scheduler.py 嵌套函数扁平化规范

## 现状

`scripts/scheduler.py` — 674 行，核心函数 `crawl_and_analyze_news`（169 行）内部定义了 2 个嵌套函数：

```python
async def crawl_and_analyze_news(db):
    # ... 60 行外层逻辑 ...

    async def _analyze_full(db, news_list):      # 105 行
        """全量分析：去重 → 情感 → 关键词 → 热度 → 索引"""
        ...

        async def _full_analyze_one(news):        # 116 行
            """单篇分析"""
            ...

    # ... 调用 _analyze_full ...
```

**隐患**：
- `_analyze_full` 和 `_full_analyze_one` 对模块外部不可见，无法单独导入测试
- 嵌套定义使 `crawl_and_analyze_news` 阅读时需要跟踪 3 层缩进
- 修改分析逻辑时容易误改外层调度逻辑

---

## 目标架构

```
scripts/
├── scheduler.py             # 调度入口（<200 行）
├── crawl_task.py            # 爬取任务编排
├── analysis_task.py         # 分析任务编排
├── hotness.py               # 热度计算
├── sync_task.py             # 搜索索引同步
└── metrics_push.py          # Prometheus Pushgateway 推送
```

---

## 拆分步骤

### 第 1 步：提取 `analysis_task.py`

将 `_analyze_full` 和 `_full_analyze_one` 提升为模块级函数：

```python
# scripts/analysis_task.py

async def run_full_analysis(db, news_list: List[Dict]) -> int:
    """
    对新闻列表执行全量分析管线：
    1. SimHash 去重（跨批次）
    2. SnowNLP 情感分析
    3. jieba 关键词提取
    4. 实体识别
    5. 热度评分
    6. 搜索结果写入

    Returns: 实际分析的文章数
    """
    analyzer = TextAnalyzer()
    sentiment_analyzer = SentimentAnalyzer()
    dedup_checker = SimHashDedup()

    analyzed_count = 0
    for news in news_list:
        try:
            result = await _analyze_single(news, analyzer, sentiment_analyzer, dedup_checker, db)
            if result:
                analyzed_count += 1
        except Exception as e:
            logger.warning(f"单篇分析失败: {news.get('title','')} {e}")
            continue

    await _update_hotness(db, news_list)
    await incremental_search_index_sync(db, news_list)
    return analyzed_count
```

> GraphRAG 增量更新由 `scheduler.py` 的 `_crawl_loop` 在爬取阶段触发（通过 `run_crawl(graph_rag_enabled=True)`），不在分析管线中重复执行。


async def _analyze_single(news: Dict, analyzer: TextAnalyzer,
                         sentiment: SentimentAnalyzer,
                         dedup: SimHashDedup, db) -> Optional[Dict]:
    """单篇文章分析（从 _full_analyze_one 提取）

    Returns: analysis_result dict 或 None（重复文章返回 None）
    """
    article_id = news.get('article_id', '')
    # 跨批次去重检查（SimHash 需要访问 db 获取近期文章指纹）
    if dedup and await dedup.is_duplicate_cross_batch(article_id, db):
        return None
    ...
```

### 第 2 步：提取 `crawl_task.py`

```python
# scripts/crawl_task.py

async def run_crawl(db, graph_rag_enabled: bool = False) -> Tuple[List[Dict], float]:
    """
    执行爬取任务：
    1. 初始化 NewsCrawler（或将来的 CrawlOrchestrator）
    2. 并行抓取 25 个数据源
    3. SimHash 去重
    4. 质量排序
    5. 存储到数据库
    6. 可选 GraphRAG 增量更新

    Returns: (articles, elapsed_seconds)
    """
    ...
```

### 第 3 步：提取 `hotness.py`

```python
# scripts/hotness.py

def compute_hotness(article: Dict, now: datetime = None) -> float:
    """热度 = 时间衰减 × (阅读量 + 评论量 × 2) / 时间衰减因子"""
    ...

async def update_hotness_scores(db, articles: List[Dict]) -> None:
    """批量更新热度分数"""
    ...
```

### 第 4 步：提取 `sync_task.py`

```python
# scripts/sync_task.py

async def incremental_search_index_sync(db, articles: List[Dict]) -> None:
    """增量同步 Whoosh 搜索索引"""
    ...

async def rebuild_search_index_full(db) -> int:
    """全量重建搜索索引"""
    ...
```

### 第 5 步：提取 `metrics_push.py`

```python
# scripts/metrics_push.py

def push_scheduler_metrics(articles_count: int, errors: int,
                           elapsed: float, pushgateway_url: str) -> None:
    """推送爬取指标到 Prometheus Pushgateway"""
    ...
```

### 第 6 步：精简 `scheduler.py`

> **前置依赖**：`crawl_task.py` 依赖 refactor-01（`CrawlOrchestrator`）。如果先执行本规范，保留旧 `NewsCrawler` 导入路径，待 refactor-01 完成后切换。

```python
# scripts/scheduler.py（最终形态 < 200 行）
import os
import sys
import time
import asyncio
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawl_task import run_crawl
from analysis_task import run_full_analysis
from hotness import reset_all_hotness
from sync_task import incremental_search_index_sync
from metrics_push import push_scheduler_metrics
from backend.database.db_manager_async import db_async
from backend.database.redis_client import redis_client
from backend.database.engine import _IS_PG

logger = logging.getLogger(__name__)

CRAWL_INTERVAL = int(os.getenv("CRAWL_INTERVAL", 900))  # 默认 15 分钟
PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")
_graph_rag_enabled = bool(os.getenv("LLM_API_KEY", "").strip())


async def _crawl_loop():
    """每 CRAWL_INTERVAL 秒执行一次爬取 + 分析"""
    while True:
        try:
            start = time.time()
            articles = await run_crawl(db_async, graph_rag_enabled=_graph_rag_enabled)
            if _graph_rag_enabled and articles:
                analyzed = await run_full_analysis(db_async, articles)
            else:
                analyzed = 0
            push_scheduler_metrics(len(articles), 0, time.time() - start, PUSHGATEWAY_URL)
        except Exception as e:
            logger.error(f"爬取循环异常: {e}", exc_info=True)
            push_scheduler_metrics(0, 1, 0, PUSHGATEWAY_URL)

        await asyncio.sleep(CRAWL_INTERVAL)


async def _daily_loop():
    """每日凌晨 2 点执行清理任务"""
    while True:
        now = time.localtime()
        seconds_until_2am = ((24 - now.tm_hour + 2) % 24) * 3600 - now.tm_min * 60 - now.tm_sec
        if seconds_until_2am <= 0:
            seconds_until_2am = 86400
        await asyncio.sleep(seconds_until_2am)

        try:
            logger.info("执行每日清理...")
            await reset_all_hotness(db_async)
            redis_client.flush_pattern("news:*")
            if not _IS_PG:
                await db_async.news.delete_old_articles(days=30)
            logger.info("每日清理完成")
        except Exception as e:
            logger.error(f"每日清理异常: {e}", exc_info=True)


async def main():
    await db_async.init_db()
    await asyncio.gather(_crawl_loop(), _daily_loop())

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 测试策略

| 层级 | 内容 | 工具 |
|------|------|------|
| 单元 | `compute_hotness` 时间衰减计算 | pytest |
| 单元 | `push_scheduler_metrics` 不抛异常 | pytest + mock |
| 集成 | `run_full_analysis` 用 mock db 验证管线顺序 | pytest |
| 端到端 | `run_crawl` 用 1-2 个稳定源 | 手动 |

---

## 验收标准

- [ ] `scheduler.py` < 200 行
- [ ] `crawl_and_analyze_news` 函数不再存在
- [ ] 每个任务模块可独立导入和测试
- [ ] 调度行为不变（15 分钟间隔、180s 超时、每日清理）
- [ ] GraphRAG 增量更新开关行为不变
- [ ] 每个模块有至少 1 个测试
