"""爬虫共享工具函数"""
import hashlib
import logging
import re
import uuid
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional

from .config import CATEGORY_PATTERNS

logger = logging.getLogger(__name__)


def classify(title: str, content: str = '') -> str:
    text = (title + ' ' + content).lower()
    for cat, pattern in CATEGORY_PATTERNS.items():
        if pattern.search(text):
            return cat
    return '综合财经'


def normalize_pubdate(raw: str) -> str:
    if not raw:
        return datetime.now().isoformat()
    if raw.startswith('202') and ('T' in raw or raw[4] == '-'):
        return raw[:25]
    if raw.isdigit():
        try:
            return datetime.fromtimestamp(int(raw)).isoformat()
        except Exception as e:
            logger.debug("Unix时间戳解析失败: %s", e)
    try:
        return parsedate_to_datetime(raw).isoformat()
    except Exception as e:
        logger.debug("RFC日期解析失败: %s", e)
    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d %H:%M:%S', '%m/%d/%Y %H:%M:%S']:
        try:
            return datetime.strptime(raw[:19], fmt).isoformat()
        except Exception:
            continue
    return datetime.now().isoformat()


def make_article(title: str, content: str, url: str, source: str,
                 published_at: str = '') -> Dict:
    cat = classify(title, content)
    return {
        'id': hashlib.md5((url or title).encode()).hexdigest()[:16] if url else str(uuid.uuid4())[:12],
        'article_id': hashlib.md5((url or title).encode()).hexdigest()[:16] if url else str(uuid.uuid4())[:12],
        'title': title[:200],
        'content': content[:10000],
        'summary': content[:300] if content else title[:300],
        'url': url,
        'source': source,
        'category': cat,
        'published_at': published_at or datetime.now().isoformat(),
        'author': '',
        'read_count': 0,
        'comment_count': 0,
        'tags': [cat],
        'created_at': datetime.now().isoformat(),
    }


def sort_by_quality(articles: List[Dict]) -> List[Dict]:
    def score(a: Dict) -> float:
        content_len = len(a.get('content', '') or '')
        title_len = len(a.get('title', '') or '')
        s = min(content_len / 100, 10) + min(title_len / 10, 5)
        high_quality_sources = {'金十数据', '华尔街见闻', '财联社', '第一财经', '东方财富'}
        if a.get('source', '') in high_quality_sources:
            s += 2
        return s
    return sorted(articles, key=score, reverse=True)
