"""
Async 数据库管理器（SQLAlchemy 2.0）
根据 DATABASE_URL 自动适配 SQLite / PostgreSQL 后端
"""
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from sqlalchemy import text

from .engine import AsyncSessionLocal, DATABASE_URL

logger = logging.getLogger(__name__)

_IS_PG = DATABASE_URL.startswith("postgresql")

NEWS_LIGHT_COLS = [
    "id", "article_id", "title", "summary", "url", "source", "category",
    "published_at", "author", "read_count", "comment_count", "tags", "created_at", "updated_at",
]
NEWS_ALL_COLS = NEWS_LIGHT_COLS  # light cols already cover all except content; ALL_COLS adds content
NEWS_ALL_COLS = [
    "id", "article_id", "title", "content", "summary", "url", "source",
    "category", "published_at", "author", "read_count", "comment_count",
    "tags", "hotness_score", "created_at", "updated_at",
]
ANALYSIS_COLS = [
    "id", "article_id", "model_used", "sentiment_score",
    "sentiment_label", "keywords", "summary", "analysis_type", "result", "created_at",
]
EVENT_COLS = ["id", "event_type", "article_id", "client_id", "payload", "created_at"]
GUEST_COLS = ["id", "anonymous_id", "content", "created_at"]


def _row_to_dict(row, cols: List[str]) -> dict:
    d = dict(zip(cols, row))
    if d.get("tags") and isinstance(d["tags"], str):
        d["tags"] = d["tags"].split(",")
    if d.get("keywords") and isinstance(d["keywords"], str):
        d["keywords"] = d["keywords"].split(",")
    return d


class AsyncDatabaseManager:
    """异步数据库管理器（单例）"""

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
        logger.info(f"AsyncDatabaseManager 初始化（后端: {'PostgreSQL' if _IS_PG else 'SQLite'}）")

    # ── SQL 兼容助手 ──

    @staticmethod
    def _build_in_clause(param_name: str, values: list, negate: bool = False) -> Tuple[str, dict]:
        """IN 子句兼容：PG 用 = ANY(:p)，SQLite 用 IN (:p_0, :p_1, ...)"""
        if not values:
            return ("FALSE", {})
        if _IS_PG:
            op = "!= ALL" if negate else "= ANY"
            return (f"{op}(:{param_name})", {param_name: values})
        params = {f"{param_name}_{i}": v for i, v in enumerate(values)}
        placeholders = ", ".join(f":{k}" for k in params)
        prefix = "NOT " if negate else ""
        return (f"{prefix}IN ({placeholders})", params)

    # ── 缓存失效 ──

    async def _invalidate_news_cache(self):
        try:
            from .redis_client import redis_client
            for page in range(1, 6):
                for limit in (12, 15, 20, 24, 50):
                    redis_client.delete(f"news:latest:{page}:{limit}")
                    redis_client.delete(f"news:media:{page}:{limit}")
        except Exception:
            pass

    # ── init ──

    async def init_db(self):
        from .engine import engine
        from .models_orm import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("ORM 表初始化完成")

    # ── news: write ──

    async def save_news_article(self, article: dict) -> bool:
        try:
            async with AsyncSessionLocal() as session:
                existing = (
                    await session.execute(
                        text("SELECT content, title, summary FROM news_articles WHERE article_id = :aid"),
                        {"aid": article["article_id"]},
                    )
                ).fetchone()

                if existing and (
                    existing[0] == article.get("content", "")
                    and existing[1] == article.get("title", "")
                    and existing[2] == article.get("summary", "")
                ):
                    logger.debug(f"新闻已存在且内容一致，跳过: {article.get('title', '')}")
                    return True

                tags = article.get("tags", [])
                tags_str = ",".join(tags) if isinstance(tags, list) else (tags or "")

                params = {
                    "aid": article["article_id"],
                    "title": article.get("title", ""),
                    "content": article.get("content", ""),
                    "summary": article.get("summary", ""),
                    "url": article.get("url", ""),
                    "source": article.get("source", ""),
                    "category": article.get("category", ""),
                    "published_at": article.get("published_at", ""),
                    "author": article.get("author", ""),
                    "read_count": article.get("read_count", 0),
                    "comment_count": article.get("comment_count", 0),
                    "tags": tags_str,
                    "created_at": article.get("created_at", datetime.now().isoformat()),
                    "updated_at": datetime.now().isoformat(),
                }

                await session.execute(
                    text(
                        "INSERT INTO news_articles (article_id, title, content, summary, url, source, "
                        "category, published_at, author, read_count, comment_count, tags, created_at, updated_at) "
                        "VALUES (:aid, :title, :content, :summary, :url, :source, :category, "
                        ":published_at, :author, :read_count, :comment_count, :tags, :created_at, :updated_at) "
                        "ON CONFLICT (article_id) DO UPDATE SET "
                        "title = EXCLUDED.title, content = EXCLUDED.content, summary = EXCLUDED.summary, "
                        "url = EXCLUDED.url, source = EXCLUDED.source, category = EXCLUDED.category, "
                        "published_at = EXCLUDED.published_at, author = EXCLUDED.author, "
                        "read_count = EXCLUDED.read_count, comment_count = EXCLUDED.comment_count, "
                        "tags = EXCLUDED.tags, created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at"
                    ),
                    params,
                )

                await session.commit()
                await self._invalidate_news_cache()
                logger.info(f"{'更新' if existing else '保存'}新闻: {article.get('title', '')}")
                return True
        except Exception as e:
            logger.error(f"保存新闻失败: {e}")
            return False

    async def save_news_articles(self, articles: List[dict]) -> int:
        if not articles:
            return 0
        try:
            async with AsyncSessionLocal() as session:
                article_ids = [a["article_id"] for a in articles]
                in_sql, in_params = self._build_in_clause("ids", article_ids)

                existing_rows = (
                    await session.execute(
                        text(
                            f"SELECT article_id, content, title, summary FROM news_articles "
                            f"WHERE article_id {in_sql}"
                        ),
                        in_params,
                    )
                ).fetchall()
                existing_map = {r[0]: r for r in existing_rows}

                # 分离需写入的文章
                to_insert = []
                skip_count = 0
                for article in articles:
                    existing = existing_map.get(article["article_id"])
                    if (
                        existing
                        and existing[1] == article.get("content", "")
                        and existing[2] == article.get("title", "")
                        and existing[3] == article.get("summary", "")
                    ):
                        skip_count += 1
                        continue
                    to_insert.append(article)

                if not to_insert:
                    logger.debug(f"批量保存: 全部 {skip_count} 篇已存在且内容一致，跳过")
                    return skip_count

                # 批量 INSERT：单条 SQL，多 VALUES 行
                now = datetime.now().isoformat()
                col_names = (
                    "article_id, title, content, summary, url, source, category, "
                    "published_at, author, read_count, comment_count, tags, created_at, updated_at"
                )
                values_parts = []
                all_params = {}
                for i, article in enumerate(to_insert):
                    p = f"a{i}_"
                    tags = article.get("tags", [])
                    tags_str = ",".join(tags) if isinstance(tags, list) else (tags or "")
                    values_parts.append(
                        f"(:{p}aid, :{p}title, :{p}content, :{p}summary, :{p}url, :{p}source, "
                        f":{p}category, :{p}published_at, :{p}author, :{p}read_count, "
                        f":{p}comment_count, :{p}tags, :{p}created_at, :{p}updated_at)"
                    )
                    all_params.update({
                        f"{p}aid": article["article_id"],
                        f"{p}title": article.get("title", ""),
                        f"{p}content": article.get("content", ""),
                        f"{p}summary": article.get("summary", ""),
                        f"{p}url": article.get("url", ""),
                        f"{p}source": article.get("source", ""),
                        f"{p}category": article.get("category", ""),
                        f"{p}published_at": article.get("published_at", ""),
                        f"{p}author": article.get("author", ""),
                        f"{p}read_count": article.get("read_count", 0),
                        f"{p}comment_count": article.get("comment_count", 0),
                        f"{p}tags": tags_str,
                        f"{p}created_at": article.get("created_at", now),
                        f"{p}updated_at": now,
                    })

                values_sql = ", ".join(values_parts)

                sql = (
                    f"INSERT INTO news_articles ({col_names}) VALUES {values_sql} "
                    "ON CONFLICT (article_id) DO UPDATE SET "
                    "title = EXCLUDED.title, content = EXCLUDED.content, summary = EXCLUDED.summary, "
                    "url = EXCLUDED.url, source = EXCLUDED.source, category = EXCLUDED.category, "
                    "published_at = EXCLUDED.published_at, author = EXCLUDED.author, "
                    "read_count = EXCLUDED.read_count, comment_count = EXCLUDED.comment_count, "
                    "tags = EXCLUDED.tags, created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at"
                )

                await session.execute(text(sql), all_params)
                await session.commit()
                await self._invalidate_news_cache()

                success = len(to_insert)
                logger.info(f"批量保存完成: 写入={success}, 跳过={skip_count}/{len(articles)}")
                return success
        except Exception as e:
            logger.error(f"批量保存失败: {e}")
            return 0

    async def update_hotness_scores(self, scores: Dict[str, float]):
        if not scores:
            return
        try:
            async with AsyncSessionLocal() as session:
                for aid, score in scores.items():
                    await session.execute(
                        text("UPDATE news_articles SET hotness_score = :score WHERE article_id = :aid"),
                        {"score": round(score, 4), "aid": aid},
                    )
                await session.commit()
                await self._invalidate_news_cache()
        except Exception as e:
            logger.error(f"更新热度分失败: {e}")

    async def reset_all_hotness_scores(self):
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("UPDATE news_articles SET hotness_score = 0 WHERE hotness_score != 0")
                )
                await session.commit()
                await self._invalidate_news_cache()
        except Exception as e:
            logger.error(f"重置热度分失败: {e}")

    # ── news: read ──

    async def get_latest_news(
        self, limit: int = 100, offset: int = 0, exclude_sources: List[str] = None
    ) -> Tuple[List[Dict], int]:
        try:
            async with AsyncSessionLocal() as session:
                now = datetime.now()
                day_ago = (now - timedelta(days=1)).isoformat()
                week_ago = (now - timedelta(days=7)).isoformat()

                tag_cnt = (
                    "COALESCE(LENGTH(tags) - LENGTH(REPLACE(tags, ',', '')), 0) "
                    "+ CASE WHEN tags != '' THEN 1 ELSE 0 END"
                )
                tag_cap = f"LEAST({tag_cnt}, 10)" if _IS_PG else f"MIN({tag_cnt}, 10)"
                order_sql = (
                    "ORDER BY "
                    "CASE WHEN published_at >= :day_ago THEN 1.0 "
                    "WHEN published_at >= :week_ago THEN 0.5 ELSE 0.1 END * 0.5 "
                    f"+ COALESCE(hotness_score, 0) * 0.3 "
                    f"+ {tag_cap} * 0.02 DESC"
                )

                base_params = {"day_ago": day_ago, "week_ago": week_ago, "limit": limit, "offset": offset}
                cols = ", ".join(NEWS_LIGHT_COLS)

                if exclude_sources:
                    in_sql, in_params = self._build_in_clause("exclude", exclude_sources, negate=True)
                    base_params.update(in_params)
                    total = (
                        await session.execute(
                            text(f"SELECT COUNT(*) FROM news_articles WHERE source {in_sql}"),
                            in_params,
                        )
                    ).scalar()
                    rows = (
                        await session.execute(
                            text(
                                f"SELECT {cols} FROM news_articles "
                                f"WHERE source {in_sql} {order_sql} LIMIT :limit OFFSET :offset"
                            ),
                            base_params,
                        )
                    ).fetchall()
                else:
                    total = (
                        await session.execute(text("SELECT COUNT(*) FROM news_articles"))
                    ).scalar()
                    rows = (
                        await session.execute(
                            text(
                                f"SELECT {cols} FROM news_articles "
                                f"{order_sql} LIMIT :limit OFFSET :offset"
                            ),
                            base_params,
                        )
                    ).fetchall()

                return [_row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error(f"获取最新新闻失败: {e}")
            return [], 0

    async def get_news_by_sources(
        self, sources: List[str], limit: int = 100, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        if not sources:
            return [], 0
        try:
            async with AsyncSessionLocal() as session:
                in_sql, in_params = self._build_in_clause("srcs", sources)
                total = (
                    await session.execute(
                        text(f"SELECT COUNT(*) FROM news_articles WHERE source {in_sql}"),
                        in_params,
                    )
                ).scalar()
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(NEWS_LIGHT_COLS)} FROM news_articles "
                            f"WHERE source {in_sql} "
                            f"ORDER BY published_at DESC LIMIT :limit OFFSET :offset"
                        ),
                        {**in_params, "limit": limit, "offset": offset},
                    )
                ).fetchall()
                return [_row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error(f"获取自媒体新闻失败: {e}")
            return [], 0

    async def search_media(
        self, sources: List[str], keyword: str, limit: int = 20, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        if not sources or not keyword:
            return [], 0
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        try:
            async with AsyncSessionLocal() as session:
                in_sql, in_params = self._build_in_clause("srcs", sources)
                params = {**in_params, "pat": pattern, "limit": limit, "offset": offset}
                total = (
                    await session.execute(
                        text(
                            f"SELECT COUNT(*) FROM news_articles "
                            f"WHERE source {in_sql} "
                            f"AND (title LIKE :pat ESCAPE '\\' OR content LIKE :pat ESCAPE '\\' "
                            f"OR summary LIKE :pat ESCAPE '\\')"
                        ),
                        {**in_params, "pat": pattern},
                    )
                ).scalar()
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(NEWS_LIGHT_COLS)} FROM news_articles "
                            f"WHERE source {in_sql} "
                            f"AND (title LIKE :pat ESCAPE '\\' OR content LIKE :pat ESCAPE '\\' "
                            f"OR summary LIKE :pat ESCAPE '\\') "
                            f"ORDER BY published_at DESC LIMIT :limit OFFSET :offset"
                        ),
                        params,
                    )
                ).fetchall()
                return [_row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error(f"搜索自媒体失败: {e}")
            return [], 0

    async def get_news_by_category(
        self, category: str, limit: int = 100, offset: int = 0,
        exclude_sources: List[str] = None,
    ) -> Tuple[List[Dict], int]:
        try:
            async with AsyncSessionLocal() as session:
                cols = ", ".join(NEWS_LIGHT_COLS)
                if exclude_sources:
                    in_sql, in_params = self._build_in_clause("exclude", exclude_sources, negate=True)
                    total = (
                        await session.execute(
                            text(
                                f"SELECT COUNT(*) FROM news_articles "
                                f"WHERE category = :cat AND source {in_sql}"
                            ),
                            {"cat": category, **in_params},
                        )
                    ).scalar()
                    rows = (
                        await session.execute(
                            text(
                                f"SELECT {cols} FROM news_articles "
                                f"WHERE category = :cat AND source {in_sql} "
                                f"ORDER BY published_at DESC LIMIT :limit OFFSET :offset"
                            ),
                            {"cat": category, **in_params, "limit": limit, "offset": offset},
                        )
                    ).fetchall()
                else:
                    total = (
                        await session.execute(
                            text("SELECT COUNT(*) FROM news_articles WHERE category = :cat"),
                            {"cat": category},
                        )
                    ).scalar()
                    rows = (
                        await session.execute(
                            text(
                                f"SELECT {cols} FROM news_articles WHERE category = :cat "
                                f"ORDER BY published_at DESC LIMIT :limit OFFSET :offset"
                            ),
                            {"cat": category, "limit": limit, "offset": offset},
                        )
                    ).fetchall()
                return [_row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error(f"获取分类新闻失败: {e}")
            return [], 0

    async def get_news_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        try:
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(NEWS_LIGHT_COLS)} FROM news_articles "
                            f"WHERE published_at BETWEEN :start AND :end "
                            f"ORDER BY published_at DESC"
                        ),
                        {"start": start_date, "end": end_date},
                    )
                ).fetchall()
                return [_row_to_dict(r, NEWS_LIGHT_COLS) for r in rows]
        except Exception as e:
            logger.error(f"按日期范围获取新闻失败: {e}")
            return []

    async def search_news(
        self, keyword: str, limit: int = 20, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        try:
            async with AsyncSessionLocal() as session:
                cols = ", ".join(NEWS_LIGHT_COLS)
                total = (
                    await session.execute(
                        text(
                            "SELECT COUNT(*) FROM news_articles "
                            "WHERE title LIKE :pat ESCAPE '\\' OR content LIKE :pat ESCAPE '\\' "
                            "OR summary LIKE :pat ESCAPE '\\'"
                        ),
                        {"pat": pattern},
                    )
                ).scalar()
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {cols} FROM news_articles "
                            f"WHERE title LIKE :pat ESCAPE '\\' OR content LIKE :pat ESCAPE '\\' "
                            f"OR summary LIKE :pat ESCAPE '\\' "
                            f"ORDER BY published_at DESC LIMIT :limit OFFSET :offset"
                        ),
                        {"pat": pattern, "limit": limit, "offset": offset},
                    )
                ).fetchall()
                return [_row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error(f"搜索新闻失败: {e}")
            return [], 0

    async def get_news_by_article_id(self, article_id: str) -> Optional[Dict]:
        try:
            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        text(f"SELECT {', '.join(NEWS_ALL_COLS)} FROM news_articles WHERE article_id = :aid"),
                        {"aid": article_id},
                    )
                ).fetchone()
                if row is None:
                    return None
                return _row_to_dict(row, NEWS_ALL_COLS)
        except Exception as e:
            logger.error(f"获取新闻失败: {e}")
            return None

    async def get_news_by_article_ids(self, article_ids: List[str]) -> List[Dict]:
        if not article_ids:
            return []
        try:
            async with AsyncSessionLocal() as session:
                in_sql, in_params = self._build_in_clause("ids", article_ids)
                rows = (
                    await session.execute(
                        text(f"SELECT {', '.join(NEWS_ALL_COLS)} FROM news_articles WHERE article_id {in_sql}"),
                        in_params,
                    )
                ).fetchall()
                news_map = {}
                for row in rows:
                    d = _row_to_dict(row, NEWS_ALL_COLS)
                    news_map[d["article_id"]] = d
                return [news_map[aid] for aid in article_ids if aid in news_map]
        except Exception as e:
            logger.error(f"批量获取新闻失败: {e}")
            return []

    async def get_recent_article_ids(self, limit: int = 500) -> List[str]:
        try:
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        text("SELECT article_id FROM news_articles ORDER BY id DESC LIMIT :limit"),
                        {"limit": limit},
                    )
                ).fetchall()
                return [r[0] for r in rows]
        except Exception as e:
            logger.error(f"获取最近 article_id 失败: {e}")
            return []

    async def get_news_count(self) -> int:
        try:
            async with AsyncSessionLocal() as session:
                return (await session.execute(text("SELECT COUNT(*) FROM news_articles"))).scalar() or 0
        except Exception as e:
            logger.error(f"获取新闻总数失败: {e}")
            return 0

    async def get_all_categories(self) -> List[Dict]:
        try:
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT category, COUNT(*) as count FROM news_articles "
                            "WHERE category IS NOT NULL AND category != '' "
                            "GROUP BY category ORDER BY count DESC"
                        )
                    )
                ).fetchall()
                return [{"category": r[0], "count": r[1]} for r in rows]
        except Exception as e:
            logger.error(f"获取分类列表失败: {e}")
            return []

    # ── analysis ──

    async def save_analysis_result(self, analysis_result: dict) -> bool:
        try:
            async with AsyncSessionLocal() as session:
                keywords = analysis_result.get("keywords", [])
                keywords_str = (
                    ",".join(keywords) if isinstance(keywords, list) else (keywords or "")
                )
                params = {
                    "aid": analysis_result["article_id"],
                    "model": analysis_result.get("model_used", ""),
                    "score": analysis_result.get("sentiment_score", 0.0),
                    "label": analysis_result.get("sentiment_label", ""),
                    "keywords": keywords_str,
                    "summary": analysis_result.get("summary", ""),
                    "atype": analysis_result.get("analysis_type", "sentiment"),
                    "result": analysis_result.get("result", ""),
                    "created_at": datetime.now().isoformat(),
                }
                await session.execute(
                    text(
                        "INSERT INTO analysis_results (article_id, model_used, sentiment_score, "
                        "sentiment_label, keywords, summary, analysis_type, result, created_at) "
                        "VALUES (:aid, :model, :score, :label, :keywords, :summary, :atype, :result, :created_at) "
                        "ON CONFLICT (article_id) DO UPDATE SET "
                        "model_used = EXCLUDED.model_used, sentiment_score = EXCLUDED.sentiment_score, "
                        "sentiment_label = EXCLUDED.sentiment_label, keywords = EXCLUDED.keywords, "
                        "summary = EXCLUDED.summary, analysis_type = EXCLUDED.analysis_type, "
                        "result = EXCLUDED.result, created_at = EXCLUDED.created_at"
                    ),
                    params,
                )
                await session.commit()
                logger.debug(f"保存分析结果: {analysis_result['article_id']}")
                return True
        except Exception as e:
            logger.error(f"保存分析结果失败: {e}")
            return False

    async def get_analysis_by_article_id(self, article_id: str) -> Optional[Dict]:
        try:
            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        text(f"SELECT {', '.join(ANALYSIS_COLS)} FROM analysis_results WHERE article_id = :aid LIMIT 1"),
                        {"aid": article_id},
                    )
                ).fetchone()
                if row is None:
                    return None
                return _row_to_dict(row, ANALYSIS_COLS)
        except Exception as e:
            logger.error(f"获取分析结果失败: {e}")
            return None

    async def get_sentiment_aggregate(self) -> Tuple[float, int, int, int]:
        try:
            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT "
                            "AVG(sentiment_score), "
                            "SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN sentiment_label = 'neutral' THEN 1 ELSE 0 END) "
                            "FROM analysis_results"
                        )
                    )
                ).fetchone()
                if row and row[0] is not None:
                    return round(row[0], 4), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
                return 0.0, 0, 0, 0
        except Exception:
            return 0.0, 0, 0, 0

    # ── guest_book ──

    async def save_guest_message(self, anonymous_id: str, content: str) -> bool:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(
                        "INSERT INTO guest_book (anonymous_id, content, created_at) "
                        "VALUES (:aid, :content, :created_at)"
                    ),
                    {"aid": anonymous_id, "content": content, "created_at": datetime.now().isoformat()},
                )
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"保存留言失败: {e}")
            return False

    async def get_guest_messages(
        self, page: int = 1, limit: int = 20
    ) -> Tuple[List[Dict], int]:
        try:
            async with AsyncSessionLocal() as session:
                total = (
                    await session.execute(text("SELECT COUNT(*) FROM guest_book"))
                ).scalar() or 0
                offset = (page - 1) * limit
                rows = (
                    await session.execute(
                        text(f"SELECT {', '.join(GUEST_COLS)} FROM guest_book ORDER BY created_at DESC LIMIT :limit OFFSET :offset"),
                        {"limit": limit, "offset": offset},
                    )
                ).fetchall()
                return [dict(zip(GUEST_COLS, r)) for r in rows], total
        except Exception as e:
            logger.error(f"获取留言失败: {e}")
            return [], 0

    async def get_anonymous_id_count(self) -> int:
        try:
            async with AsyncSessionLocal() as session:
                return (
                    await session.execute(text("SELECT COUNT(*) FROM guest_book"))
                ).scalar() or 0
        except Exception:
            return 0

    # ── events ──

    async def insert_event(
        self, event_type: str, article_id: str = None,
        payload: str = None, client_id: str = None,
    ) -> bool:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(
                        "INSERT INTO event_log (event_type, article_id, payload, client_id, created_at) "
                        "VALUES (:etype, :aid, :payload, :cid, :created_at)"
                    ),
                    {
                        "etype": event_type,
                        "aid": article_id,
                        "payload": payload,
                        "cid": client_id,
                        "created_at": datetime.now().isoformat(),
                    },
                )
                await session.commit()
                return True
        except Exception as e:
            logger.warning(f"写入事件失败: {e}")
            return False

    async def get_recent_events(self, hours: int = 24) -> List[Dict]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        try:
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(EVENT_COLS)} FROM event_log WHERE created_at >= :since "
                            "ORDER BY created_at DESC"
                        ),
                        {"since": since},
                    )
                ).fetchall()
                return [dict(zip(EVENT_COLS, r)) for r in rows]
        except Exception as e:
            logger.error(f"获取事件失败: {e}")
            return []

    # ── raw SQL access ──

    async def execute_write(self, sql: str, params: dict = None):
        async with AsyncSessionLocal() as session:
            result = await session.execute(text(sql), params or {})
            await session.commit()
            return result

    async def query_one(self, sql: str, params: dict = None):
        async with AsyncSessionLocal() as session:
            return (await session.execute(text(sql), params or {})).fetchone()

    async def query_all(self, sql: str, params: dict = None):
        async with AsyncSessionLocal() as session:
            return (await session.execute(text(sql), params or {})).fetchall()

    async def close(self):
        from .engine import engine
        await engine.dispose()
        logger.info("Async 数据库连接已关闭")


# 全局单例
db_async = AsyncDatabaseManager()
