"""正文提取管线（4 策略：选择器 → 文本密度 → Readability → 回退）"""
import logging
import re
from collections import OrderedDict
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .config import DOMAIN_SELECTORS, GENERIC_SELECTORS, SKIP_PATTERN

logger = logging.getLogger(__name__)

try:
    import lxml  # noqa: F401
    _BS_PARSER = 'lxml'
except ImportError:
    _BS_PARSER = 'html.parser'

_CACHE = OrderedDict()
_CACHE_MAX = 512
_CACHE_LOCK = __import__('threading').Lock()


def _cache_get(url: str) -> Optional[str]:
    with _CACHE_LOCK:
        if url in _CACHE:
            _CACHE.move_to_end(url)
            return _CACHE[url]
    return None


def _cache_set(url: str, content: str):
    with _CACHE_LOCK:
        _CACHE[url] = content
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def _clean_content(tag) -> str:
    for s in tag.find_all(['script', 'style', 'iframe', 'noscript']):
        s.decompose()

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

        if is_container and (SKIP_PATTERN.search(cls) or SKIP_PATTERN.search(el_id)):
            inner = el.get_text(strip=True)
            if len(inner) < 50 or sum(1 for c in inner if '一' <= c <= '鿿') / max(len(inner), 1) < 0.05:
                to_decompose.append(el)
                return

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
            except Exception as e:
                logger.debug("遍历子元素失败: %s", e)

    _walk(tag)

    for el in to_decompose:
        el.decompose()

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


def extract_article(url: str, session: requests.Session, timeout: int = 10) -> str:
    """从文章页提取正文（带 LRU 缓存，4层策略）"""
    cached = _cache_get(url)
    if cached is not None:
        return cached

    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        encoding = resp.encoding or resp.apparent_encoding or 'utf-8'
        html = resp.content.decode(encoding, errors='replace')
        soup = BeautifulSoup(html, _BS_PARSER)

        for s in soup.find_all(['script', 'style', 'iframe', 'noscript']):
            s.decompose()

        # 策略1: 域名针对性选择器
        domain = ''
        try:
            domain = urlparse(url).netloc
        except Exception as e:
            logger.debug("解析域名失败: %s", e)

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

        # 策略2: 文本密度检测
        best_container = None
        best_score = 0
        for tag in soup.find_all(['div', 'section', 'article', 'main', 'td', 'ul']):
            text = tag.get_text(strip=True)
            if len(text) < 100:
                continue
            cn_count = sum(1 for c in text if '一' <= c <= '鿿')
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

        # 策略3: 隐藏 input/textarea
        for inp in soup.find_all(['input', 'textarea'], attrs={'value': True}):
            val = inp.get('value', '')
            if len(val) < 200:
                continue
            if '<' in val and ('>' in val or '/>' in val):
                inner_soup = BeautifulSoup(val, _BS_PARSER)
                text = inner_soup.get_text(strip=True)
            else:
                text = val.strip()
            if text and sum(1 for c in text if '一' <= c <= '鿿') > 30:
                text = text[:15000]
                _cache_set(url, text)
                return text

        for ta in soup.find_all('textarea'):
            text = ta.string or ta.get_text(strip=True)
            if len(text) < 200:
                continue
            if sum(1 for c in text if '一' <= c <= '鿿') > 30:
                text = text[:15000]
                _cache_set(url, text)
                return text

    except Exception as e:
        logger.warning("提取文章正文失败 %s: %s", url, e)

    _cache_set(url, '')
    return ''
