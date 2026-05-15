"""Playwright 网页类数据源"""
import logging
from typing import List, Dict

from ..base import BaseFetcher
from .registry import register

logger = logging.getLogger(__name__)


@register("eastmoney_web")
class EastMoneyWebFetcher(BaseFetcher):
    name = "eastmoney_web"
    category = "slow"
    timeout = 30

    def fetch(self, limit: int = 8) -> List[Dict]:
        from ..news_crawler import NewsCrawler
        crawler = NewsCrawler()
        try:
            return crawler._fetch_web_source(
                'https://finance.eastmoney.com/',
                '东方财富',
                lambda href, text: '/a/' in href,
                limit,
            )
        finally:
            crawler.close()


@register("sina_finance")
class SinaFinanceFetcher(BaseFetcher):
    name = "sina_finance"
    category = "slow"
    timeout = 30

    def fetch(self, limit: int = 8) -> List[Dict]:
        from ..news_crawler import NewsCrawler
        crawler = NewsCrawler()
        try:
            return crawler._fetch_sina_finance(limit)
        finally:
            crawler.close()
