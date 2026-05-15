"""API 类数据源"""
import logging
import time
from typing import List, Dict

from ..base import BaseFetcher
from ..utils import normalize_pubdate, classify, make_article
from .registry import register

logger = logging.getLogger(__name__)


@register("jinshi")
class JinshiFetcher(BaseFetcher):
    name = "jinshi"
    category = "fast"
    timeout = 15

    def fetch(self, limit: int = 15) -> List[Dict]:
        try:
            url = "https://www.jinshi.cn/get_news"
            resp = self.session.get(
                url, params={'size': str(min(limit * 2, 50))}, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get('data', [])
            if not items:
                return []

            results = []
            for item in items:
                title = item.get('title', '') or item.get('summary', '')
                content = item.get('content', '') or item.get('summary', '')
                article_url = item.get('link', '') or f"https://www.jinshi.cn/news/{item.get('id', '')}"
                if not title or not content or len(content) < 30:
                    continue
                results.append(make_article(
                    title[:200], content[:2000], article_url, '金十数据',
                    normalize_pubdate(item.get('time', ''))
                ))
                if len(results) >= limit:
                    break
            return results[:limit]
        except Exception as e:
            logger.warning("金十数据抓取失败: %s", e)
            return []


@register("wallstreetcn")
class WallStreetCNFetcher(BaseFetcher):
    name = "wallstreetcn"
    category = "fast"
    timeout = 15

    def fetch(self, limit: int = 15) -> List[Dict]:
        try:
            resp = self.session.get(
                'https://api-one-wscn.awtmt.com/apiv1/content/lives',
                params={'limit': str(min(limit * 2, 50)), 'channel': 'global-channel'},
                timeout=self.timeout,
            )
            data = resp.json()
            items = data.get('data', {}).get('items', [])
            if not items:
                return []

            results = []
            for item in items[:limit]:
                title = item.get('title', '') or (item.get('content', '') or '')[:50]
                content = item.get('content', '') or item.get('content_text', '')
                article_url = item.get('uri', '')
                if article_url and not article_url.startswith('http'):
                    article_url = 'https://wallstreetcn.com' + article_url
                results.append(make_article(
                    title[:200], content[:8000], article_url, '华尔街见闻',
                    normalize_pubdate(item.get('display_time', ''))
                ))
            return results
        except Exception as e:
            logger.warning("华尔街见闻抓取失败: %s", e)
            return []


@register("ths_api")
class ThsApiFetcher(BaseFetcher):
    name = "ths_api"
    category = "fast"
    timeout = 15

    def fetch(self, limit: int = 15) -> List[Dict]:
        try:
            url = "https://news.10jqka.com.cn/tapp/news/push/stock"
            params = {"page": 1, "tag": "", "track": "", "pagesize": min(limit * 2, 30)}
            headers = {"Referer": "https://news.10jqka.com.cn/"}
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("data", {}).get("list", [])[:limit]:
                title = item.get("title", "")
                article_url = item.get("url", "")
                summary = item.get("summary", "")
                results.append(make_article(
                    title[:200], summary[:8000], article_url, "同花顺",
                    normalize_pubdate(item.get("ctime", ""))
                ))
            return results
        except Exception as e:
            logger.warning("同花顺API抓取失败: %s", e)
            return []


@register("cls_api")
class ClsApiFetcher(BaseFetcher):
    name = "cls_api"
    category = "fast"
    timeout = 15

    def fetch(self, limit: int = 15) -> List[Dict]:
        try:
            resp = self.session.get(
                'https://www.cls.cn/v3/depth/home/assembled/1000',
                headers={'Referer': 'https://www.cls.cn/'},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get('data', {}).get('roll_data', [])[:limit]:
                title = item.get('title', '')
                article_url = item.get('url', '')
                content = item.get('content', '')
                if article_url and not article_url.startswith('http'):
                    article_url = 'https://www.cls.cn' + article_url
                results.append(make_article(
                    title[:200], content[:8000], article_url, '财联社',
                    normalize_pubdate(item.get('ctime', ''))
                ))
            return results
        except Exception as e:
            logger.warning("财联社API抓取失败: %s", e)
            return []
