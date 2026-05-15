"""数据源注册表"""
from typing import Dict, Type, List

from ..base import BaseFetcher

FETCHER_REGISTRY: Dict[str, Type[BaseFetcher]] = {}

FAST_SOURCES: List[str] = [
    "jinshi", "wallstreetcn", "ths_api", "cls_api", "xueqiu", "eastmoney_api",
    "rss_xinhua", "rss_caixin", "rss_ce", "rss_huxiu", "rss_sspai", "rss_guancha",
    "36kr", "akshare",
]
SLOW_SOURCES: List[str] = [
    "eastmoney_web", "sina_finance", "wechat_sogou", "tmtpost",
    "caixin", "jiemian", "netease_finance", "yicai", "10jqka", "baidu_finance",
]
ALL_SOURCES: List[str] = FAST_SOURCES + SLOW_SOURCES


def register(name: str):
    """注册数据源的装饰器"""
    def decorator(cls):
        FETCHER_REGISTRY[name] = cls
        cls.name = name
        return cls
    return decorator
