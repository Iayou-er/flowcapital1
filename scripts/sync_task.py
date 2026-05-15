"""搜索索引同步任务"""
import asyncio
import logging

logger = logging.getLogger(__name__)


async def incremental_sync(db, articles: list) -> int:
    """增量同步 Whoosh 搜索索引"""
    try:
        from backend.analyzer.search_engine import add_documents_batch
        count = await asyncio.to_thread(add_documents_batch, articles)
        logger.info("全文搜索索引已同步更新 %d 条", count)
        return count
    except Exception as e:
        logger.warning("更新搜索索引失败: %s", e)
        return 0


async def check_index_consistency(db, lag_metric=None):
    """全量对比 DB 和 Whoosh 索引的 article_id 差异，修复缺失条目"""
    from backend.analyzer.search_engine import _get_index, add_documents_batch

    idx = await asyncio.to_thread(_get_index)
    reader = idx.reader()
    indexed_ids = set(r['article_id'] for r in reader.all_stored_fields() if r.get('article_id'))
    reader.close()

    db_ids = set(await db.get_recent_article_ids(limit=10000))

    missing = db_ids - indexed_ids
    if lag_metric:
        lag_metric.set(len(missing))
    if not missing:
        logger.info("搜索索引全量一致性检查通过")
        return

    logger.warning("搜索索引缺失 %d 条，开始修复...", len(missing))
    articles = await db.get_news_by_article_ids(list(missing))
    docs = [
        {
            'article_id': a['article_id'],
            'title': a.get('title', ''),
            'content': a.get('content', ''),
            'summary': a.get('summary', ''),
            'source': a.get('source', ''),
            'category': a.get('category', ''),
            'published_at': a.get('published_at', ''),
        }
        for a in articles
    ]
    count = await asyncio.to_thread(add_documents_batch, docs)
    logger.info("索引修复完成: %d 条", count)
