"""Playwright 共享浏览器管理（同步 API，线程安全单例）"""
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_browser = None
_playwright = None
_context = None


def get_page():
    """线程安全获取共享的 Playwright browser context"""
    global _browser, _playwright, _context
    if _browser is not None and _context is not None:
        try:
            if _browser.is_connected():
                return _context
        except Exception as e:
            logger.debug("Playwright连接检查失败: %s", e)

    with _lock:
        if _browser is not None and _context is not None:
            try:
                if _browser.is_connected():
                    return _context
            except Exception as e:
                logger.debug("Playwright连接检查(锁定)失败: %s", e)

        # 清理旧实例
        _cleanup()

        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        _context = _browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                       'Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1920, 'height': 1080},
            locale='zh-CN',
        )
        return _context


def reset_browser():
    """重置 Playwright 实例（崩溃时调用）"""
    global _browser, _playwright, _context
    with _lock:
        _cleanup()
        _context = None
        _browser = None
        _playwright = None


def _cleanup():
    global _context, _browser, _playwright
    for obj, name in [(_context, "context"), (_browser, "browser"), (_playwright, "playwright")]:
        if obj:
            try:
                close_fn = getattr(obj, 'close', None) or getattr(obj, 'stop', None)
                if close_fn:
                    close_fn()
            except Exception as e:
                logger.debug("关闭旧Playwright %s 失败: %s", name, e)
