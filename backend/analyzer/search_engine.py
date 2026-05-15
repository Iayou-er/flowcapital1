"""
全文搜索引擎
基于 Whoosh 实现中文新闻的多关键词组合搜索
"""

import os
import asyncio
import logging
from typing import List, Dict, Optional, Tuple
from whoosh.index import create_in, open_dir, Index
from whoosh.fields import Schema, TEXT, ID, DATETIME
from whoosh.qparser import QueryParser, MultifieldParser, GroupPlugin, FuzzyTermPlugin
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import jieba
import threading

logger = logging.getLogger(__name__)

# 索引路径
INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'whoosh_index')

# 专用线程池执行 Whoosh I/O，避免与 FastAPI 共享默认线程池
_whoosh_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="whoosh")


class ChineseAnalyzer:
    """基于 jieba 的 Whoosh 中文分词分析器"""

    def __call__(self, value, positions=False, chars=False, keeporiginal=False,
                 removestops=True, start_pos=0, start_chars=0, **kwargs):
        from whoosh.analysis import Token
        pos = start_pos
        for word in jieba.lcut(value):
            word = word.strip()
            if len(word) < 2:
                continue
            token = Token()
            token.text = word
            if positions:
                token.pos = pos
            pos += 1
            yield token

    def __repr__(self):
        return "ChineseAnalyzer()"


def _parse_date(val) -> Optional[datetime]:
    """将 ISO 日期字符串转为 datetime 对象"""
    if val is None or val == '':
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        return None


# 懒初始化
_schema = Schema(
    article_id=ID(stored=True, unique=True),
    title=TEXT(stored=True, analyzer=ChineseAnalyzer(), field_boost=2.0),
    content=TEXT(stored=False, analyzer=ChineseAnalyzer()),
    summary=TEXT(stored=False, analyzer=ChineseAnalyzer()),
    source=TEXT(stored=True, analyzer=ChineseAnalyzer()),
    category=TEXT(stored=True, analyzer=ChineseAnalyzer()),
    published_at=DATETIME(stored=True),
)

_index: Optional[Index] = None
_lock = threading.Lock()


def _get_index() -> Index:
    global _index
    if _index is not None:
        return _index
    with _lock:
        if _index is not None:
            return _index
        os.makedirs(INDEX_DIR, exist_ok=True)
        toc_files = [f for f in os.listdir(INDEX_DIR) if f.startswith('_MAIN_') and f.endswith('.toc')]
        if toc_files:
            _index = open_dir(INDEX_DIR)
        else:
            _index = create_in(INDEX_DIR, _schema)
            logger.info("Whoosh 索引已创建")
        return _index


def rebuild_index(news_list: List[Dict]) -> int:
    """
    重建索引：用于首次初始化或批量导入

    Args:
        news_list: 新闻列表（字典，需包含 article_id/title/content/source/category/published_at）

    Returns:
        索引的新闻数量
    """
    idx = _get_index()
    writer = idx.writer()
    count = 0
    for news in news_list:
        try:
            writer.add_document(
                article_id=news.get('article_id', ''),
                title=news.get('title', ''),
                content=news.get('content', ''),
                summary=news.get('summary', ''),
                source=news.get('source', ''),
                category=news.get('category', ''),
                published_at=_parse_date(news.get('published_at')),
            )
            count += 1
        except Exception as e:
            logger.warning(f"索引新闻失败: {e}")
    writer.commit()
    logger.info(f"Whoosh 重建索引完成: {count} 条")
    return count


def add_document(news: Dict):
    """添加单条新闻到索引"""
    idx = _get_index()
    writer = idx.writer()
    try:
        writer.update_document(
            article_id=news.get('article_id', ''),
            title=news.get('title', ''),
            content=news.get('content', ''),
            summary=news.get('summary', ''),
            source=news.get('source', ''),
            category=news.get('category', ''),
            published_at=_parse_date(news.get('published_at')),
        )
    except Exception as e:
        logger.warning(f"索引更新失败: {e}")
    finally:
        writer.commit()


def add_documents_batch(news_list: List[Dict]) -> int:
    """批量添加新闻到索引（单个 writer，单次 commit）"""
    if not news_list:
        return 0
    idx = _get_index()
    writer = idx.writer()
    count = 0
    for news in news_list:
        try:
            writer.update_document(
                article_id=news.get('article_id', ''),
                title=news.get('title', ''),
                content=news.get('content', ''),
                summary=news.get('summary', ''),
                source=news.get('source', ''),
                category=news.get('category', ''),
                published_at=_parse_date(news.get('published_at')),
            )
            count += 1
        except Exception as e:
            logger.warning(f"索引更新失败: {e}")
    writer.commit()
    return count


def remove_document(article_id: str):
    """从索引中删除新闻"""
    idx = _get_index()
    writer = idx.writer()
    try:
        writer.delete_by_term('article_id', article_id)
    except Exception as e:
        logger.warning(f"索引删除失败: {e}")
    finally:
        writer.commit()


def optimize_index():
    """压缩优化索引（建议在每日调度任务中调用），输出健康指标"""
    try:
        idx = _get_index()
        reader = idx.reader()
        doc_count = reader.doc_count()
        doc_count_all = reader.doc_count_all()
        deleted = doc_count_all - doc_count
        reader.close()

        logger.info(f"索引健康: 文档={doc_count}, 已删除={deleted}, "
                    f"删除率={deleted/max(doc_count_all,1)*100:.1f}%")

        if deleted > 100:
            writer = idx.writer()
            writer.commit(optimize=True)
            logger.info(f"索引优化完成，清理 {deleted} 条已删除文档")
        else:
            logger.debug(f"索引删除文档数 {deleted}，跳过优化")
    except Exception as e:
        logger.warning(f"索引优化失败: {e}")


def search(
    query: str,
    page: int = 1,
    limit: int = 20,
    category: str = None,
    source: str = None,
) -> Tuple[List[str], int]:
    """
    全文搜索（Whoosh 后端）

    Args:
        query: 搜索查询（支持多词，如 "AI 芯片 半导体"）
        page: 页码
        limit: 每页数量
        category: 按分类过滤
        source: 按来源过滤

    Returns:
        (article_id 列表, 总结果数)
    """
    if not query or not query.strip():
        return [], 0

    idx = _get_index()

    # 多字段搜索：标题 > 正文 > 摘要
    qp = MultifieldParser(
        ['title', 'content', 'summary'],
        schema=_schema,
    )
    qp.add_plugin(FuzzyTermPlugin())
    qp.add_plugin(GroupPlugin())

    try:
        q = qp.parse(query)
    except Exception as e:
        logger.warning(f"查询解析失败: {e}")
        return [], 0

    # 分类/来源过滤（作为查询条件 AND 组合）
    from whoosh.query import And, Term
    if category:
        q = And([q, Term('category', category)])
    if source:
        q = And([q, Term('source', source)])

    with idx.searcher() as searcher:
        results = searcher.search(q, limit=page * limit)
        total = results.estimated_length()
        article_ids = [r['article_id'] for r in results]

    # 分页
    offset = (page - 1) * limit
    return article_ids[offset:offset + limit], total


# ═══════════════════════════════════════════════════════════════
# PostgreSQL 全文搜索（pg_jieba / zhparser tsvector）
# 仅当 DATABASE_URL 为 PostgreSQL 时可用，Whoosh 作为 fallback
# ═══════════════════════════════════════════════════════════════

_IS_PG = None


def _check_pg() -> bool:
    global _IS_PG
    if _IS_PG is None:
        from backend.database.engine import DATABASE_URL
        _IS_PG = DATABASE_URL.startswith("postgresql")
    return _IS_PG


async def search_pg(
    query: str,
    page: int = 1,
    limit: int = 20,
    category: str = None,
    source: str = None,
) -> Tuple[List[str], int]:
    """
    PostgreSQL 全文搜索（tsvector + tsquery）
    要求 search_vector 列已通过触发器维护（见 migration 规范）
    """
    if not query or not query.strip():
        return [], 0

    from backend.database.engine import AsyncSessionLocal
    from sqlalchemy import text

    try:
        async with AsyncSessionLocal() as session:
            where_clauses = ["search_vector @@ plainto_tsquery('chinese', :query)"]
            params = {"query": query, "limit": limit, "offset": (page - 1) * limit}

            if category:
                where_clauses.append("category = :category")
                params["category"] = category
            if source:
                where_clauses.append("source = :source")
                params["source"] = source

            where_sql = " AND ".join(where_clauses)

            count_row = (
                await session.execute(
                    text(f"SELECT COUNT(*) FROM news_articles WHERE {where_sql}"),
                    params,
                )
            ).fetchone()
            total = count_row[0] if count_row else 0

            rows = (
                await session.execute(
                    text(
                        f"SELECT article_id, ts_rank(search_vector, plainto_tsquery('chinese', :query)) AS rank "
                        f"FROM news_articles WHERE {where_sql} "
                        f"ORDER BY rank DESC LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).fetchall()

            article_ids = [r[0] for r in rows]
            return article_ids, total
    except Exception as e:
        logger.warning(f"PG 全文搜索失败，回退到 Whoosh: {e}")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_whoosh_executor, search, query, page, limit, category, source)


async def search_async(
    query: str,
    page: int = 1,
    limit: int = 20,
    category: str = None,
    source: str = None,
) -> Tuple[List[str], int]:
    """统一搜索入口：PG 优先，Whoosh fallback"""
    if _check_pg():
        try:
            return await search_pg(query, page, limit, category, source)
        except Exception as e:
            logger.debug(f"PG 搜索失败，降级到 Whoosh: {e}")
    # 同步 Whoosh 搜索（在专用线程池中执行，避免阻塞事件循环）
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_whoosh_executor, search, query, page, limit, category, source)
