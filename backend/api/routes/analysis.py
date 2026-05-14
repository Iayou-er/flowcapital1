"""
分析相关API路由
"""
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timedelta
from collections import defaultdict
import os
from backend.database.db_manager import db
from backend.database.redis_client import redis_client
from backend.analyzer.sentiment import SentimentAnalyzer
from backend.analyzer.text_analyzer import TextAnalyzer
from backend.api.routes.news import MEDIA_SOURCES

analysis_router = APIRouter(prefix="/analysis", tags=["分析"])

# 懒加载单例
_sentiment = None
_text_analyzer = None

def _get_sentiment():
    global _sentiment
    if _sentiment is None:
        _sentiment = SentimentAnalyzer()
    return _sentiment

def _get_text_analyzer():
    global _text_analyzer
    if _text_analyzer is None:
        _text_analyzer = TextAnalyzer()
    return _text_analyzer

_CACHE_PREFIX = "analysis:"
_CACHE_TTL = int(os.environ.get("REDIS_CACHE_TTL", 300))


def _get_time_boundary(time_range: str) -> str:
    """根据时间范围返回起始日期"""
    now = datetime.now()
    if time_range == 'day':
        return (now - timedelta(days=1)).strftime('%Y-%m-%d')
    elif time_range == 'week':
        return (now - timedelta(days=7)).strftime('%Y-%m-%d')
    elif time_range == 'month':
        return (now - timedelta(days=30)).strftime('%Y-%m-%d')
    elif time_range == 'year':
        return (now - timedelta(days=365)).strftime('%Y-%m-%d')
    return (now - timedelta(days=7)).strftime('%Y-%m-%d')


def _build_analysis_map(article_ids):
    """从 analysis_results 表批量读取预计算结果"""
    if not article_ids:
        return {}
    placeholders = ','.join(['?'] * len(article_ids))
    sql = (
        f"SELECT article_id, sentiment_score, sentiment_label, keywords, summary "
        f"FROM analysis_results WHERE article_id IN ({placeholders})"
    )
    rows = db._query(sql, tuple(article_ids))
    result = {}
    for r in rows:
        kw = r.get('keywords', '')
        keywords = [k.strip() for k in kw.split(',') if k.strip()] if kw else []
        result[r['article_id']] = {
            'score': r['sentiment_score'] or 0.0,
            'label': r['sentiment_label'] or 'neutral',
            'keywords': keywords,
            'summary': r.get('summary', '') or '',
        }
    return result


@analysis_router.get("")
async def analyze_news(
    time_range: str = Query("week", pattern="^(day|week|month|year)$"),
    start_date: str = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    """分析新闻数据，返回统计结果 + 情感分析 + 情感趋势 + 分类热度供前端图表使用"""
    try:
        # 缓存 key
        cache_key = f"{_CACHE_PREFIX}{time_range}:{start_date}:{end_date}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached

        # ── 按时间范围过滤 ──
        if start_date and end_date:
            news_list = db.get_news_by_date_range(start_date, end_date + ' 23:59:59')
        else:
            boundary = _get_time_boundary(time_range)
            news_list = db.get_news_by_date_range(boundary, '2099-12-31')

        # 排除自媒体源
        news_list = [n for n in news_list if n.get('source', '') not in MEDIA_SOURCES]

        if not news_list:
            result = {"code": 0, "data": {
                "totalNews": 0,
                "categoryDistribution": [],
                "categoryHeat": [],
                "topKeywords": [],
                "newsTrend": [],
                "sentimentTrend": [],
                "sentiment": {
                    "overallScore": 0, "overallLabel": "neutral", "confidence": 0,
                    "distribution": {"positive": 0, "negative": 0, "neutral": 0},
                    "positiveCount": 0, "negativeCount": 0, "neutralCount": 0,
                }
            }}
            redis_client.set(cache_key, result, ttl=_CACHE_TTL)
            return result

        # ── 步骤1：优先从预计算结果读取，缺失的才并行计算 ──
        article_ids = [n['article_id'] for n in news_list if n.get('article_id')]
        existing_map = _build_analysis_map(article_ids)

        news_sentiments = []
        all_keywords = []
        missing_articles = []  # [(news_dict, index), ...]
        for i, news in enumerate(news_list):
            aid = news.get('article_id', '')
            if aid in existing_map:
                r = existing_map[aid]
                news_sentiments.append((r['score'], r['label']))
                all_keywords.extend(r['keywords'])
            else:
                news_sentiments.append(None)
                missing_articles.append((news, i))

        # 仅对缺失的新闻并行计算 sentiment + keywords + summary
        if missing_articles:
            import json
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from backend.database.models import AnalysisResult
            from backend.analyzer.text_analyzer import TextAnalyzer
            sentiment = _get_sentiment()
            text_analyzer = TextAnalyzer()

            def _analyze_one(news):
                content = news.get('content', '') or news.get('summary', '')
                text = (news.get('title', '') + ' ' + content[:200])
                if not text.strip():
                    return None
                s_result = sentiment.analyze_sentiment(text)
                keywords = text_analyzer.extract_keywords(content, 8)
                summary = text_analyzer.generate_summary(content, max_sentences=3)
                return {
                    'score': s_result['sentiment_score'],
                    'label': s_result['sentiment_label'],
                    'keywords': keywords,
                    'summary': summary,
                    'raw': json.dumps(s_result, ensure_ascii=False),
                }

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(_analyze_one, news): (news, idx) for news, idx in missing_articles}
                for future in as_completed(futures):
                    news, idx = futures[future]
                    try:
                        r = future.result(timeout=15)
                        if r:
                            db.save_analysis_result(AnalysisResult(
                                article_id=news.get('article_id', ''),
                                model_used='snownlp',
                                sentiment_score=r['score'],
                                sentiment_label=r['label'],
                                keywords=r['keywords'],
                                summary=r['summary'],
                                analysis_type='sentiment',
                                result=r['raw'],
                            ))
                            news_sentiments[idx] = (r['score'], r['label'])
                            all_keywords.extend(r['keywords'])
                        else:
                            news_sentiments[idx] = (0.0, 'neutral')
                    except Exception:
                        news_sentiments[idx] = (0.0, 'neutral')

        # 填充剩余 None
        news_sentiments = [(s[0] if s else 0.0, s[1] if s else 'neutral') for s in news_sentiments]

        # ── 步骤2：分类统计 ──
        category_data = defaultdict(lambda: {"count": 0, "sentiment_sum": 0.0})
        for news, (score, _) in zip(news_list, news_sentiments):
            cat = news.get("category", "未分类")
            category_data[cat]["count"] += 1
            category_data[cat]["sentiment_sum"] += score

        category_distribution = [
            {"name": k, "value": v["count"]}
            for k, v in sorted(category_data.items(), key=lambda x: x[1]["count"], reverse=True)
        ]

        category_heat = [
            {
                "category": k,
                "count": v["count"],
                "avgSentiment": round(v["sentiment_sum"] / v["count"], 4) if v["count"] else 0
            }
            for k, v in sorted(category_data.items(), key=lambda x: x[1]["count"], reverse=True)
        ]

        # ── 步骤3：关键词统计（优先用预计算结果） ──
        if all_keywords:
            from collections import Counter
            kw_counter = Counter(all_keywords)
            top_keywords = [
                {"keyword": k, "count": v}
                for k, v in kw_counter.most_common(15)
            ]
        else:
            top_keywords = []

        # ── 步骤4：新闻趋势 + 情感趋势 ──
        daily_data = defaultdict(lambda: {"count": 0, "sentiment_sum": 0.0, "positive": 0, "negative": 0, "neutral": 0})
        for news, (score, label) in zip(news_list, news_sentiments):
            pub_date = news.get("published_at", "")[:10]
            if pub_date:
                daily_data[pub_date]["count"] += 1
                daily_data[pub_date]["sentiment_sum"] += score
                daily_data[pub_date][label] += 1

        news_trend = [
            {"date": k, "count": v["count"]}
            for k, v in sorted(daily_data.items())
        ]

        sentiment_trend = [
            {
                "date": k,
                "avgSentiment": round(v["sentiment_sum"] / v["count"], 4) if v["count"] else 0,
                "positive": v["positive"],
                "negative": v["negative"],
                "neutral": v["neutral"]
            }
            for k, v in sorted(daily_data.items())
        ]

        # ── 步骤5：整体情感 ──
        all_scores = [s for s, _ in news_sentiments]
        avg_score = round(sum(all_scores) / len(all_scores), 4) if all_scores else 0
        pos_count = sum(1 for _, l in news_sentiments if l == 'positive')
        neg_count = sum(1 for _, l in news_sentiments if l == 'negative')
        neu_count = sum(1 for _, l in news_sentiments if l == 'neutral')

        if avg_score > 0.15:
            overall_label = 'positive'
        elif avg_score < -0.15:
            overall_label = 'negative'
        else:
            overall_label = 'neutral'

        result = {
            "code": 0,
            "data": {
                "categoryDistribution": category_distribution,
                "categoryHeat": category_heat,
                "topKeywords": top_keywords,
                "newsTrend": news_trend,
                "sentimentTrend": sentiment_trend,
                "totalNews": len(news_list),
                "sentiment": {
                    "overallScore": avg_score,
                    "overallLabel": overall_label,
                    "confidence": round(abs(avg_score), 4),
                    "distribution": {
                        "positive": pos_count,
                        "negative": neg_count,
                        "neutral": neu_count
                    },
                    "positiveCount": pos_count,
                    "negativeCount": neg_count,
                    "neutralCount": neu_count
                }
            },
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "time_range": time_range,
                "source": "经济新闻分析系统"
            }
        }
        redis_client.set(cache_key, result, ttl=_CACHE_TTL)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"新闻分析失败: {e}")


@analysis_router.get("/hero")
async def hero_stats():
    """首页 Hero 区轻量数据接口 — 仅返回 DB 聚合数据"""
    try:
        cache_key = f"{_CACHE_PREFIX}hero"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached

        total_news = db.get_news_count()
        if total_news == 0:
            result = {"code": 0, "data": {"totalNews": 0, "sentiment": {
                "overallScore": 0, "overallLabel": "neutral", "confidence": 0,
                "positiveCount": 0, "negativeCount": 0, "neutralCount": 0
            }}}
            redis_client.set(cache_key, result, ttl=_CACHE_TTL * 2)
            return result

        avg_score, pos_count, neg_count, neu_count = db.get_sentiment_aggregate()

        if avg_score > 0.15:
            overall_label = 'positive'
        elif avg_score < -0.15:
            overall_label = 'negative'
        else:
            overall_label = 'neutral'

        result = {
            "code": 0,
            "data": {
                "totalNews": total_news,
                "sentiment": {
                    "overallScore": avg_score,
                    "overallLabel": overall_label,
                    "confidence": round(abs(avg_score), 4),
                    "positiveCount": pos_count,
                    "negativeCount": neg_count,
                    "neutralCount": neu_count,
                }
            }
        }
        redis_client.set(cache_key, result, ttl=_CACHE_TTL * 2)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取首页数据失败: {e}")


@analysis_router.get("/breakdown")
async def sentiment_breakdown(
    time_range: str = Query("week", pattern="^(day|week|month|year)$"),
):
    """P4: 情感细分 — 按来源/分类/实体"""
    try:
        cache_key = f"analysis:breakdown:{time_range}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached

        from backend.analyzer.text_analyzer import TextAnalyzer
        boundary = _get_time_boundary(time_range)
        news_list = db.get_news_by_date_range(boundary, '2099-12-31')
        news_list = [n for n in news_list if n.get('source', '') not in MEDIA_SOURCES]

        analyzer = TextAnalyzer()
        result = {"code": 0, "data": analyzer.sentiment_breakdown(news_list)}
        redis_client.set(cache_key, result, ttl=_CACHE_TTL)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"情感细分失败: {e}")


@analysis_router.get("/entities")
async def extract_entities(
    article_id: str = Query(None, max_length=200),
    limit: int = Query(20, ge=1, le=100),
):
    """P1: 实体识别 — 单篇或批量"""
    try:
        from backend.analyzer.text_analyzer import TextAnalyzer
        analyzer = TextAnalyzer()

        if article_id:
            news = db.get_news_by_article_id(article_id)
            if not news:
                raise HTTPException(status_code=404, detail="新闻未找到")
            content = news.get('content', '') or news.get('summary', '')
            entities = analyzer.extract_entities(news.get('title', '') + ' ' + content)
            return {"code": 0, "data": {"article_id": article_id, "entities": entities}}

        news_list, _ = db.get_latest_news(limit=limit, exclude_sources=MEDIA_SOURCES)
        results = []
        for n in news_list:
            content = n.get('content', '') or n.get('summary', '')
            entities = analyzer.extract_entities((n.get('title', '') + ' ' + content)[:3000])
            if entities['companies'] or entities['people']:
                results.append({'article_id': n.get('article_id'), 'title': n.get('title', '')[:50], 'entities': entities})
        return {"code": 0, "data": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"实体识别失败: {e}")


@analysis_router.get("/hot-topics")
async def hot_topics(
    limit: int = Query(100, ge=10, le=500),
):
    """P3: 热点话题检测"""
    try:
        cache_key = f"analysis:hot:{limit}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached

        from backend.analyzer.text_analyzer import TextAnalyzer
        news_list, _ = db.get_latest_news(limit=limit, exclude_sources=MEDIA_SOURCES)

        topics = TextAnalyzer.detect_hot_topics(news_list, top_k=15)
        result = {"code": 0, "data": topics}
        redis_client.set(cache_key, result, ttl=_CACHE_TTL)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"热点检测失败: {e}")


@analysis_router.get("/clusters")
async def article_clusters(
    limit: int = Query(200, ge=20, le=500),
):
    """P5: 文章聚类"""
    try:
        cache_key = f"analysis:clusters:{limit}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached

        from backend.analyzer.text_analyzer import TextAnalyzer
        news_list, _ = db.get_latest_news(limit=limit, exclude_sources=MEDIA_SOURCES)

        clusters = TextAnalyzer.cluster_articles(news_list, threshold=0.35)
        result = {"code": 0, "data": clusters}
        redis_client.set(cache_key, result, ttl=_CACHE_TTL * 2)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"聚类失败: {e}")


@analysis_router.post("/reload-dict")
async def reload_finance_dict():
    """热更新财经情感词典"""
    from backend.analyzer.finance_sentiment_dict import reload_dict
    reload_dict()
    return {'code': 0, 'message': '词典已重载'}


@analysis_router.get("/{article_id}")
async def analyze_single_article(article_id: str):
    """分析单条新闻，返回情感、关键词和自动摘要"""
    try:
        cache_key = f"article_analysis:{article_id}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached

        news = db.get_news_by_article_id(article_id)
        if not news:
            raise HTTPException(status_code=404, detail="新闻未找到")

        analyzer = _get_text_analyzer()
        result = analyzer.analyze_news_article(news)

        response = {"code": 0, "data": result}
        redis_client.set(cache_key, response, ttl=_CACHE_TTL)
        return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析失败: {e}")
