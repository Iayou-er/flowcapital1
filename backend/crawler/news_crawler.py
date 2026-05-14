"""
经济新闻爬虫
多数据源并行聚合: AKShare(东财) + 东方财富网页 + 新浪财经 + 金十数据 + 东方财富公告
+ 华尔街见闻 + 网易财经 + 第一财经 + 同花顺 + RSS源(新华网/财新/经济日报) + API源(同花顺/财联社/百度)
"""

import hashlib
import json
import time
import logging
import uuid
import re
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional, Callable
from urllib.parse import urlparse, urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import feedparser

logger = logging.getLogger(__name__)

# ── lxml 解析器检测 ──
try:
    import lxml  # noqa: F401
    _BS_PARSER = 'lxml'
except ImportError:
    _BS_PARSER = 'html.parser'
    logger.info("lxml 未安装，使用 html.parser（建议 pip install lxml 提升解析速度）")

# ── 模块级常量：预编译正则 ──

SKIP_PATTERN = re.compile(
    r'(nav|menu|sidebar|footer|comment|share|related|ad[_-]|banner|toolbar|'
    r'recommend|subscribe|login|register|breadcrumb|pager|page[_-]?nav|hot[_-]|rank[_-])', re.I
)

ARTICLE_CLASS_PATTERN = re.compile(
    r'^(article[-_]?body|article[-_]?content|news[-_]?content|post[-_]?content|'
    r'story[-_]?content|detail[-_]?content|content[-_]?detail|article-box)', re.I
)
ARTICLE_ID_PATTERN = re.compile(r'^(article|content|newsContent|detail|arcBody|storyBody)', re.I)
ARTICLE_CLASS_SIMPLE = re.compile(r'^(article|news-content|post-content|detail|story|content-wrap)', re.I)

# 域名 → 正文选择器
DOMAIN_SELECTORS = {
    'sina.com.cn': [
        {'name': 'div', 'id': 'artibody'},
        {'name': 'div', 'class': 'article'},
        {'name': 'div', 'class': 'main-article'},
    ],
    'eastmoney.com': [
        {'name': 'div', 'class': 'Body'},
        {'name': 'div', 'id': 'ContentBody'},
        {'name': 'div', 'class': 'newsContent'},
    ],
    '10jqka.com.cn': [
        {'name': 'div', 'class': 'txtContent'},
        {'name': 'div', 'class': 'detail-txt'},
        {'name': 'div', 'class': 'article-content'},
    ],
    'yicai.com': [
        {'name': 'div', 'class': 'article-detail'},
        {'name': 'div', 'class': 'm-detail-content'},
    ],
    'cls.cn': [
        {'name': 'div', 'class': 'detail-content'},
        {'name': 'div', 'class': 'article-detail'},
    ],
    'wallstreetcn.com': [
        {'name': 'div', 'class': 'article__content'},
        {'name': 'div', 'class': 'live-item-detail'},
    ],
    '163.com': [
        {'name': 'div', 'id': 'endText'},
        {'name': 'div', 'class': 'post_body'},
    ],
    'finance.sina.com.cn': [
        {'name': 'div', 'id': 'artibody'},
        {'name': 'div', 'class': 'article'},
    ],
    'caixin.com': [
        {'name': 'div', 'id': 'Main_Content_Val'},
        {'name': 'div', 'class': 'article-content'},
        {'name': 'div', 'class': 'textbox'},
    ],
    'jiemian.com': [
        {'name': 'div', 'class': 'article-content'},
        {'name': 'div', 'class': 'article-main'},
        {'name': 'div', 'class': 'main-content'},
    ],
    'tmtpost.com': [
        {'name': 'div', 'class': 'article-body'},
        {'name': 'div', 'class': 'post-content'},
        {'name': 'div', 'class': 'inner'},
    ],
    '36kr.com': [
        {'name': 'div', 'class': 'article-body'},
        {'name': 'div', 'class': 'common-width'},
    ],
}

# 通用正文选择器
GENERIC_SELECTORS = [
    {'name': 'div', 'class': ARTICLE_CLASS_PATTERN},
    {'name': 'article'},
    {'name': 'main'},
    {'name': 'div', 'id': ARTICLE_ID_PATTERN},
    {'name': 'div', 'class': ARTICLE_CLASS_SIMPLE},
]

# 预编译分类正则 — 一次编译，全局复用
CATEGORY_PATTERNS = {}
for _cat, _keywords in {
    '宏观经济': ['宏观', '经济', 'GDP', 'CPI', 'PPI', '央行', '统计局', '财政', '货币', '降息', '加息', '通胀', '通缩'],
    '股市动态': ['A股', '沪指', '深成指', '创业板', '沪深', '大盘', '涨停', '跌停', '板块', '牛市', '熊市', '指数'],
    '债券市场': ['债券', '国债', '信用债', '城投债', '企业债', '违约', '评级'],
    '外汇交易': ['汇率', '美元', '欧元', '日元', '人民币', '外汇', '美联储', 'Fed'],
    '期货市场': ['期货', '原油', '黄金', '铜', '铁矿石', '大豆', '农产品'],
    '公司财报': ['财报', '业绩', '利润', '营收', '净利润', '年报', '季报', '公告', '披露'],
    '政策法规': ['政策', '法规', '监管', '证监会', '国务院', '改革', '意见', '通知'],
    '行业分析': ['行业', '赛道', '产业链', '新能源', '半导体', '医药', '消费', '科技'],
    '国际市场': ['美股', '港股', '纳指', '标普', '日经', '欧洲', '全球', '海外', '国际'],
    '投资策略': ['策略', '研报', '机构', '基金', '配置', '布局', '加仓', '减持', '估值'],
}.items():
    CATEGORY_PATTERNS[_cat] = re.compile('|'.join(re.escape(kw) for kw in _keywords), re.I)

# 文章提取缓存 — LRU + 上限 + 线程安全
_article_cache = OrderedDict()
_article_cache_lock = __import__('threading').Lock()
_ARTICLE_CACHE_MAX = 512


def _classify(title: str, content: str = '') -> str:
    """根据标题和内容分类（预编译正则，O(分类数)）"""
    text = (title + ' ' + content).lower()
    for cat, pattern in CATEGORY_PATTERNS.items():
        if pattern.search(text):
            return cat
    return '综合财经'


def _cache_get(url: str) -> Optional[str]:
    """从 LRU 缓存获取文章（线程安全）"""
    with _article_cache_lock:
        if url in _article_cache:
            _article_cache.move_to_end(url)
            return _article_cache[url]
    return None


def _cache_set(url: str, content: str):
    """写入 LRU 缓存（线程安全）"""
    with _article_cache_lock:
        if url in _article_cache:
            _article_cache.move_to_end(url)
        _article_cache[url] = content
        while len(_article_cache) > _ARTICLE_CACHE_MAX:
            _article_cache.popitem(last=False)


def _clean_content(tag) -> str:
    """从 HTML 容器提取干净的正文（单次遍历，避免多次 find_all）"""
    # 1. 移除脚本/样式
    for s in tag.find_all(['script', 'style', 'iframe', 'noscript']):
        s.decompose()

    # 2. 单次递归遍历：收集段落 + 标记广告区
    to_decompose = []
    paragraphs = []

    def _walk(el, depth=0):
        if el.name in ['script', 'style', 'iframe', 'noscript']:
            return
        is_block = el.name in {'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
                               'blockquote', 'pre', 'td', 'div', 'span'}
        is_container = el.name in {'div', 'section', 'aside', 'ul', 'ol', 'table'}
        cls = ' '.join(el.get('class', [])) if el.get('class') else ''
        el_id = el.get('id', '') or ''

        # 检查是否为广告/导航区域
        if is_container and (SKIP_PATTERN.search(cls) or SKIP_PATTERN.search(el_id)):
            inner = el.get_text(strip=True)
            if len(inner) < 50 or sum(1 for c in inner if '\u4e00' <= c <= '\u9fff') / max(len(inner), 1) < 0.05:
                to_decompose.append(el)
                return

        # 收集块级文字
        if is_block:
            text = el.get_text(strip=True)
            if len(text) >= 5:
                parent_cls = ' '.join(el.parent.get('class', [])) if el.parent else ''
                if not SKIP_PATTERN.search(parent_cls):
                    link_len = sum(len(a.get_text(strip=True)) for a in el.find_all('a'))
                    if not (link_len > len(text) * 0.5 and len(text) < 200):
                        paragraphs.append(text)

        for child in el.children:
            try:
                if hasattr(child, 'name'):
                    _walk(child, depth + 1)
            except Exception:
                pass

    _walk(tag)

    # 3. 删除广告区域
    for el in to_decompose:
        el.decompose()

    # 4. 组装结果
    if paragraphs:
        deduped = []
        for p in paragraphs:
            if not deduped or p != deduped[-1]:
                deduped.append(p)
        text = '\n\n'.join(deduped)
    else:
        text = tag.get_text(strip=True)

    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()[:15000]


def _extract_article(url: str, session: requests.Session, timeout: int = 10) -> str:
    """从文章页提取正文（带 LRU 缓存，4层策略）"""
    cached = _cache_get(url)
    if cached is not None:
        return cached

    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        # 自动检测编码：Content-Type → apparent → utf-8
        encoding = resp.encoding or resp.apparent_encoding or 'utf-8'
        html = resp.content.decode(encoding, errors='replace')
        soup = BeautifulSoup(html, _BS_PARSER)

        for s in soup.find_all(['script', 'style', 'iframe', 'noscript']):
            s.decompose()

        # ── 策略1: 域名针对性选择器 ──
        domain = ''
        try:
            domain = urlparse(url).netloc
        except Exception:
            pass

        selectors = []
        for site_domain, site_selectors in DOMAIN_SELECTORS.items():
            if site_domain in domain:
                selectors.extend(site_selectors)
                break
        selectors.extend(GENERIC_SELECTORS)

        for selector in selectors:
            tag = soup.find(**selector)
            if tag:
                result = _clean_content(tag)
                if len(result) > 100:
                    _cache_set(url, result)
                    return result

        # ── 策略3: 内容密度检测 ──
        best_container = None
        best_score = 0
        for tag in soup.find_all(['div', 'section', 'article', 'main', 'td', 'ul']):
            text = tag.get_text(strip=True)
            if len(text) < 100:
                continue
            cn_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
            link_text_len = sum(len(a.get_text(strip=True)) for a in tag.find_all('a'))
            link_ratio = link_text_len / max(len(text), 1)
            score = cn_count - link_text_len * 2 - int(link_ratio * cn_count)
            if score > best_score:
                best_score = score
                best_container = tag

        if best_container and best_score > 50:
            result = _clean_content(best_container)
            if len(result) > 100:
                _cache_set(url, result)
                return result

        # ── 策略4: 隐藏 input/textarea ──
        for inp in soup.find_all(['input', 'textarea'], attrs={'value': True}):
            val = inp.get('value', '')
            if len(val) < 200:
                continue
            if '<' in val and ('>' in val or '/>' in val):
                inner_soup = BeautifulSoup(val, _BS_PARSER)
                text = inner_soup.get_text(strip=True)
            else:
                text = val.strip()
            if text and sum(1 for c in text if '\u4e00' <= c <= '\u9fff') > 30:
                text = text[:15000]
                _cache_set(url, text)
                return text

        for ta in soup.find_all('textarea'):
            text = ta.string or ta.get_text(strip=True)
            if len(text) < 200:
                continue
            if sum(1 for c in text if '\u4e00' <= c <= '\u9fff') > 30:
                text = text[:15000]
                _cache_set(url, text)
                return text

    except Exception as e:
        logger.warning(f"提取文章正文失败 {url}: {e}")

    _cache_set(url, '')
    return ''


# ── 断路器 ──

def _normalize_pubdate(raw: str) -> str:
    """将各种日期字符串统一为 ISO-8601 格式"""
    if not raw:
        return datetime.now().isoformat()
    # 已经是 ISO 格式
    if raw.startswith('202') and ('T' in raw or raw[4] == '-'):
        return raw[:25]
    # Unix 时间戳（数字串）
    if raw.isdigit():
        try:
            return datetime.fromtimestamp(int(raw)).isoformat()
        except Exception:
            pass
    # RFC-2822 / RSS pubDate 格式
    try:
        return parsedate_to_datetime(raw).isoformat()
    except Exception:
        pass
    # 最后尝试标准 strptime
    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d %H:%M:%S', '%m/%d/%Y %H:%M:%S']:
        try:
            return datetime.strptime(raw[:19], fmt).isoformat()
        except Exception:
            continue
    return datetime.now().isoformat()


# ── Playwright 共享浏览器（线程安全懒初始化）──

_pw_lock = __import__('threading').Lock()
_pw_browser = None
_pw_playwright = None
_pw_context = None


def _ensure_playwright_context():
    """线程安全获取共享的 Playwright browser context"""
    global _pw_browser, _pw_playwright, _pw_context
    if _pw_browser is not None and _pw_context is not None:
        try:
            if _pw_browser.is_connected():
                return _pw_context
        except Exception:
            pass

    with _pw_lock:
        if _pw_browser is not None and _pw_context is not None:
            try:
                if _pw_browser.is_connected():
                    return _pw_context
            except Exception:
                pass

        # 清理旧实例
        try:
            if _pw_context:
                _pw_context.close()
        except Exception:
            pass
        try:
            if _pw_browser:
                _pw_browser.close()
        except Exception:
            pass
        try:
            if _pw_playwright:
                _pw_playwright.stop()
        except Exception:
            pass

        # 创建新实例
        from playwright.sync_api import sync_playwright
        _pw_playwright = sync_playwright().start()
        _pw_browser = _pw_playwright.chromium.launch(headless=True)
        _pw_context = _pw_browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       'Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
        )
        return _pw_context


def _reset_playwright():
    """重置 Playwright 实例（崩溃时调用）"""
    global _pw_browser, _pw_playwright, _pw_context
    with _pw_lock:
        try:
            if _pw_context:
                _pw_context.close()
        except Exception:
            pass
        try:
            if _pw_browser:
                _pw_browser.close()
        except Exception:
            pass
        try:
            if _pw_playwright:
                _pw_playwright.stop()
        except Exception:
            pass
        _pw_context = None
        _pw_browser = None
        _pw_playwright = None


class CircuitBreaker:
    """数据源断路器：连续失败 N 次后暂退避"""

    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 300):
        self.threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._failures: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}

    def is_open(self, name: str) -> bool:
        failures = self._failures.get(name, 0)
        if failures >= self.threshold:
            elapsed = time.time() - self._last_failure_time.get(name, 0)
            if elapsed < self.reset_timeout:
                return True
            self._failures[name] = 0
        return False

    def record_failure(self, name: str):
        self._failures[name] = self._failures.get(name, 0) + 1
        self._last_failure_time[name] = time.time()

    def record_success(self, name: str):
        self._failures[name] = 0


class NewsCrawler:
    """经济新闻爬虫"""

    def __init__(self, delay: float = 0.3):
        self.delay = delay
        self._use_akshare = False
        self._circuit_breaker = CircuitBreaker()

        try:
            import akshare as ak  # noqa: F401
            self._use_akshare = True
            logger.info("AKShare 已安装，将使用 AKShare 作为数据源")
        except ImportError:
            logger.info("AKShare 未安装，将使用东方财富网页爬虫")

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'FlowCapital/1.0 (Economic News Aggregator; '
                'https://flowcapital.cn/robots.txt; '
                'compliance@flowcapital.cn) '
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })

        # HTTP 重试适配器（降池+SSL容错）
        retry_strategy = Retry(
            total=3,
            connect=2,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=['GET', 'HEAD'],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=8, pool_maxsize=8)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    # ── 公开接口 ──

    def crawl_latest_news(self, limit: int = 50) -> List[Dict]:
        """获取最新经济新闻（全源并行 + 更高单源配额）"""
        _t0 = time.time()
        logger.info(f"开始获取最新经济新闻，目标数量: {limit}")

        all_news = []
        seen_urls = set()

        # 全源统一并行（不再分快慢先后）
        all_sources = [
            # 快速 API/RSS 源
            ('金十数据', self._fetch_jinshi, True),
            ('华尔街见闻', self._fetch_wallstreetcn, True),
            ('同花顺API', self._fetch_ths_api, True),
            ('财联社API', self._fetch_cls_api, True),
            ('雪球', self._fetch_xueqiu, True),
            ('东方财富公告', self._fetch_eastmoney_api, True),
            ('RSS(新华网)', self._fetch_rss_xinhua, True),
            ('RSS(财新)', self._fetch_rss_caixin, True),
            ('RSS(经济日报)', self._fetch_rss_ce, True),
            ('RSS(虎嗅)', self._fetch_rss_huxiu, True),
            ('RSS(少数派)', self._fetch_rss_sspai, True),
            ('RSS(观察者网)', self._fetch_rss_guancha, True),
            ('36氪', self._fetch_36kr, True),
            ('AKShare(东财)', self._fetch_akshare, True),
            # 慢速网页/Playwright 源
            ('东方财富网页', self._fetch_eastmoney_web, False),
            ('新浪财经', self._fetch_sina_finance, False),
            ('微信公众号', self._fetch_wechat_sogou, False),
            ('钛媒体', self._fetch_tmtpost, False),
            ('财新网', self._fetch_caixin, False),
            ('界面新闻', self._fetch_jiemian, False),
            ('网易财经', self._fetch_netease_finance, False),
            ('第一财经', self._fetch_yicai, False),
            ('同花顺', self._fetch_10jqka, False),
            ('百度财经API', self._fetch_baidu_finance, False),
        ]

        # 单源配额：快速源每条更多，慢速源适当控制避免超时
        per_fast = max(limit // 4, 15)
        per_slow = max(limit // 6, 8)
        total_workers = min(len(all_sources), 12)

        with ThreadPoolExecutor(max_workers=total_workers) as executor:
            future_map = {}
            for name, func, is_fast in all_sources:
                per_src = per_fast if is_fast else per_slow
                future_map[executor.submit(self._fetch_with_cb, name, func, per_src)] = (name, is_fast)

            # as_completed 带全局超时（180s），防止线程死锁导致调度器永久挂起
            completed = []
            try:
                for f in as_completed(future_map, timeout=180):
                    completed.append(f)
            except TimeoutError:
                logger.warning(f"爬取全局超时(180s)，{len(future_map)-len(completed)}/{len(future_map)} 源未完成")
                for f in future_map:
                    f.cancel()
                # 追加已完成的（done+cancelled 但有结果的）
                for f in future_map:
                    if f not in completed and (f.done() or f.cancelled()):
                        completed.append(f)

            for future in completed:
                name, is_fast = future_map.get(future, ('unknown', False))
                if name == 'unknown':
                    continue
                tag = '快速' if is_fast else '慢速'
                try:
                    news = future.result(timeout=0)  # 已完成的不需要等待
                    if news:
                        for item in news:
                            url = item.get('url', '')
                            if url and url not in seen_urls:
                                seen_urls.add(url)
                                all_news.append(item)
                        logger.info(f"  [{tag}] {name}: {len(news)} 条，累计 {len(all_news)} 条")
                except Exception as e:
                    logger.warning(f"  [{tag}] {name} 失败: {e}")

        elapsed = time.time() - _t0
        if not all_news:
            logger.warning("所有真实数据源均无返回")
        else:
            all_news = self._sort_by_quality(all_news)

        logger.info(f"成功获取 {len(all_news)} 条新闻（{len(seen_urls)} 不重复），耗时 {elapsed:.0f}s")
        return all_news[:limit]

    def crawl_news_by_keyword(self, keyword: str, limit: int = 50) -> List[Dict]:
        news = self.crawl_latest_news(limit * 2)
        kw_lower = keyword.lower()
        filtered = [
            n for n in news
            if kw_lower in n.get('title', '').lower()
            or kw_lower in n.get('content', '').lower()
            or kw_lower in n.get('summary', '').lower()
        ]
        return filtered[:limit] if filtered else news[:limit]

    def crawl_news_by_category(self, category: str, limit: int = 50) -> List[Dict]:
        news = self.crawl_latest_news(limit * 2)
        filtered = [
            n for n in news
            if category in n.get('category', '')
            or category in str(n.get('tags', []))
            or category in n.get('title', '')
        ]
        return filtered[:limit] if filtered else news[:limit]

    def crawl_stock_news(self, stock_code: str, limit: int = 30) -> List[Dict]:
        logger.info(f"获取股票新闻: {stock_code}")

        if self._use_akshare:
            try:
                import akshare as ak
                df = ak.stock_news_em(symbol=stock_code)
                if df is not None and not df.empty:
                    results = []
                    for _, row in df.head(limit).iterrows():
                        results.append(self._normalize_row(row, stock_code))
                    logger.info(f"AKShare 返回 {len(results)} 条 {stock_code} 新闻")
                    return results
            except Exception as e:
                logger.warning(f"AKShare 获取 {stock_code} 新闻失败: {e}")

        all_news = self.crawl_latest_news(limit * 3)
        filtered = [
            n for n in all_news
            if stock_code in n.get('title', '')
            or stock_code in n.get('content', '')
            or stock_code in str(n.get('tags', []))
        ]
        return filtered[:limit] if filtered else all_news[:limit]

    def crawl_news_today(self, limit: int = 50) -> List[Dict]:
        return self.crawl_latest_news(limit)

    def crawl_news_by_time_range(self, start_time: datetime, end_time: datetime, limit: int = 50) -> List[Dict]:
        news = self.crawl_latest_news(limit * 3)
        filtered = []
        for n in news:
            try:
                pub = n.get('published_at', '')
                if 'T' in pub:
                    dt = datetime.fromisoformat(pub.replace('Z', '+00:00')).replace(tzinfo=None)
                else:
                    dt = datetime.strptime(pub[:19], '%Y-%m-%d %H:%M:%S')
                if start_time <= dt <= end_time:
                    filtered.append(n)
            except Exception:
                pass
            if len(filtered) >= limit:
                break
        return filtered

    def get_available_categories(self) -> List[str]:
        return list(CATEGORY_PATTERNS.keys())

    # ── 断路器包装 ──

    def _fetch_with_cb(self, name: str, fetch_func: Callable, limit: int) -> List[Dict]:
        """带断路器保护的 fetch"""
        if self._circuit_breaker.is_open(name):
            logger.info(f"  [断路器] {name} 已熔断，跳过本次")
            return []
        try:
            result = fetch_func(limit)
            if result:
                self._circuit_breaker.record_success(name)
            else:
                self._circuit_breaker.record_failure(name)
            return result
        except Exception:
            self._circuit_breaker.record_failure(name)
            raise

    # ── 通用网页爬取模板（替代 6 个慢速源重复代码） ──

    def _fetch_web_source(
        self, url: str, source_name: str,
        link_filter: Callable[[str, str], bool],
        limit: int,
        encoding: str = 'utf-8',
        extra_headers: Dict = None,
    ) -> List[Dict]:
        """通用网页爬取：获取列表页 → 过滤链接 → 并行提取正文"""
        try:
            headers = {'Referer': url}
            if extra_headers:
                headers.update(extra_headers)
            resp = self.session.get(url, timeout=15, headers=headers)
            resp.raise_for_status()
            html = resp.content.decode(encoding, errors='replace')
            soup = BeautifulSoup(html, _BS_PARSER)

            articles = []
            seen = set()
            for a in soup.find_all('a', href=True):
                text = a.get_text(strip=True)
                href = a['href']
                if not text or len(text) < 5 or href in seen:
                    continue
                if not link_filter(href, text):
                    continue
                seen.add(href)
                if href.startswith('http'):
                    full_url = href
                else:
                    full_url = urljoin(url, href)
                articles.append((text, full_url))
                if len(articles) >= limit:
                    break

            if not articles:
                return []

            return self._extract_articles_parallel(articles, source_name, limit)

        except requests.exceptions.RequestException as e:
            logger.error(f"{source_name} 请求失败: {e}")
        except Exception as e:
            logger.error(f"{source_name} 解析失败: {e}")
        return []

    def _extract_articles_parallel(self, articles: List[tuple], source_name: str, limit: int) -> List[Dict]:
        """并行提取多篇文章正文"""
        results = []
        with ThreadPoolExecutor(max_workers=min(len(articles), 4)) as executor:
            future_to_info = {
                executor.submit(_extract_article, url, self.session): (title, url)
                for title, url in articles
            }
            # as_completed 带超时，防止单篇文章挂死
            try:
                completed = as_completed(future_to_info, timeout=60)
            except TimeoutError:
                completed = as_completed(future_to_info, timeout=1)

            for future in completed:
                title, article_url = future_to_info[future]
                try:
                    content = future.result(timeout=15)
                    if not content or len(content) < 30:
                        continue
                    cn_count = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
                    en_count = sum(1 for c in content if c.isascii() and c.isalpha())
                    digit_count = sum(1 for c in content if c.isdigit())
                    valid_ratio = (cn_count + en_count + digit_count) / max(len(content), 1)
                    if valid_ratio < 0.7:
                        continue
                    results.append(self._make_article(title[:200], content[:8000], article_url, source_name))
                except Exception:
                    pass
                if len(results) >= limit:
                    break
        return results[:limit]

    # ── 质量排序 ──

    @staticmethod
    def _sort_by_quality(articles: List[Dict]) -> List[Dict]:
        """按内容长度 + 发布时间加权排序"""
        def score(a: Dict) -> float:
            content_len = len(a.get('content', '') or '')
            title_len = len(a.get('title', '') or '')
            # 正文 > 50 字加分，标题 10-100 字加分
            s = min(content_len / 100, 10) + min(title_len / 10, 5)
            # 来源可信度加权
            high_quality_sources = {'金十数据', '华尔街见闻', '财联社', '第一财经', '东方财富'}
            if a.get('source', '') in high_quality_sources:
                s += 2
            return s

        return sorted(articles, key=score, reverse=True)

    # ── AKShare ──

    def _fetch_akshare(self, limit: int) -> List[Dict]:
        try:
            import akshare as ak
            hot_stocks = ['000001', '600519', '000858', '601318', '002594']
            results = []
            seen_urls = set()

            for symbol in hot_stocks:
                try:
                    df = ak.stock_news_em(symbol=symbol)
                    if df is None or df.empty:
                        continue
                    for _, row in df.iterrows():
                        url = str(row.get('新闻链接', ''))
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)
                        content = str(row.get('新闻内容', ''))
                        title = str(row.get('新闻标题', ''))
                        if not title and not content:
                            continue
                        a = self._make_article(
                            (title or content[:100])[:200], content, url,
                            str(row.get('文章来源', '东方财富')),
                            _normalize_pubdate(str(row.get('发布时间', '')))
                        )
                        a['tags'] = [symbol, '财经']
                        results.append(a)
                        if len(results) >= limit:
                            break
                except Exception as e:
                    logger.warning(f"AKShare {symbol}: {e}")
                if len(results) >= limit:
                    break

            time.sleep(self.delay)
            return results[:limit]
        except Exception as e:
            logger.warning(f"AKShare 获取新闻失败: {e}")
        return []

    # ── 东方财富网页 ──

    def _fetch_eastmoney_web(self, limit: int) -> List[Dict]:
        return self._fetch_web_source(
            'https://finance.eastmoney.com/',
            '东方财富',
            lambda href, text: '/a/' in href,
            limit,
        )

    # ── 新浪财经网页 ──

    def _fetch_sina_finance(self, limit: int) -> List[Dict]:
        result = self._fetch_web_source(
            'https://finance.sina.com.cn/',
            '新浪财经',
            lambda href, text: any(d in href for d in ['finance.sina.com', 'sina.com.cn/roll']),
            limit,
        )
        if not result:
            return self._fetch_sina_roll(limit)
        return result

    def _fetch_sina_roll(self, limit: int) -> List[Dict]:
        try:
            url = 'https://feed.mix.sina.com.cn/api/roll/get'
            params = {
                'pageid': 153, 'lid': 2516, 'k': '',
                'num': str(min(limit * 2, 50)), 'page': 1,
            }
            resp = self.session.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get('result', {}).get('data', [])

            if not items:
                return []

            articles = []
            for item in items:
                title = item.get('title', '')
                article_url = item.get('url', '')
                if not title or not article_url:
                    continue
                articles.append((title, article_url))
                if len(articles) >= limit:
                    break

            # 并行提取正文（复用公共方法）
            ctime = items[0].get('ctime', '') if items else ''
            try:
                pub_time = datetime.fromtimestamp(int(ctime)).isoformat() if ctime else datetime.now().isoformat()
            except Exception:
                pub_time = datetime.now().isoformat()

            return self._extract_articles_parallel(articles, '新浪财经', limit)
        except Exception as e:
            logger.warning(f"新浪滚动新闻失败: {e}")
        return []

    # ── 金十数据 ──

    def _fetch_jinshi(self, limit: int) -> List[Dict]:
        try:
            url = 'https://www.jinshi.cn/get_news'
            resp = self.session.get(url, params={'size': str(min(limit * 2, 50))}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get('data', [])

            if not items:
                return []

            results = []
            for item in items:
                title = re.sub(r'<[^>]+>', '', item.get('title', '') or item.get('summary', ''))
                content = re.sub(r'<[^>]+>', '', item.get('content', '') or item.get('summary', ''))
                article_url = item.get('link', '') or f"https://www.jinshi.cn/news/{item.get('id', '')}"

                if not title or not content or len(content) < 30:
                    continue
                cn_count = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
                en_count = sum(1 for c in content if c.isascii() and c.isalpha())
                digit_count = sum(1 for c in content if c.isdigit())
                if (cn_count + en_count + digit_count) / max(len(content), 1) < 0.7:
                    continue

                results.append(self._make_article(
                    title[:200], content[:2000], article_url, '金十数据',
                    _normalize_pubdate(item.get('time', ''))
                ))
                if len(results) >= limit:
                    break
            return results[:limit]
        except Exception as e:
            logger.warning(f"金十数据获取失败: {e}")
        return []

    # ── 东方财富公告API ──

    def _fetch_eastmoney_api(self, limit: int) -> List[Dict]:
        try:
            url = 'https://np-anotice-stock.eastmoney.com/api/security/ann'
            params = {
                'page_size': str(limit), 'page_index': '1',
                'ann_type': 'A', 'f_node': '0', 's_node': '0',
            }
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            news_list = data.get('data', {}).get('list', [])
            if not news_list:
                return []

            results = []
            for item in news_list[:limit]:
                title = item.get('title_ch', '') or item.get('title', '')
                if not title:
                    continue
                stock_codes = ','.join(
                    c.get('stock_code', '') for c in item.get('codes', []) if c.get('stock_code')
                )
                a = self._make_article(
                    title[:200], '', f"https://data.eastmoney.com/noticedetail/{item.get('art_code', '')}.html",
                    '东方财富公告', _normalize_pubdate(item.get('display_time', ''))
                )
                a['id'] = item.get('art_code', str(uuid.uuid4())[:12])
                a['category'] = '公司财报'
                a['tags'] = [stock_codes, '公告'] if stock_codes else ['公告']
                results.append(a)

            time.sleep(self.delay)
            return results
        except Exception as e:
            logger.error(f"东方财富公告API失败: {e}")
        return []

    # ── 华尔街见闻 ──

    def _fetch_wallstreetcn(self, limit: int) -> List[Dict]:
        try:
            resp = self.session.get(
                'https://api-one-wscn.awtmt.com/apiv1/content/lives',
                params={'limit': str(min(limit * 2, 50)), 'channel': 'global-channel'},
                timeout=10,
            )
            data = resp.json()
            items = data.get('data', {}).get('items', [])

            results = []
            for item in items:
                title = item.get('title', '') or (item.get('content', '') or '')[:50]
                content = re.sub(r'<[^>]+>', '', item.get('content', '') or '')
                if not content or len(content) < 20:
                    continue
                article_url = f"https://wallstreetcn.com/live/{item.get('id', '')}"
                pub = item.get('created_at', '')
                try:
                    pub_time = datetime.fromtimestamp(int(pub)).isoformat() if pub else datetime.now().isoformat()
                except Exception:
                    pub_time = datetime.now().isoformat()

                results.append(self._make_article(title[:200], content[:8000], article_url, '华尔街见闻', pub_time))
                if len(results) >= limit:
                    break
            return results[:limit]
        except Exception as e:
            logger.warning(f"华尔街见闻获取失败: {e}")
        return []

    # ── 网易财经 ──

    def _fetch_netease_finance(self, limit: int) -> List[Dict]:
        return self._fetch_web_source(
            'https://money.163.com/',
            '网易财经',
            lambda href, text: ('money.163.com' in href and 'http' not in href) or '/article/' in href,
            limit,
        )

    # ── 第一财经 ──

    def _fetch_yicai(self, limit: int) -> List[Dict]:
        return self._fetch_web_source(
            'https://www.yicai.com/news/',
            '第一财经',
            lambda href, text: 'yicai.com' in href and ('news' in href or 'story' in href),
            limit,
        )

    # ── 同花顺网页 ──

    def _fetch_10jqka(self, limit: int) -> List[Dict]:
        return self._fetch_web_source(
            'https://news.10jqka.com.cn/',
            '同花顺',
            lambda href, text: '10jqka.com.cn' in href,
            limit,
            encoding='gbk',
        )

    # ── RSS 源 ──

    def _fetch_rss_xinhua(self, limit: int) -> List[Dict]:
        return self._fetch_rss_feed('https://www.chinanews.com/rss/finance.xml', '中新网财经', limit)

    def _fetch_rss_caixin(self, limit: int) -> List[Dict]:
        return self._fetch_rss_feed('https://rsshub.rssforever.com/cls/telegraph', '财联社RSS', limit)

    def _fetch_rss_ce(self, limit: int) -> List[Dict]:
        return self._fetch_rss_feed('https://rsshub.rssforever.com/wallstreetcn/live', '华尔街见闻RSS', limit)

    def _fetch_rss_feed(self, url: str, source_name: str, limit: int) -> List[Dict]:
        """通用RSS源抓取（对短摘要自动补充全文提取）"""
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                return []
            results = []
            short_entries = []  # 需要补充全文提取的短摘要条目
            for entry in feed.entries:
                title = getattr(entry, 'title', '')
                link = getattr(entry, 'link', '')
                summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
                summary = re.sub(r'<[^>]+>', '', summary or '')
                # 优先使用 feedparser 解析过的 struct_time
                pub = ''
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    try:
                        pub = datetime(*entry.published_parsed[:6]).isoformat()
                    except Exception:
                        pass
                if not pub and hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    try:
                        pub = datetime(*entry.updated_parsed[:6]).isoformat()
                    except Exception:
                        pass
                if not pub:
                    raw_pub = ''
                    if hasattr(entry, 'published'):
                        raw_pub = entry.published
                    elif hasattr(entry, 'updated'):
                        raw_pub = entry.updated
                    pub = _normalize_pubdate(raw_pub)

                if not title and not summary:
                    continue

                content = summary[:10000]
                # 标记需要补充提取的短摘要（批量并行处理）
                if link and len(content) < 500:
                    short_entries.append((entry, title, content[:300], link, pub))
                    continue

                if not content:
                    continue

                a = self._make_article(
                    (title or content[:100])[:200], content,
                    link or '', source_name, pub,
                )
                a['author'] = getattr(entry, 'author', '')
                results.append(a)
                if len(results) >= limit:
                    break

            # 批量并行补充短摘要的全文提取
            if short_entries and len(results) < limit:
                remaining = limit - len(results)
                batch = [(t, l) for _, t, _, l, _ in short_entries[:remaining]]
                extracted = self._extract_articles_parallel(batch, source_name, remaining)
                for i, (_, _, _, _, pub) in enumerate(short_entries[:len(extracted)]):
                    extracted[i]['published_at'] = pub
                results.extend(extracted[:remaining])

            return results[:limit]
        except Exception as e:
            logger.warning(f"RSS源 {source_name} 获取失败: {e}")
        return []

    # ── API 数据源 ──

    def _fetch_ths_api(self, limit: int) -> List[Dict]:
        try:
            resp = self.session.get(
                'https://news.10jqka.com.cn/tapp/news/push/stock',
                params={'page': '1', 'tag': '', 'track': 'website', 'pagesize': str(min(limit * 2, 50))},
                timeout=10,
                headers={'Referer': 'https://news.10jqka.com.cn/'},
            )
            data = resp.json()
            items = data.get('data', {}).get('list', [])

            results = []
            for item in items:
                title = item.get('title', '')
                content = re.sub(r'<[^>]+>', '', item.get('content', '') or '')
                if not title or len(content) < 20:
                    continue
                article_url = f"https://news.10jqka.com.cn/detail/pid/{item.get('pid', '')}"
                pub = item.get('ctime', '')
                results.append(self._make_article(title[:200], content[:8000], article_url, '同花顺API', pub))
                if len(results) >= limit:
                    break
            return results[:limit]
        except Exception as e:
            logger.warning(f"同花顺API获取失败: {e}")
        return []

    def _fetch_cls_api(self, limit: int) -> List[Dict]:
        try:
            resp = self.session.get(
                'https://www.cls.cn/telegraph',
                timeout=10,
                headers={'Referer': 'https://www.cls.cn/', 'Accept': 'text/html,application/xhtml+xml'},
            )
            html = resp.content.decode('utf-8', errors='replace')
            soup = BeautifulSoup(html, _BS_PARSER)

            results = []
            seen = set()
            for div in soup.find_all('div', class_=re.compile(r'telegraph-content|item-content|feed-content', re.I)):
                text = div.get_text(strip=True)
                if not text or len(text) < 20 or text in seen:
                    continue
                seen.add(text)
                link_tag = div.find('a', href=True)
                article_url = link_tag['href'] if link_tag else 'https://www.cls.cn/telegraph'
                results.append(self._make_article(text[:200], text[:2000], article_url, '财联社'))
                if len(results) >= limit:
                    break

            if results:
                return results[:limit]

            # 备用: 从脚本JSON提取
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string and 'telegraph' in script.string:
                    match = re.search(r'(\[.*?\])', script.string)
                    if match:
                        try:
                            items = json.loads(match.group(1))
                            for item in items:
                                content = re.sub(r'<[^>]+>', '', item.get('content', '') or '')
                                if len(content) < 20:
                                    continue
                                article_url = f"https://www.cls.cn/detail/{item.get('id', '')}"
                                results.append(self._make_article(content[:200], content[:8000], article_url, '财联社'))
                                if len(results) >= limit:
                                    break
                        except json.JSONDecodeError:
                            pass
                if results:
                    break
            return results[:limit]
        except Exception as e:
            logger.warning(f"财联社获取失败: {e}")
        return []

    def _fetch_baidu_finance(self, limit: int) -> List[Dict]:
        return self._fetch_web_source(
            'https://gushitong.baidu.com/home/',
            '百度财经',
            lambda href, text: bool(
                re.search(r'baidu\.com/(article|news|detail|\d+)', href) or
                re.match(r'^/\d+', href)
            ),
            limit,
            extra_headers={'Referer': 'https://www.baidu.com/'},
        )

    # ── 新增 RSS 源 ──

    def _fetch_rss_sspai(self, limit: int) -> List[Dict]:
        """少数派 RSS（RSSHub 镜像）"""
        return self._fetch_rss_feed(
            'https://rsshub.rssforever.com/sspai/series',
            '少数派',
            limit,
        )

    def _fetch_rss_guancha(self, limit: int) -> List[Dict]:
        """观察者网 RSS（RSSHub 镜像）"""
        return self._fetch_rss_feed(
            'https://rsshub.rssforever.com/guancha',
            '观察者网',
            limit,
        )


    # ── 搜狗微信搜索 ──

    def _fetch_wechat_sogou(self, limit: int) -> List[Dict]:
        """搜狗微信搜索（多关键词+翻页+浏览器复用）

        5 个财经关键词 × 2 页，每页保留搜索页点击所有链接后再关闭。
        Playwright 浏览器在爬虫生命周期内复用，省启动时间。
        """
        try:
            from playwright.sync_api import sync_playwright

            actual_limit = min(limit, 15)

            # 线程安全的懒初始化（搜狗和雪球共享浏览器）
            ctx = _ensure_playwright_context()
            if ctx is None:
                return []

            results = []
            seen_urls = set()
            queries = ['宏观经济', '政策+股市', '财经+行业', '投资+策略', '金融+改革']

            for query in queries:
                if len(results) >= actual_limit:
                    break
                for page_num in [1, 2]:
                    if len(results) >= actual_limit:
                        break
                    search_page = None
                    try:
                        search_page = ctx.new_page()
                        search_page.goto(
                            f'https://weixin.sogou.com/weixin?type=2&query={query}&page={page_num}',
                            wait_until='networkidle', timeout=25000,
                        )
                        search_page.wait_for_timeout(4000)

                        titles = search_page.evaluate('''() => {
                            const result = [];
                            document.querySelectorAll('a').forEach(a => {
                                if (a.href && a.href.includes('sogou.com/link')) {
                                    const text = a.textContent.trim();
                                    if (text.length > 10) result.push(text.slice(0, 80));
                                }
                            });
                            return result;
                        }''')

                        # 在同一个搜索页上逐一点击链接
                        for title in titles:
                            if len(results) >= actual_limit:
                                break
                            title_key = title[:30]
                            if title_key in seen_urls:
                                continue
                            seen_urls.add(title_key)

                            try:
                                wx_page = None
                                links = search_page.locator('a').filter(has_text=title[:15])
                                if links.count() == 0:
                                    continue

                                with search_page.context.expect_page(timeout=15000) as ni:
                                    links.first.click(force=True)
                                wx_page = ni.value
                                wx_page.wait_for_load_state('domcontentloaded', timeout=15000)
                                wx_page.wait_for_timeout(2000)

                                final_url = wx_page.url
                                if 'mp.weixin.qq.com' not in final_url:
                                    wx_page.close()
                                    continue

                                content = wx_page.evaluate('() => document.body.innerText')
                                wx_page.close()

                                cn = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
                                if content and len(content) >= 100 and cn > 50:
                                    results.append(self._make_article(
                                        title[:200], content[:8000],
                                        final_url, '微信公众号',
                                    ))
                            except Exception:
                                pass
                    except Exception as e:
                        logger.warning(f"搜狗 q={query} p={page_num}: {e}")
                    finally:
                        if search_page:
                            try:
                                search_page.close()
                            except Exception:
                                pass

            return results[:actual_limit]
        except Exception as e:
            logger.warning(f"搜狗微信获取失败: {e}")
            _reset_playwright()
        return []

    # ── 虎嗅 RSS ──

    def _fetch_rss_huxiu(self, limit: int) -> List[Dict]:
        """虎嗅 RSS（RSSHub 镜像）"""
        return self._fetch_rss_feed(
            'https://rsshub.rssforever.com/huxiu/article',
            '虎嗅',
            limit,
        )

    # ── 36氪 ──

    def _fetch_36kr(self, limit: int) -> List[Dict]:
        """36氪快讯 API"""
        try:
            resp = self.session.get(
                'https://www.36kr.com/api/newsflash',
                params={'per_page': str(min(limit, 30))},
                timeout=10,
                headers={'Referer': 'https://www.36kr.com/'},
            )
            data = resp.json()
            items = data.get('data', {}).get('items', [])

            results = []
            for item in items:
                title = item.get('title', '')
                content = item.get('description', '') or item.get('content', '')
                content = re.sub(r'<[^>]+>', '', content)[:5000]
                article_url = item.get('news_url', '') or f"https://www.36kr.com/newsflashes/{item.get('id', '')}"

                if not content or len(content) < 20:
                    continue

                pub = _normalize_pubdate(str(item.get('published_at', ''))[:19])

                results.append(self._make_article(
                    title[:200], content, article_url, '36氪', pub,
                ))
                if len(results) >= limit:
                    break
            return results[:limit]
        except Exception as e:
            logger.warning(f"36氪获取失败: {e}")
        return []

    # ── 钛媒体 ──

    def _fetch_tmtpost(self, limit: int) -> List[Dict]:
        """钛媒体网页爬取"""
        return self._fetch_web_source(
            'https://www.tmtpost.com/',
            '钛媒体',
            lambda href, text: bool(
                re.search(r'tmtpost\.com/\d+\.html|^/(video|nictation)/\d+\.html', href)
            ),
            limit,
        )

    @staticmethod
    def _make_article(title: str, content: str, url: str, source: str,
                      published_at: str = '') -> Dict:
        """构造标准新闻条目（分类只计算一次）"""
        cat = _classify(title, content)
        return {
            'id': hashlib.md5((url or title).encode()).hexdigest()[:16] if url else str(uuid.uuid4())[:12],
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

    # ── 雪球 ──

    def _fetch_xueqiu(self, limit: int) -> List[Dict]:
        """雪球热门讨论（Playwright 解 WAF → v4 API）"""
        try:
            cookies = self._get_xueqiu_cookies()
            if not cookies:
                return []

            xq_session = requests.Session()
            xq_session.headers.update(self.session.headers)
            for c in cookies:
                xq_session.cookies.set(
                    c['name'], c['value'],
                    domain=c.get('domain', ''),
                    path=c.get('path', '/'),
                )

            # category: -1=全部, 6=财经, 10=宏观
            categories = ['6', '10', '-1']
            results = []
            seen = set()

            for cat in categories:
                if len(results) >= limit:
                    break
                resp = xq_session.get(
                    'https://xueqiu.com/v4/statuses/public_timeline_by_category.json',
                    params={
                        'since_id': '-1', 'max_id': '-1',
                        'count': str(min(limit, 15)), 'category': cat,
                    },
                    timeout=10,
                    headers={'Referer': 'https://xueqiu.com/', 'Accept': 'application/json'},
                )
                data = resp.json()
                items = data.get('list', [])

                for item in items:
                    # data 字段是 JSON 字符串
                    raw = item.get('data', '')
                    if isinstance(raw, str):
                        try:
                            detail = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            continue
                    else:
                        detail = raw

                    content = detail.get('text', '') or detail.get('description', '')
                    content = re.sub(r'<[^>]+>', '', content)[:8000]
                    aid = str(detail.get('id', '') or item.get('id', ''))

                    if not content or len(content) < 30 or aid in seen:
                        continue
                    seen.add(aid)

                    title = detail.get('title', '') or content[:100]
                    target = detail.get('target', '')
                    article_url = target if target.startswith('http') else f'https://xueqiu.com/{aid}/'

                    pub = _normalize_pubdate(str(detail.get('created_at', ''))[:10])

                    results.append(self._make_article(
                        title[:200], content, article_url, '雪球', pub,
                    ))
                    if len(results) >= limit:
                        break
            return results[:limit]
        except Exception as e:
            logger.warning(f"雪球获取失败: {e}")
        return []

    @staticmethod
    def _get_xueqiu_cookies() -> list:
        """用 Playwright 打开雪球首页获取 cookie（共享浏览器）"""
        try:
            ctx = _ensure_playwright_context()
            if ctx is None:
                return []

            xq_ctx = ctx.browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
            )
            page = xq_ctx.new_page()
            page.goto('https://xueqiu.com/', wait_until='domcontentloaded', timeout=15000)
            page.wait_for_timeout(3000)
            cookies = xq_ctx.cookies()
            xq_ctx.close()
            return cookies
        except Exception as e:
            logger.warning(f"Playwright 获取雪球 cookie 失败: {e}")
        return []

    # ── 财新网 ──

    def _fetch_caixin(self, limit: int) -> List[Dict]:
        """财新网网页爬取"""
        return self._fetch_web_source(
            'https://www.caixin.com/',
            '财新网',
            lambda href, text: 'caixin.com' in href and ('/detail/' in href or 'article' in href or 'news' in href),
            limit,
        )

    # ── 界面新闻 ──

    def _fetch_jiemian(self, limit: int) -> List[Dict]:
        """界面新闻财经频道"""
        return self._fetch_web_source(
            'https://www.jiemian.com/lists/4.html',
            '界面新闻',
            lambda href, text: 'jiemian.com/article' in href or 'jiemian.com/news' in href,
            limit,
        )

    # ── 内部工具 ──

    @staticmethod
    def _normalize_row(row, stock_code: str) -> Dict:
        """将 AKShare DataFrame 行转为标准格式"""
        content = str(row.get('新闻内容', ''))
        title = str(row.get('新闻标题', ''))
        url = str(row.get('新闻链接', ''))
        return {
            'id': hashlib.md5(url.encode()).hexdigest()[:16] if url else str(uuid.uuid4())[:12],
            'title': (title or content[:100])[:200],
            'content': content,
            'summary': (title or content[:200])[:300],
            'url': url,
            'source': str(row.get('文章来源', '东方财富')),
            'category': _classify(title, content),
            'published_at': _normalize_pubdate(str(row.get('发布时间', ''))),
            'author': '',
            'read_count': 0,
            'comment_count': 0,
            'tags': [stock_code, '个股'],
            'created_at': datetime.now().isoformat(),
        }
