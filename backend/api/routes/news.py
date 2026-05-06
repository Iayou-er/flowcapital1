"""
新闻相关API路由
"""
from fastapi import APIRouter, HTTPException, Query, Path, Depends
from pydantic import BaseModel, Field
from typing import List, Optional
import os
import logging
from backend.database.db_manager import db
from backend.database.redis_client import redis_client
from backend.analyzer.translator import Translator
from backend.api.auth import verify_api_key

news_router = APIRouter(prefix="/news", tags=["新闻"])

logger = logging.getLogger(__name__)

_CACHE_TTL = int(os.environ.get("REDIS_CACHE_TTL", 300))

# Pydantic models
class TranslateRequest(BaseModel):
    target_lang: str = Field(default='en', description="目标语言代码 (en/ja/ko/fr/de)")
    source_lang: str = Field(default='auto', description="源语言代码，auto 自动检测")


class TranslateTextRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000, description="待翻译文本")
    target_lang: str = Field(default='en', description="目标语言代码")
    source_lang: str = Field(default='auto', description="源语言代码")


# 翻译器懒初始化
_translator: Optional[Translator] = None

def get_translator() -> Translator:
    global _translator
    if _translator is None:
        _translator = Translator()
    return _translator


@news_router.get("/latest")
async def get_latest_news(
    page: int = Query(1, ge=1, le=1000, description="页码"),
    limit: int = Query(20, ge=1, le=200, description="每页数量")
):
    """获取最新新闻（支持分页）"""
    try:
        cache_key = f"news:latest:{page}:{limit}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached
        offset = (page - 1) * limit
        news, total = db.get_latest_news(limit=limit, offset=offset, exclude_sources=EXCLUDED_SOURCES)
        result = {"code": 0, "data": news, "count": len(news), "total": total, "page": page}
        redis_client.set(cache_key, result, ttl=_CACHE_TTL)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取新闻失败: {e}")


# 自媒体/公众号来源
MEDIA_SOURCES = [
    '微信公众号', '雪球', '虎嗅', '少数派', '36氪',
    '钛媒体', '观察者网',
]

# 首页需排除的源（自媒体 + 其他不展示的内容）
EXCLUDED_SOURCES = MEDIA_SOURCES + ['知乎']


@news_router.get("/fresh")
async def get_fresh_check():
    """轻量接口：返回最新文章时间戳 + 总数，用于前端自动轮询"""
    try:
        row = db._query_one(
            "SELECT COUNT(*), MAX(published_at) FROM news_articles WHERE source NOT IN ({})".format(
                ','.join('?' for _ in EXCLUDED_SOURCES)
            ),
            tuple(EXCLUDED_SOURCES)
        )
        return {
            "code": 0,
            "data": {
                "total": row[0] if row else 0,
                "latest_at": row[1] or '',
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@news_router.get("/media")
async def get_media_news(
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(20, ge=1, le=200),
):
    """获取自媒体/公众号板块内容（独立于传统新闻）"""
    try:
        cache_key = f"news:media:{page}:{limit}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached
        offset = (page - 1) * limit
        news, total = db.get_news_by_sources(MEDIA_SOURCES, limit=limit, offset=offset)
        result = {"code": 0, "data": news, "count": len(news), "total": total, "page": page}
        redis_client.set(cache_key, result, ttl=_CACHE_TTL)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取自媒体内容失败: {e}")


@news_router.get("/media/search")
async def search_media(
    q: str = Query(..., alias="q", min_length=1, max_length=200),
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(20, ge=1, le=200),
):
    """搜索自媒体内容 — 独立实现，SQL LIKE 限定媒体源"""
    try:
        query = q.strip()
        if not query:
            return {"code": 400, "data": [], "count": 0, "total": 0, "page": page}

        offset = (page - 1) * limit
        results, total = db.search_media(MEDIA_SOURCES, query, limit=limit, offset=offset)

        return {"code": 0, "data": results, "count": len(results), "total": total, "page": page}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索自媒体内容失败: {e}")


@news_router.get("/search")
async def search_news(
    q: str = Query(..., alias="q", min_length=1, max_length=200, description="搜索查询（支持多词，如 \"AI 芯片 半导体\"）"),
    keyword: str = Query(None, min_length=1, max_length=100, description="兼容旧参数：单关键词"),
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(20, ge=1, le=200),
    category: str = Query(None, max_length=50, description="按分类过滤"),
    source: str = Query(None, max_length=50, description="按来源过滤"),
):
    """全文搜索新闻（支持多关键词组合，基于 Whoosh + jieba 分词）"""
    # 清理搜索关键词，防止注入
    q = q.strip()
    if keyword:
        keyword = keyword.strip()
    if category:
        category = category.strip()
    if source:
        source = source.strip()

    try:
        query = q or keyword
        if not query:
            return {"code": 400, "data": [], "count": 0, "total": 0, "page": page}

        from backend.analyzer.search_engine import search as fulltext_search

        article_ids, total = fulltext_search(query, page=page, limit=limit, category=category, source=source)

        # Whoosh 空结果降级到 SQL LIKE
        if not article_ids:
            raise ValueError('Whoosh empty result')

        # 根据 article_id 批量获取完整新闻，排除自媒体源
        results = [
            r for r in db.get_news_by_article_ids(article_ids)
            if r.get('source', '') not in EXCLUDED_SOURCES
        ]

        return {"code": 0, "data": results, "count": len(results), "total": total, "page": page}
    except Exception as e:
        # 降级到传统 LIKE 搜索
        try:
            offset = (page - 1) * limit
            results, total = db.search_news(keyword=query, limit=limit, offset=offset)
            results = [r for r in results if r.get('source', '') not in EXCLUDED_SOURCES]
            return {"code": 0, "data": results, "count": len(results), "total": total, "page": page, "fallback": True}
        except Exception as fallback_err:
            logger.warning(f"Whoosh 搜索和 fallback LIKE 均失败: {e}, fallback: {fallback_err}")
            raise HTTPException(status_code=500, detail="搜索服务不可用")


@news_router.get("/categories")
async def get_categories():
    """获取所有可用分类及其新闻数量"""
    try:
        categories = db.get_all_categories()
        return {"code": 0, "data": categories}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分类失败: {e}")


@news_router.get("/category/{category}")
async def get_news_by_category(
    category: str = Path(..., min_length=1, max_length=50),
    page: int = Query(1, ge=1, le=1000),
    limit: int = Query(20, ge=1, le=200)
):
    """按分类获取新闻（支持分页）"""
    try:
        cache_key = f"news:category:{category}:{page}:{limit}"
        cached = redis_client.get(cache_key)
        if cached is not None:
            return cached
        offset = (page - 1) * limit
        news, total = db.get_news_by_category(category=category, limit=limit, offset=offset, exclude_sources=EXCLUDED_SOURCES)
        result = {"code": 0, "data": news, "count": len(news), "total": total, "page": page}
        redis_client.set(cache_key, result, ttl=_CACHE_TTL)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分类新闻失败: {e}")


@news_router.get("/{article_id}")
async def get_news_by_id(article_id: str = Path(..., max_length=200)):
    """获取特定新闻详情"""
    try:
        news = db.get_news_by_article_id(article_id)
        if not news:
            raise HTTPException(status_code=404, detail="新闻未找到")
        return {"code": 0, "data": news}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取新闻详情失败: {e}")


@news_router.post("/{article_id}/translate", dependencies=[Depends(verify_api_key)])
async def translate_news(
    article_id: str = Path(..., max_length=200),
    request: Optional[TranslateRequest] = None,
):
    """翻译特定新闻"""
    try:
        news = db.get_news_by_article_id(article_id)
        if not news:
            raise HTTPException(status_code=404, detail="新闻未找到")

        translator = get_translator()
        target_lang = request.target_lang if request else 'en'
        source_lang = request.source_lang if request else 'auto'

        detected = translator.detect_language(news.get('title', ''))

        # 并行翻译 title/summary/content
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_title = pool.submit(translator.translate, news.get('title', ''), source_lang, target_lang)
            f_summary = pool.submit(translator.translate, news.get('summary', ''), source_lang, target_lang)
            f_content = pool.submit(translator.translate, news.get('content', ''), source_lang, target_lang)
            title_translated = f_title.result(timeout=15)
            summary_translated = f_summary.result(timeout=15)
            content_translated = f_content.result(timeout=15)

        return {
            "code": 0,
            "data": {
                "original": {
                    "title": news.get('title', ''),
                    "summary": news.get('summary', ''),
                    "content": news.get('content', ''),
                },
                "translated": {
                    "title": title_translated,
                    "summary": summary_translated,
                    "content": content_translated,
                },
                "source_lang": detected,
                "target_lang": target_lang,
                "was_translated": detected != target_lang,
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"翻译失败: {e}")


@news_router.post("/translate", dependencies=[Depends(verify_api_key)])
async def translate_text(request: TranslateTextRequest):
    """通用文本翻译接口"""
    try:
        translator = get_translator()
        result = translator.auto_translate(request.text, target_lang=request.target_lang)
        return {"code": 0, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"翻译失败: {e}")


@news_router.get("/languages")
async def get_supported_languages():
    """获取支持的语言列表"""
    from backend.analyzer.translator import SUPPORTED_LANGS
    return {
        "code": 0,
        "data": {
            "languages": SUPPORTED_LANGS,
            "default_source": "auto",
            "default_target": "en",
        }
    }


@news_router.get("/duplicates")
async def find_duplicate_news(
    limit: int = Query(50, ge=1, le=200, description="检查的新闻数量"),
    threshold: int = Query(4, ge=1, le=10, description="SimHash 汉明距离阈值"),
):
    """查找数据库中重复的新闻报道（同一事件多源报道）"""
    try:
        from backend.analyzer.dedup import NewsDeduplicator

        news_list, _ = db.get_latest_news(limit=limit)
        if not news_list:
            return {"code": 0, "data": {"total": 0, "duplicate_pairs": []}}

        deduplicator = NewsDeduplicator(threshold=threshold)
        duplicates = deduplicator.find_duplicates(news_list)

        pairs = []
        for a, b, dist in duplicates:
            pairs.append({
                "article_a": {"id": a.get("article_id"), "title": a.get("title"), "source": a.get("source")},
                "article_b": {"id": b.get("article_id"), "title": b.get("title"), "source": b.get("source")},
                "distance": dist,
            })

        return {
            "code": 0,
            "data": {
                "total": len(pairs),
                "duplicate_pairs": pairs,
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"去重检测失败: {e}")


@news_router.post("/rebuild-index", dependencies=[Depends(verify_api_key)])
async def rebuild_search_index(
    limit: int = Query(500, ge=1, le=2000, description="重建索引的新闻数量上限"),
):
    """重建全文搜索索引（用于首次初始化或数据变更后重建）"""
    try:
        from backend.analyzer.search_engine import rebuild_index

        news_list, total = db.get_latest_news(limit=limit)
        if not news_list:
            return {"code": 0, "data": {"message": "无新闻可索引"}}

        count = rebuild_index(news_list)
        return {"code": 0, "data": {"message": f"索引完成，共索引 {count} 条新闻"}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重建索引失败: {e}")
