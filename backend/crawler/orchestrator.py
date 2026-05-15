"""爬虫编排器：并行调度所有数据源"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict

from .circuit import CircuitBreaker
from .utils import sort_by_quality

logger = logging.getLogger(__name__)


class CrawlOrchestrator:
    """编排并行抓取：快源 + 慢源统一通过 ThreadPoolExecutor 调度"""

    def __init__(self, crawler=None):
        self._crawler = crawler
        self._circuit_breaker = CircuitBreaker()

    def run(self, sources: List[tuple] = None, limit: int = 50) -> List[Dict]:
        """并行抓取所有源，汇总、去重、质量排序

        Args:
            sources: [(name, fetch_func, is_fast), ...] 如不传则使用 crawler 的默认源列表
            limit: 最终返回的文章数上限
        """
        if self._crawler is None:
            logger.error("CrawlOrchestrator 未绑定 NewsCrawler 实例")
            return []

        crawler = self._crawler

        if sources is None:
            sources = [
                ('金十数据', crawler._fetch_jinshi, True),
                ('华尔街见闻', crawler._fetch_wallstreetcn, True),
                ('同花顺API', crawler._fetch_ths_api, True),
                ('财联社API', crawler._fetch_cls_api, True),
                ('雪球', crawler._fetch_xueqiu, True),
                ('东方财富公告', crawler._fetch_eastmoney_api, True),
                ('RSS(新华网)', crawler._fetch_rss_xinhua, True),
                ('RSS(财新)', crawler._fetch_rss_caixin, True),
                ('RSS(经济日报)', crawler._fetch_rss_ce, True),
                ('RSS(虎嗅)', crawler._fetch_rss_huxiu, True),
                ('RSS(少数派)', crawler._fetch_rss_sspai, True),
                ('RSS(观察者网)', crawler._fetch_rss_guancha, True),
                ('36氪', crawler._fetch_36kr, True),
                ('AKShare(东财)', crawler._fetch_akshare, True),
                ('东方财富网页', crawler._fetch_eastmoney_web, False),
                ('新浪财经', crawler._fetch_sina_finance, False),
                ('微信公众号', crawler._fetch_wechat_sogou, False),
                ('钛媒体', crawler._fetch_tmtpost, False),
                ('财新网', crawler._fetch_caixin, False),
                ('界面新闻', crawler._fetch_jiemian, False),
                ('网易财经', crawler._fetch_netease_finance, False),
                ('第一财经', crawler._fetch_yicai, False),
                ('同花顺', crawler._fetch_10jqka, False),
                ('百度财经API', crawler._fetch_baidu_finance, False),
            ]

        _t0 = time.time()
        logger.info("开始获取最新经济新闻，目标数量: %d", limit)

        all_news = []
        seen_urls = set()

        per_fast = max(limit // 4, 15)
        per_slow = max(limit // 6, 8)
        total_workers = min(len(sources), 12)

        with ThreadPoolExecutor(max_workers=total_workers) as executor:
            future_map = {}
            for name, func, is_fast in sources:
                per_src = per_fast if is_fast else per_slow
                future_map[executor.submit(
                    crawler._fetch_with_cb, name, func, per_src
                )] = (name, is_fast)

            completed = []
            try:
                for f in as_completed(future_map, timeout=180):
                    completed.append(f)
            except TimeoutError:
                logger.warning(
                    "爬取全局超时(180s)，%d/%d 源未完成",
                    len(future_map) - len(completed), len(future_map)
                )
                for f in future_map:
                    f.cancel()
                for f in future_map:
                    if f not in completed and (f.done() or f.cancelled()):
                        completed.append(f)

            for future in completed:
                name, is_fast = future_map.get(future, ('unknown', False))
                if name == 'unknown':
                    continue
                tag = '快速' if is_fast else '慢速'
                try:
                    news = future.result(timeout=0)
                    if news:
                        for item in news:
                            url = item.get('url', '')
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                all_news.append(item)
                        logger.info("  [%s] %s: %d 条，累计 %d 条", tag, name, len(news), len(all_news))
                except Exception as e:
                    logger.warning("  [%s] %s 失败: %s", tag, name, e)

        elapsed = time.time() - _t0
        if not all_news:
            logger.warning("所有真实数据源均无返回")
        else:
            all_news = sort_by_quality(all_news)

        logger.info(
            "成功获取 %d 条新闻（%d 不重复），耗时 %.0fs",
            len(all_news), len(seen_urls), elapsed
        )
        return all_news[:limit]
