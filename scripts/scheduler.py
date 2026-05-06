#!/usr/bin/env python3
"""
经济新闻采集调度器
定时执行新闻爬取和分析任务
"""

import hashlib
import json
import schedule
import time
import logging
import os
import sys

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
from backend.database.db_manager import db
from backend.analyzer.text_analyzer import TextAnalyzer
from backend.analyzer.sentiment import SentimentAnalyzer, HAS_SNOWNLP
from backend.analyzer.dedup import NewsDeduplicator

# 模块级单例，避免每次调度重复创建
_crawler = NewsCrawler(delay=0.1)
_analyzer = TextAnalyzer()
_sentiment = SentimentAnalyzer()


_crawl_running = False


def crawl_and_analyze_news():
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
        news_list = _crawler.crawl_latest_news(limit=60)

        if not news_list:
            logger.warning("未获取到新闻数据")
            return

        logger.info(f"成功爬取 {len(news_list)} 条新闻")

        # 1.5 SimHash 去重聚类
        deduplicator = NewsDeduplicator(threshold=4)
        news_list = deduplicator.deduplicate(news_list)
        if news_list:
            logger.info(f"去重后剩余 {len(news_list)} 条新闻")

        # 2. 保存到数据库，识别增量新闻
        logger.info("正在保存新闻到数据库...")
        from backend.database.models import NewsArticle

        news_models = []
        for news in news_list:
            try:
                url = news.get('url', '')
                if url:
                    article_id = hashlib.md5(url.encode()).hexdigest()[:16]
                else:
                    article_id = hashlib.md5(news.get('title', '').encode()).hexdigest()[:16]
                news_model = NewsArticle(
                    article_id=article_id,
                    title=news.get('title', ''),
                    content=news.get('content', ''),
                    summary=news.get('summary', ''),
                    url=news.get('url', ''),
                    source=news.get('source', '新浪财经'),
                    category=news.get('category', ''),
                    published_at=news.get('published_at', ''),
                    author=news.get('author', ''),
                    read_count=news.get('read_count', 0),
                    comment_count=news.get('comment_count', 0),
                    tags=news.get('tags', [])
                )
                news_models.append(news_model)
            except Exception as e:
                logger.error(f"转换新闻数据失败: {e}")
                continue

        if news_models:
            success_count = db.save_news_articles(news_models)
            logger.info(f"成功保存 {success_count} 条新闻到数据库")

            # 同步更新全文搜索索引（批量写入）
            try:
                from backend.analyzer.search_engine import add_documents_batch
                count = add_documents_batch([
                    {
                        'article_id': m.article_id,
                        'title': m.title,
                        'content': m.content,
                        'summary': m.summary,
                        'source': m.source,
                        'category': m.category,
                        'published_at': m.published_at,
                    }
                    for m in news_models
                ])
                logger.info(f"全文搜索索引已同步更新 {count} 条")
            except Exception as e:
                logger.warning(f"更新搜索索引失败: {e}")

        # 3. 仅对增量新闻做情感分析 + 关键词 + 摘要（基于 article_id 去重）
        logger.info("正在进行新闻分析...")
        if news_models:
            from backend.database.models import AnalysisResult
            from concurrent.futures import ThreadPoolExecutor, as_completed
            positive_count = negative_count = neutral_count = 0

            # 收集缺失分析的新闻
            unresolved = []
            for news_model in news_models:
                existing = db.get_analysis_by_article_id(news_model.article_id)
                if not existing:
                    unresolved.append(news_model)

            if unresolved:
                def _analyze_full(news_model):
                    text = (news_model.title + ' ' + (news_model.content or news_model.summary)[:300])
                    if not text.strip():
                        return None
                    s_result = _sentiment.analyze_sentiment(text)
                    keywords = _analyzer.extract_keywords(news_model.content or news_model.summary or '', 8)
                    summary = _analyzer.generate_summary(news_model.content or news_model.summary or '', max_sentences=3)
                    return {
                        'model': news_model,
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
                                db.save_analysis_result(AnalysisResult(
                                    article_id=r['model'].article_id,
                                    model_used='snownlp' if HAS_SNOWNLP else 'dict',
                                    sentiment_score=r['sentiment_score'],
                                    sentiment_label=label,
                                    keywords=r['keywords'],
                                    summary=r['summary'],
                                    analysis_type='full',
                                    result=r['raw'],
                                ))
                        except Exception as exc:
                            logger.warning(f"分析失败: {exc}")

                logger.info(f"分析完成 — 正面: {positive_count} 负面: {negative_count} 中性: {neutral_count}")
            else:
                logger.info("无增量新闻，跳过分析")

        # 4. 预热缓存（直接写 Redis，不经过 HTTP 避免堵死 API）
        try:
            from backend.database.redis_client import redis_client
            from backend.api.routes.news import MEDIA_SOURCES, EXCLUDED_SOURCES
            # 预计算最新新闻各页数据（排除自媒体源，与 /news/latest 保持一致）
            for page in [1, 2, 3]:
                for limit in [12, 24]:
                    news, total = db.get_latest_news(limit=limit, offset=(page-1)*limit,
                                                     exclude_sources=EXCLUDED_SOURCES)
                    redis_client.set(f'news:latest:{page}:{limit}',
                                     {'code': 0, 'data': news, 'count': len(news), 'total': total, 'page': page},
                                     ttl=900)
                    # 媒体页
                    mnews, mtotal = db.get_news_by_sources(MEDIA_SOURCES, limit=limit, offset=(page-1)*limit)
                    redis_client.set(f'news:media:{page}:{limit}',
                                     {'code': 0, 'data': mnews, 'count': len(mnews), 'total': mtotal, 'page': page},
                                     ttl=900)
            logger.info("缓存预热完成（直接写 Redis）")
        except Exception as e:
            logger.warning(f"缓存预热失败: {e}")

        # 5. 优化搜索索引
        try:
            from backend.analyzer.search_engine import optimize_index
            optimize_index()
        except Exception as e:
            logger.warning(f"索引优化失败: {e}")

        # 6. WAL checkpoint 清理（防止 WAL 文件无限增长）
        try:
            from backend.database.db_manager import db as _db
            _db._execute_write('PRAGMA wal_checkpoint(TRUNCATE)', commit=True)
            logger.info("WAL checkpoint 完成")
        except Exception as e:
            logger.warning(f"WAL checkpoint 失败: {e}")

        logger.info("新闻爬取和分析任务执行完成")

    except Exception as e:
        logger.error(f"执行新闻爬取和分析任务时出错: {e}", exc_info=True)
    finally:
        _crawl_running = False


def full_analysis():
    """
    每日凌晨全面分析任务 — 比常规爬取更重型
    包括：批量情感分析、分类统计、趋势报告生成
    """
    try:
        logger.info("=" * 60)
        logger.info("开始执行每日全面分析任务...")
        logger.info("=" * 60)

        _analyzer_local = _analyzer
        _sentiment_local = _sentiment
        from backend.database.models import AnalysisResult

        # 1. 获取近期所有新闻
        news_list, total = db.get_latest_news(limit=500)
        logger.info(f"获取到 {total} 条新闻用于分析")

        if not news_list:
            logger.warning("无新闻数据，跳过全面分析")
            return

        # 2. 仅对未分析过的新闻做全量分析（sentiment + keywords + summary）
        logger.info("正在进行批量全量分析...")
        positive_count = negative_count = neutral_count = total_score = 0.0
        analyzed_count = 0

        # 收集缺失分析的新闻
        unresolved = []
        for news in news_list:
            aid = news.get('article_id')
            if not aid:
                continue
            existing = db.get_analysis_by_article_id(aid)
            if not existing:
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
                            db.save_analysis_result(AnalysisResult(
                                article_id=r['aid'],
                                model_used='snownlp' if HAS_SNOWNLP else 'dict',
                                sentiment_score=r['sentiment_score'],
                                sentiment_label=label,
                                keywords=r['keywords'],
                                summary=r['summary'],
                                analysis_type='full',
                                result=r['raw'],
                            ))
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
            optimize_index()
        except Exception as e:
            logger.warning(f"索引优化失败: {e}")

        # 6. WAL checkpoint 清理（防止 WAL 文件无限增长）
        try:
            from backend.database.db_manager import db
            db._execute_write('PRAGMA wal_checkpoint(TRUNCATE)', commit=True)
            logger.info("WAL checkpoint 完成")
        except Exception as e:
            logger.warning(f"WAL checkpoint 失败: {e}")

        logger.info("=" * 60)
        logger.info("每日全面分析任务执行完成")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"每日全面分析任务出错: {e}", exc_info=True)


def main():
    """主函数"""
    logger.info("启动经济新闻调度系统...")

    # 每 15 分钟爬取一次最新新闻（轻量任务）
    schedule.every(15).minutes.do(crawl_and_analyze_news)

    # 每天凌晨 2 点执行一次全面分析（重型任务：情感分析 + 趋势报告）
    schedule.every().day.at("02:00").do(full_analysis)

    logger.info("调度器已启动，等待定时任务执行...")
    logger.info("  - 常规爬取: 每 15 分钟")
    logger.info("  - 全面分析: 每天凌晨 02:00")

    # 立即执行一次常规爬取
    crawl_and_analyze_news()

    # 运行调度器
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次
    except KeyboardInterrupt:
        logger.info("调度器已停止")
    except Exception as e:
        logger.error(f"调度器运行出错: {e}")


if __name__ == "__main__":
    main()
