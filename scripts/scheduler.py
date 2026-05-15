#!/usr/bin/env python3
"""
经济新闻采集调度器
定时执行新闻爬取和分析任务
集成 GraphRAG 增量更新、跨批次去重、搜索索引一致性、用户行为热度
"""

import hashlib
import json
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta

# 项目根目录（脚本所在目录的父目录）
current_dir = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(current_dir)
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 确保 logs 目录存在
logs_dir = os.path.join(PROJECT_ROOT, 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(logs_dir, 'scheduler.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

from backend.crawler.news_crawler import NewsCrawler
from backend.database.db_manager_async import db_async as db, _IS_PG
from backend.database.redis_client import redis_client
from backend.analyzer.text_analyzer import TextAnalyzer
from backend.analyzer.sentiment import SentimentAnalyzer, HAS_SNOWNLP
from backend.analyzer.dedup import NewsDeduplicator

# Prometheus 指标（scheduler 单进程，不设 PROMETHEUS_MULTIPROC_DIR）
if "PROMETHEUS_MULTIPROC_DIR" in os.environ:
    logger.warning("scheduler 检测到 PROMETHEUS_MULTIPROC_DIR，已忽略")
    del os.environ["PROMETHEUS_MULTIPROC_DIR"]

from backend.monitoring.metrics import (
    use_registry, CRAWL_ARTICLES, CRAWL_ERRORS, LLM_CALLS,
    DEDUP_RATIO, SEARCH_INDEX_LAG, HOTNESS_ARTICLES_TOTAL,
)
from hotness import update_hotness_scores as _update_hotness
from metrics_push import push_metrics
from sync_task import incremental_sync, check_index_consistency as _check_index

# 模块级单例，避免每次调度重复创建
_crawler = NewsCrawler(delay=0.1)
_analyzer = TextAnalyzer()
_sentiment = SentimentAnalyzer()

# GraphRAG 单例
_graph_rag_engine = None

# LLM 调用计数
_DAILY_LLM_CALLS = 0
_MAX_DAILY_LLM_CALLS = 500

_crawl_running = False


def _get_rag_engine():
    """获取 GraphRAG 引擎模块级单例"""
    global _graph_rag_engine
    if _graph_rag_engine is None:
        from graph_rag.rag_engine import GraphRAGEngine
        from graph_rag.llm_client import CloudLLMClient
        llm = CloudLLMClient(
            api_type=os.getenv('LLM_API_TYPE', 'openai'),
            api_key=os.getenv('LLM_API_KEY'),
            model=os.getenv('LLM_MODEL', 'qwen-plus')
        )
        _graph_rag_engine = GraphRAGEngine(llm, db)
    return _graph_rag_engine


async def _incremental_update_with_limit(article_dicts):
    """带 LLM 日调用上限的增量图谱更新"""
    global _DAILY_LLM_CALLS
    if _DAILY_LLM_CALLS >= _MAX_DAILY_LLM_CALLS:
        logger.warning(f"LLM 调用已达日上限 {_MAX_DAILY_LLM_CALLS}，跳过图谱增量更新")
        return
    estimated = len(article_dicts) * 2
    if _DAILY_LLM_CALLS + estimated > _MAX_DAILY_LLM_CALLS:
        article_dicts = article_dicts[:(_MAX_DAILY_LLM_CALLS - _DAILY_LLM_CALLS) // 2]
    engine = _get_rag_engine()
    count = await engine.incremental_update(article_dicts)
    _DAILY_LLM_CALLS += count * 2


async def crawl_and_analyze_news():
    """爬取和分析新闻的主任务 — 每 15 分钟执行一次"""
    global _crawl_running
    if _crawl_running:
        logger.warning("上一轮爬取尚未完成，跳过本次")
        return
    _crawl_running = True
    try:
        logger.info("开始执行新闻爬取和分析任务...")

        # 1. 爬取最新新闻
        logger.info("正在爬取最新经济新闻...")
        news_list = await asyncio.to_thread(_crawler.crawl_latest_news, limit=60)

        if not news_list:
            logger.warning("未获取到新闻数据")
            return

        total_crawled = len(news_list)
        logger.info(f"成功爬取 {total_crawled} 条新闻")

        # 指标：按数据源统计爬取量
        source_counts = {}
        for n in news_list:
            src = n.get('source', 'unknown')
            source_counts[src] = source_counts.get(src, 0) + 1
        for src, cnt in source_counts.items():
            CRAWL_ARTICLES.labels(source=src).inc(cnt)

        # 1.5.1 SimHash 批次内去重
        deduplicator = NewsDeduplicator(threshold=4)
        before_batch = len(news_list)
        news_list = await asyncio.to_thread(deduplicator.deduplicate, news_list)
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
        before_cross = len(news_list)
        filtered = []
        for news in news_list:
            url = news.get('url', '')
            if url:
                filtered.append(news)
            elif not await deduplicator.is_duplicate_cross_batch(
                news.get('title', '') + ' ' + (news.get('content', '') or news.get('summary', '')),
                redis_client, db
            ):
                filtered.append(news)
        news_list = filtered
        cross_deduped = before_cross - len(news_list)

        # 输出去重指标
        dedup_total = batch_deduped + url_deduped + cross_deduped
        dedup_rate = dedup_total / max(total_crawled, 1) * 100
        DEDUP_RATIO.set(1 - len(news_list) / max(total_crawled, 1))
        logger.info(f"去重统计: 批次内SimHash={batch_deduped}, 批次内URL={url_deduped}, "
                    f"跨批次SimHash={cross_deduped}, 剩余={len(news_list)}, "
                    f"去重率={dedup_rate:.1f}%")

        if not news_list:
            logger.info("去重后无新新闻，跳过入库")
            return

        # 2. 保存到数据库，识别增量新闻
        logger.info("正在保存新闻到数据库...")

        news_dicts = []
        for news in news_list:
            try:
                url = news.get('url', '')
                if url:
                    article_id = hashlib.md5(url.encode()).hexdigest()[:16]
                else:
                    article_id = hashlib.md5(news.get('title', '').encode()).hexdigest()[:16]
                news_dicts.append({
                    'article_id': article_id,
                    'title': news.get('title', ''),
                    'content': news.get('content', ''),
                    'summary': news.get('summary', ''),
                    'url': news.get('url', ''),
                    'source': news.get('source', '新浪财经'),
                    'category': news.get('category', ''),
                    'published_at': news.get('published_at', ''),
                    'author': news.get('author', ''),
                    'read_count': news.get('read_count', 0),
                    'comment_count': news.get('comment_count', 0),
                    'tags': news.get('tags', [])
                })
            except Exception as e:
                logger.error(f"转换新闻数据失败: {e}")
                continue

        if news_dicts:
            success_count = await db.save_news_articles(news_dicts)
            logger.info(f"新闻入库完成: 新写入 {success_count} 条, 跳过 {len(news_dicts) - success_count} 条已存在")

            # 写 SimHash 指纹到 Redis（24h TTL）
            for news in news_list:
                text = news.get('title', '') + ' ' + (news.get('content', '') or news.get('summary', ''))
                if text.strip():
                    url = news.get('url', '')
                    if url:
                        aid = hashlib.md5(url.encode()).hexdigest()[:16]
                    else:
                        aid = hashlib.md5(news.get('title', '').encode()).hexdigest()[:16]
                    fp = deduplicator._compute_fingerprint(text)
                    await redis_client.set(f'simhash:{aid}', str(fp), ttl=86400)

            # 同步更新全文搜索索引（批量写入）
            try:
                from backend.analyzer.search_engine import add_documents_batch
                count = await asyncio.to_thread(add_documents_batch, [
                    {
                        'article_id': m.get('article_id', ''),
                        'title': m.get('title', ''),
                        'content': m.get('content', ''),
                        'summary': m.get('summary', ''),
                        'source': m.get('source', ''),
                        'category': m.get('category', ''),
                        'published_at': m.get('published_at', ''),
                    }
                    for m in news_dicts
                ])
                logger.info(f"全文搜索索引已同步更新 {count} 条")
            except Exception as e:
                logger.warning(f"更新搜索索引失败: {e}")

            # 搜索索引轻量一致性检查
            try:
                from backend.analyzer.search_engine import _get_index
                idx = await asyncio.to_thread(_get_index)
                reader = idx.reader()
                indexed_count = reader.doc_count()
                reader.close()
                db_count = await db.get_news_count()
                lag = db_count - indexed_count
                if lag > 50:
                    logger.warning(f"搜索索引落后 DB {lag} 条，将在凌晨全量修复")
                else:
                    logger.debug(f"索引一致性: DB={db_count}, 索引={indexed_count}, 差距={lag}")
            except Exception as e:
                logger.warning(f"轻量一致性检查失败: {e}")

        # 3. 仅对增量新闻做情感分析 + 关键词 + 摘要
        logger.info("正在进行新闻分析...")
        if news_dicts:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            positive_count = negative_count = neutral_count = 0

            unresolved = []
            # 批量查询已有分析结果，避免 N+1
            all_aids = [nd['article_id'] for nd in news_dicts]
            existing_map = await db.get_analysis_by_article_ids(all_aids)
            for nd in news_dicts:
                if nd['article_id'] not in existing_map:
                    unresolved.append(nd)

            if unresolved:
                def _analyze_full(nd):
                    text = (nd['title'] + ' ' + (nd['content'] or nd['summary'])[:300])
                    if not text.strip():
                        return None
                    s_result = _sentiment.analyze_sentiment(text)
                    keywords = _analyzer.extract_keywords(nd['content'] or nd['summary'] or '', 8)
                    summary = _analyzer.generate_summary(nd['content'] or nd['summary'] or '', max_sentences=3)
                    return {
                        'article_id': nd['article_id'],
                        'sentiment_score': s_result['sentiment_score'],
                        'sentiment_label': s_result['sentiment_label'],
                        'keywords': keywords,
                        'summary': summary,
                        'raw': json.dumps(s_result, ensure_ascii=False),
                    }

                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {executor.submit(_analyze_full, m): m for m in unresolved}
                    for future in as_completed(futures):
                        try:
                            r = future.result(timeout=15)
                            if r:
                                label = r['sentiment_label']
                                if label == 'positive':
                                    positive_count += 1
                                elif label == 'negative':
                                    negative_count += 1
                                else:
                                    neutral_count += 1
                                await db.save_analysis_result({
                                    'article_id': r['article_id'],
                                    'model_used': 'snownlp' if HAS_SNOWNLP else 'dict',
                                    'sentiment_score': r['sentiment_score'],
                                    'sentiment_label': label,
                                    'keywords': r['keywords'],
                                    'summary': r['summary'],
                                    'analysis_type': 'full',
                                    'result': r['raw'],
                                })
                        except Exception as exc:
                            logger.warning(f"分析失败: {exc}")

                logger.info(f"分析完成 — 正面: {positive_count} 负面: {negative_count} 中性: {neutral_count}")
            else:
                logger.info("无增量新闻，跳过分析")

        # 4. 预热缓存（直接写 Redis，不经过 HTTP 避免堵死 API）
        try:
            from backend.api.routes.news import MEDIA_SOURCES, EXCLUDED_SOURCES
            for page in [1, 2, 3]:
                for limit in [12, 24]:
                    news, total = await db.get_latest_news(limit=limit, offset=(page-1)*limit,
                                                     exclude_sources=EXCLUDED_SOURCES)
                    await redis_client.set(f'news:latest:{page}:{limit}',
                                     {'code': 0, 'data': news, 'count': len(news), 'total': total, 'page': page},
                                     ttl=900)
                    mnews, mtotal = await db.get_news_by_sources(MEDIA_SOURCES, limit=limit, offset=(page-1)*limit)
                    await redis_client.set(f'news:media:{page}:{limit}',
                                     {'code': 0, 'data': mnews, 'count': len(mnews), 'total': mtotal, 'page': page},
                                     ttl=900)
            logger.info("缓存预热完成（直接写 Redis）")
        except Exception as e:
            logger.warning(f"缓存预热失败: {e}")

        # 5. 优化搜索索引
        try:
            from backend.analyzer.search_engine import optimize_index
            await asyncio.to_thread(optimize_index)
        except Exception as e:
            logger.warning(f"索引优化失败: {e}")

        # 6. WAL checkpoint 清理（防止 WAL 文件无限增长，仅 SQLite）
        try:
            if not _IS_PG:
                await db.execute_write('PRAGMA wal_checkpoint(TRUNCATE)')
                logger.info("WAL checkpoint 完成")
        except Exception as e:
            logger.warning(f"WAL checkpoint 失败: {e}")

        # 7. 增量更新知识图谱
        try:
            article_dicts = [
                {
                    'article_id': m.get('article_id', ''),
                    'title': m.get('title', ''),
                    'content': m.get('content', ''),
                    'summary': m.get('summary', ''),
                    'source': m.get('source', ''),
                    'published_at': m.get('published_at', ''),
                }
                for m in news_dicts
            ]
            await _incremental_update_with_limit(article_dicts)
        except Exception as e:
            logger.warning(f"图谱增量更新失败: {e}")

        push_metrics(use_registry)
        logger.info("新闻爬取和分析任务执行完成")

    except Exception as e:
        logger.error(f"执行新闻爬取和分析任务时出错: {e}", exc_info=True)
    finally:
        _crawl_running = False


async def check_search_index_consistency():
    """全量对比 DB 和 Whoosh 索引的 article_id 差异，修复缺失条目。"""
    await _check_index(db, SEARCH_INDEX_LAG)


async def update_hotness_scores():
    """聚合过去 24h 事件，计算热度分（含时间衰减 + 防刷去重）"""
    await _update_hotness(db, HOTNESS_ARTICLES_TOTAL)


async def full_analysis():
    """
    每日凌晨全面分析任务 — 比常规爬取更重型
    包括：批量情感分析、分类统计、趋势报告生成、图谱全量重建、索引全量检查
    """
    global _DAILY_LLM_CALLS
    try:
        logger.info("=" * 60)
        logger.info("开始执行每日全面分析任务...")
        logger.info("=" * 60)

        _DAILY_LLM_CALLS = 0  # 重置 LLM 计数器

        _analyzer_local = _analyzer
        _sentiment_local = _sentiment

        # 1. 获取近期所有新闻
        news_list, total = await db.get_latest_news(limit=500)
        logger.info(f"获取到 {total} 条新闻用于分析")

        if not news_list:
            logger.warning("无新闻数据，跳过全面分析")
            return

        # 2. 仅对未分析过的新闻做全量分析
        logger.info("正在进行批量全量分析...")
        positive_count = negative_count = neutral_count = total_score = 0.0
        analyzed_count = 0

        unresolved = []
        # 批量查询已有分析结果，避免 N+1
        all_aids = [news.get('article_id') for news in news_list if news.get('article_id')]
        existing_map = await db.get_analysis_by_article_ids(all_aids)
        for news in news_list:
            aid = news.get('article_id')
            if not aid:
                continue
            if aid not in existing_map:
                unresolved.append(news)

        if unresolved:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _full_analyze_one(news):
                text = (news.get('title', '') + ' ' +
                        (news.get('content', '') or news.get('summary', ''))[:300])
                if not text.strip():
                    return None
                s_result = _sentiment_local.analyze_sentiment(text)
                content = news.get('content', '') or news.get('summary', '')
                keywords = _analyzer_local.extract_keywords(content, 8)
                summary = _analyzer_local.generate_summary(content, max_sentences=3)
                return {
                    'aid': news.get('article_id', ''),
                    'sentiment_score': s_result['sentiment_score'],
                    'sentiment_label': s_result['sentiment_label'],
                    'keywords': keywords,
                    'summary': summary,
                    'raw': json.dumps(s_result, ensure_ascii=False),
                }

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(_full_analyze_one, n): n for n in unresolved}
                for future in as_completed(futures):
                    try:
                        r = future.result(timeout=15)
                        if r:
                            label = r['sentiment_label']
                            if label == 'positive':
                                positive_count += 1
                            elif label == 'negative':
                                negative_count += 1
                            else:
                                neutral_count += 1
                            total_score += r['sentiment_score']
                            analyzed_count += 1
                            await db.save_analysis_result({
                                'article_id': r['aid'],
                                'model_used': 'snownlp' if HAS_SNOWNLP else 'dict',
                                'sentiment_score': r['sentiment_score'],
                                'sentiment_label': label,
                                'keywords': r['keywords'],
                                'summary': r['summary'],
                                'analysis_type': 'full',
                                'result': r['raw'],
                            })
                    except Exception as exc:
                        logger.warning(f"全量分析失败: {exc}")

        if analyzed_count > 0:
            avg_score = round(total_score / analyzed_count, 4) if analyzed_count else 0
            logger.info(f"全量分析 {analyzed_count} 条完成")
            logger.info(f"  正面: {positive_count} 负面: {negative_count} 中性: {neutral_count} 平均: {avg_score}")
        else:
            logger.info("所有新闻均已分析过，跳过")

        # 3. 分类统计
        category_count = {}
        for news in news_list:
            cat = news.get("category", "未分类")
            category_count[cat] = category_count.get(cat, 0) + 1

        logger.info("分类统计:")
        for cat, cnt in sorted(category_count.items(), key=lambda x: x[1], reverse=True):
            logger.info(f"  {cat}: {cnt} 条")

        # 4. 关键词趋势
        logger.info("正在提取热门关键词...")
        contents = [n.get('content', '') or n.get('summary', '') for n in news_list if n.get('content') or n.get('summary')]
        if contents:
            all_content = ' '.join(contents)
            keywords = _analyzer_local.extract_keywords(all_content, top_k=20)
            logger.info(f"热门关键词: {', '.join(keywords)}")
        else:
            logger.info("无可提取内容，跳过关键词分析")

        # 5. 优化搜索索引
        try:
            from backend.analyzer.search_engine import optimize_index
            await asyncio.to_thread(optimize_index)
        except Exception as e:
            logger.warning(f"索引优化失败: {e}")

        # 6. 搜索索引全量一致性检查
        try:
            await check_search_index_consistency()
        except Exception as e:
            logger.warning(f"搜索索引一致性检查失败: {e}")

        # 7. 全量重建知识图谱
        try:
            engine = _get_rag_engine()
            await engine.build_knowledge_graph(limit=500)
            logger.info("知识图谱全量重建完成")
        except Exception as e:
            logger.warning(f"知识图谱全量重建失败: {e}")

        # 8. 热度分计算 + 旧事件清理
        try:
            await update_hotness_scores()
        except Exception as e:
            logger.warning(f"热度分计算失败: {e}")

        # 9. WAL checkpoint 清理（仅 SQLite）
        try:
            if not _IS_PG:
                await db.execute_write('PRAGMA wal_checkpoint(TRUNCATE)')
                logger.info("WAL checkpoint 完成")
        except Exception as e:
            logger.warning(f"WAL checkpoint 失败: {e}")

        logger.info("=" * 60)
        logger.info("每日全面分析任务执行完成")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"每日全面分析任务出错: {e}", exc_info=True)


async def main():
    """主函数"""
    logger.info("启动经济新闻调度系统...")

    async def _crawl_loop():
        await crawl_and_analyze_news()
        while True:
            await asyncio.sleep(15 * 60)
            await crawl_and_analyze_news()

    async def _daily_loop():
        now = datetime.now()
        target = now.replace(hour=2, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        await full_analysis()
        while True:
            await asyncio.sleep(24 * 3600)
            await full_analysis()

    logger.info("调度器已启动，等待定时任务执行...")
    logger.info("  - 常规爬取: 每 15 分钟")
    logger.info("  - 全面分析: 每天凌晨 02:00")

    try:
        await asyncio.gather(
            asyncio.create_task(_crawl_loop()),
            asyncio.create_task(_daily_loop()),
        )
    except KeyboardInterrupt:
        logger.info("调度器已停止")
    except Exception as e:
        logger.error(f"调度器运行出错: {e}")


if __name__ == "__main__":
    asyncio.run(main())
