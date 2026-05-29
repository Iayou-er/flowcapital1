"""
Async 数据库门面（向后兼容）
根据 DATABASE_URL 自动适配 SQLite / PostgreSQL 后端
"""
import logging
import threading
from typing import List, Dict, Optional, Tuple

from sqlalchemy import text

from .engine import AsyncSessionLocal, DATABASE_URL
from .retry import retry_on_lock
from .cache import CacheManager
from .repositories.news import NewsRepository
from .repositories.analysis import AnalysisRepository
from .repositories.guest_book import GuestBookRepository
from .repositories.events import EventRepository

logger = logging.getLogger(__name__)

_IS_PG = DATABASE_URL.startswith("postgresql")


class AsyncDatabaseManager:
    """异步数据库门面（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.news = NewsRepository()
        self.analysis = AnalysisRepository()
        self.guest_book = GuestBookRepository()
        self.events = EventRepository()
        self.cache = CacheManager()
        logger.info("AsyncDatabaseManager 初始化（后端: %s）", "PostgreSQL" if _IS_PG else "SQLite")

    # ── init / close ──

    async def init_db(self):
        from .engine import engine
        from .models_orm import Base
        async with engine.begin() as conn:
            if not _IS_PG:
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.execute(text("PRAGMA synchronous=NORMAL"))
                await conn.execute(text("PRAGMA busy_timeout=5000"))   # 5s，配合 retry_on_lock 共 5 次
                await conn.execute(text("PRAGMA wal_autocheckpoint=1000"))  # 减少 checkpoint 频率
                await conn.execute(text("PRAGMA cache_size=-64000"))  # 64MB 页缓存
            await conn.run_sync(Base.metadata.create_all)
        logger.info("ORM 表初始化完成（WAL: %s）", "disabled" if _IS_PG else "enabled")

    async def close(self):
        from .engine import engine
        await engine.dispose()
        logger.info("Async 数据库连接已关闭")

    # ── news: write (代理) ──

    async def save_news_article(self, article: dict) -> bool:
        result = await self.news.save(article)
        if result:
            await self.cache.invalidate_news_list()
        return result

    async def save_news_articles(self, articles: List[dict]) -> int:
        result = await self.news.save_batch(articles)
        if result:
            await self.cache.invalidate_news_list()
        return result

    async def update_hotness_scores(self, scores: Dict[str, float]):
        await self.news.update_hotness_scores(scores)
        await self.cache.invalidate_news_list()

    async def reset_all_hotness_scores(self):
        await self.news.reset_hotness()
        await self.cache.invalidate_news_list()

    # ── news: read (代理) ──

    async def get_latest_news(
        self, limit: int = 100, offset: int = 0, exclude_sources: List[str] = None
    ) -> Tuple[List[Dict], int]:
        return await self.news.get_latest(limit, offset, exclude_sources)

    async def get_news_by_sources(
        self, sources: List[str], limit: int = 100, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        return await self.news.get_by_sources(sources, limit, offset)

    async def search_media(
        self, sources: List[str], keyword: str, limit: int = 20, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        return await self.news.search_media(sources, keyword, limit, offset)

    async def get_news_by_category(
        self, category: str, limit: int = 100, offset: int = 0,
        exclude_sources: List[str] = None,
    ) -> Tuple[List[Dict], int]:
        return await self.news.get_by_category(category, limit, offset, exclude_sources)

    async def get_news_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        return await self.news.get_by_date_range(start_date, end_date)

    async def search_news(
        self, keyword: str, limit: int = 20, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        return await self.news.search(keyword, limit, offset)

    async def get_news_by_article_id(self, article_id: str) -> Optional[Dict]:
        return await self.news.get_by_id(article_id)

    async def get_news_by_article_ids(self, article_ids: List[str]) -> List[Dict]:
        return await self.news.get_by_ids(article_ids)

    async def get_recent_article_ids(self, limit: int = 500) -> List[str]:
        return await self.news.get_recent_ids(limit)

    async def get_news_count(self) -> int:
        return await self.news.count()

    async def get_all_categories(self) -> List[Dict]:
        return await self.news.get_categories()

    # ── analysis (代理) ──

    async def save_analysis_result(self, analysis_result: dict) -> bool:
        return await self.analysis.save(analysis_result)

    async def get_analysis_by_article_id(self, article_id: str) -> Optional[Dict]:
        return await self.analysis.get_by_article_id(article_id)

    async def get_analysis_by_article_ids(self, article_ids: List[str]) -> Dict[str, Dict]:
        return await self.analysis.get_by_article_ids(article_ids)

    async def get_sentiment_aggregate(self) -> Tuple[float, int, int, int]:
        return await self.analysis.get_sentiment_aggregate()

    # ── guest_book (代理) ──

    async def save_guest_message(self, anonymous_id: str, content: str) -> bool:
        return await self.guest_book.save(anonymous_id, content)

    async def get_guest_messages(
        self, page: int = 1, limit: int = 20
    ) -> Tuple[List[Dict], int]:
        return await self.guest_book.get_messages(page, limit)

    async def get_anonymous_id_count(self) -> int:
        return await self.guest_book.count()

    # ── events (代理) ──

    async def insert_event(
        self, event_type: str, article_id: str = None,
        payload: str = None, client_id: str = None,
    ) -> bool:
        return await self.events.save(event_type, article_id, payload, client_id)

    async def get_recent_events(self, hours: int = 24) -> List[Dict]:
        return await self.events.get_recent(hours)

    # ── raw SQL access ──

    # busy_timeout=5s × 3 次重试 = 最坏 ~15s，匹配前端 30s timeout
    @retry_on_lock(max_retries=3, delay=0.1)
    async def execute_write(self, sql: str, params: dict = None):
        async with AsyncSessionLocal() as session:
            result = await session.execute(text(sql), params or {})
            await session.commit()
            return result

    @retry_on_lock(max_retries=3, delay=0.1)
    async def query_one(self, sql: str, params: dict = None):
        async with AsyncSessionLocal() as session:
            return (await session.execute(text(sql), params or {})).fetchone()

    @retry_on_lock(max_retries=3, delay=0.1)
    async def query_all(self, sql: str, params: dict = None):
        async with AsyncSessionLocal() as session:
            return (await session.execute(text(sql), params or {})).fetchall()


# 全局单例
db_async = AsyncDatabaseManager()
