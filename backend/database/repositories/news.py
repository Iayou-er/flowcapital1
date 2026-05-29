"""NewsRepository — news_articles 表 CRUD"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

from sqlalchemy import text

from .base import AsyncRepository
from ..sql_helpers import NEWS_LIGHT_COLS, NEWS_ALL_COLS, build_in_clause, row_to_dict, _IS_PG
from ..engine import AsyncSessionLocal
from ..retry import retry_on_lock

logger = logging.getLogger(__name__)


class NewsRepository(AsyncRepository):
    table_name = "news_articles"
    columns = NEWS_ALL_COLS

    @retry_on_lock(max_retries=3, delay=0.1)
    async def save(self, article: dict) -> bool:
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
                    logger.debug("新闻已存在且内容一致，跳过: %s", article.get("title", ""))
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
                logger.info("%s 新闻: %s", "更新" if existing else "保存", article.get("title", ""))
                return True
        except Exception as e:
            logger.error("保存新闻失败: %s", e)
            return False

    @retry_on_lock(max_retries=3, delay=0.1)
    async def save_batch(self, articles: List[dict]) -> int:
        if not articles:
            return 0
        try:
            # 1. 去重检查（单次查询）
            async with AsyncSessionLocal() as session:
                article_ids = [a["article_id"] for a in articles]
                in_sql, in_params = build_in_clause("ids", article_ids)
                existing_rows = (
                    await session.execute(
                        text(f"SELECT article_id, content, title, summary FROM news_articles WHERE article_id {in_sql}"),
                        in_params,
                    )
                ).fetchall()
                existing_map = {r[0]: r for r in existing_rows}

            to_insert = []
            skip_count = 0
            for article in articles:
                existing = existing_map.get(article["article_id"])
                if (existing and existing[1] == article.get("content", "")
                        and existing[2] == article.get("title", "")
                        and existing[3] == article.get("summary", "")):
                    skip_count += 1
                    continue
                to_insert.append(article)

            if not to_insert:
                logger.debug("批量保存: 全部 %d 篇已存在且内容一致，跳过", skip_count)
                return skip_count

            # 2. 分块写入（每块独立 session，避免单条失败回滚全部 + 防超 SQLite 变量上限）
            CHUNK_SIZE = 50
            now = datetime.now().isoformat()
            col_names = (
                "article_id, title, content, summary, url, source, category, "
                "published_at, author, read_count, comment_count, tags, created_at, updated_at"
            )
            total_saved = 0

            for chunk_start in range(0, len(to_insert), CHUNK_SIZE):
                chunk = to_insert[chunk_start:chunk_start + CHUNK_SIZE]
                values_parts = []
                all_params = {}
                for i, article in enumerate(chunk):
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
                try:
                    async with AsyncSessionLocal() as session:
                        await session.execute(text(sql), all_params)
                        await session.commit()
                        total_saved += len(chunk)
                except Exception as e:
                    logger.warning("批次 %d-%d 保存失败: %s", chunk_start, chunk_start + len(chunk), e)

            logger.info("批量保存完成: 写入=%d, 跳过=%d/%d", total_saved, skip_count, len(articles))
            return total_saved
        except Exception as e:
            logger.error("批量保存失败: %s", e)
            return 0

    @retry_on_lock(max_retries=3, delay=0.1)
    async def update_hotness_scores(self, scores: Dict[str, float]):
        if not scores:
            return
        try:
            # CASE 批量更新，避免 N+1
            cases = []
            params = {}
            for i, (aid, score) in enumerate(scores.items()):
                cases.append(f"WHEN :aid{i} THEN :score{i}")
                params[f"aid{i}"] = aid
                params[f"score{i}"] = round(score, 4)
            aids_list = ", ".join(f":aid{i}" for i in range(len(scores)))
            sql = (
                f"UPDATE news_articles SET hotness_score = CASE article_id "
                f"{' '.join(cases)} END WHERE article_id IN ({aids_list})"
            )
            async with AsyncSessionLocal() as session:
                await session.execute(text(sql), params)
                await session.commit()
        except Exception as e:
            logger.error("更新热度分失败: %s", e)

    async def reset_hotness(self):
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("UPDATE news_articles SET hotness_score = 0 WHERE hotness_score != 0")
                )
                await session.commit()
        except Exception as e:
            logger.error("重置热度分失败: %s", e)

    # ── read methods ──

    async def get_latest(
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
                    in_sql, in_params = build_in_clause("exclude", exclude_sources, negate=True)
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

                return [row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error("获取最新新闻失败: %s", e)
            return [], 0

    async def get_by_sources(
        self, sources: List[str], limit: int = 100, offset: int = 0
    ) -> Tuple[List[Dict], int]:
        if not sources:
            return [], 0
        try:
            async with AsyncSessionLocal() as session:
                in_sql, in_params = build_in_clause("srcs", sources)
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
                return [row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error("获取自媒体新闻失败: %s", e)
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
                in_sql, in_params = build_in_clause("srcs", sources)
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
                return [row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error("搜索自媒体失败: %s", e)
            return [], 0

    async def get_by_category(
        self, category: str, limit: int = 100, offset: int = 0,
        exclude_sources: List[str] = None,
    ) -> Tuple[List[Dict], int]:
        try:
            async with AsyncSessionLocal() as session:
                cols = ", ".join(NEWS_LIGHT_COLS)
                if exclude_sources:
                    in_sql, in_params = build_in_clause("exclude", exclude_sources, negate=True)
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
                return [row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error("获取分类新闻失败: %s", e)
            return [], 0

    async def get_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
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
                return [row_to_dict(r, NEWS_LIGHT_COLS) for r in rows]
        except Exception as e:
            logger.error("按日期范围获取新闻失败: %s", e)
            return []

    async def search(
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
                return [row_to_dict(r, NEWS_LIGHT_COLS) for r in rows], total or 0
        except Exception as e:
            logger.error("搜索新闻失败: %s", e)
            return [], 0

    async def get_by_id(self, article_id: str) -> Optional[Dict]:
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
                return row_to_dict(row, NEWS_ALL_COLS)
        except Exception as e:
            logger.error("获取新闻失败: %s", e)
            return None

    async def get_by_ids(self, article_ids: List[str]) -> List[Dict]:
        if not article_ids:
            return []
        try:
            async with AsyncSessionLocal() as session:
                in_sql, in_params = build_in_clause("ids", article_ids)
                rows = (
                    await session.execute(
                        text(f"SELECT {', '.join(NEWS_ALL_COLS)} FROM news_articles WHERE article_id {in_sql}"),
                        in_params,
                    )
                ).fetchall()
                news_map = {}
                for row in rows:
                    d = row_to_dict(row, NEWS_ALL_COLS)
                    news_map[d["article_id"]] = d
                return [news_map[aid] for aid in article_ids if aid in news_map]
        except Exception as e:
            logger.error("批量获取新闻失败: %s", e)
            return []

    async def get_recent_ids(self, limit: int = 500) -> List[str]:
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
            logger.error("获取最近 article_id 失败: %s", e)
            return []

    async def count(self) -> int:
        try:
            async with AsyncSessionLocal() as session:
                return (await session.execute(text("SELECT COUNT(*) FROM news_articles"))).scalar() or 0
        except Exception as e:
            logger.error("获取新闻总数失败: %s", e)
            return 0

    async def get_categories(self) -> List[Dict]:
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
            logger.error("获取分类列表失败: %s", e)
            return []
