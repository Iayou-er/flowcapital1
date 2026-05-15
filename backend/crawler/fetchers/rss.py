"""RSS 类数据源"""
import logging
from typing import List, Dict

import feedparser

from ..base import BaseFetcher
from ..utils import normalize_pubdate, classify, make_article
from .registry import register

logger = logging.getLogger(__name__)

RSS_FEEDS = {
    "rss_xinhua": ("新华网", "http://www.xinhuanet.com/politics/xhll.xml"),
    "rss_caixin": ("财新", "https://rsshub.app/caixin/latest"),
    "rss_ce": ("经济日报", "https://rsshub.app/ce/latest"),
    "rss_huxiu": ("虎嗅", "https://rsshub.app/huxiu/article"),
    "rss_sspai": ("少数派", "https://rsshub.app/sspai/matrix"),
    "rss_guancha": ("观察者网", "https://rsshub.app/guanchazhe/index"),
}


class RssFetcher(BaseFetcher):
    name = "rss"
    category = "fast"
    timeout = 20

    def __init__(self, feed_key: str, session=None):
        super().__init__(session)
        self.feed_key = feed_key
        self.source_name, self.feed_url = RSS_FEEDS[feed_key]

    def fetch(self, limit: int = 15) -> List[Dict]:
        try:
            feed = feedparser.parse(self.feed_url)
            results = []
            for entry in feed.entries[:limit]:
                title = entry.get("title", "")
                url = entry.get("link", "")
                summary = entry.get("summary", "")
                published = entry.get("published", "") or entry.get("updated", "")
                results.append(make_article(
                    title[:200], summary[:8000], url, self.source_name,
                    normalize_pubdate(published)
                ))
            return results
        except Exception as e:
            logger.warning("%s RSS抓取失败: %s", self.source_name, e)
            return []


# 为每个 RSS 源注册独立 Fetcher
def _make_rss_fetcher(key):
    @register(key)
    class _RssFetcher(RssFetcher):
        __name__ = f"{key.replace('rss_', '').title()}Fetcher"
        def __init__(self, session=None):
            super().__init__(key, session)
    return _RssFetcher

for _key in RSS_FEEDS:
    _make_rss_fetcher(_key)
