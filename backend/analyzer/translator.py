"""
多语言翻译模块
支持百度翻译 API / 腾讯翻译 API / 免费降级方案
"""

import os
import json
import time
import hashlib
import logging
import requests
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

_TRANSLATE_CACHE_MAX = 500

# 支持的语言
SUPPORTED_LANGS = {
    'zh': '中文',
    'en': '英文',
    'ja': '日文',
    'ko': '韩文',
    'fr': '法文',
    'de': '德文',
}


class Translator:
    """多语言翻译器，支持多 API 源和降级"""

    def __init__(self):
        self._api_type = os.environ.get('TRANSLATE_API_TYPE', 'baidu').lower()
        self._app_id = os.environ.get('TRANSLATE_APP_ID', '')
        self._app_key = os.environ.get('TRANSLATE_APP_KEY', '')
        self._available = bool(self._app_id and self._app_key)

        # 百度翻译配置
        self._baidu_url = 'https://fanyi-api.baidu.com/api/trans/vip/translate'

        # 腾讯翻译配置
        self._tencent_url = 'https://tmt.tencentcloudapi.com/'

        # 内存缓存 (LRU)
        self._cache: Dict[str, str] = {}
        self._cache_order: list = []
        self._cache_max = _TRANSLATE_CACHE_MAX

        # 腾讯翻译 auth 未实现，配置为 tencent 时自动降级
        if self._api_type == 'tencent':
            logger.warning("腾讯翻译 API auth 未实现，将使用免费降级方案")
            self._available = False

        if self._available:
            logger.info(f"翻译 API 已配置: {self._api_type}")
        else:
            logger.warning("未配置翻译 API 密钥，将使用免费降级方案")

    def translate(self, text: str, from_lang: str = 'auto', to_lang: str = 'en') -> str:
        """
        翻译文本

        Args:
            text: 待翻译文本
            from_lang: 源语言，'auto' 自动检测
            to_lang: 目标语言

        Returns:
            翻译后的文本
        """
        if not text or not text.strip():
            return ''

        # 检查缓存
        cache_key = hashlib.md5(f'{text}:{from_lang}:{to_lang}'.encode()).hexdigest()[:16]
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 同语言直接返回
        if from_lang == to_lang:
            return text

        try:
            if self._available:
                if self._api_type == 'tencent':
                    result = self._translate_tencent(text, from_lang, to_lang)
                else:
                    result = self._translate_baidu(text, from_lang, to_lang)
            else:
                result = self._translate_fallback(text, from_lang, to_lang)

            if result:
                if cache_key not in self._cache and len(self._cache) >= self._cache_max:
                    oldest = self._cache_order.pop(0)
                    self._cache.pop(oldest, None)
                self._cache[cache_key] = result
                if cache_key not in self._cache_order:
                    self._cache_order.append(cache_key)
            return result
        except Exception as e:
            logger.warning(f"翻译失败: {e}")
            return self._translate_fallback(text, from_lang, to_lang)

    def translate_batch(self, texts: List[str], from_lang: str = 'auto', to_lang: str = 'en') -> List[str]:
        """批量翻译"""
        return [self.translate(t, from_lang, to_lang) for t in texts]

    def _translate_baidu(self, text: str, from_lang: str, to_lang: str) -> str:
        """百度翻译"""
        salt = str(int(time.time()))
        sign = hashlib.md5((self._app_id + text[:2000] + salt + self._app_key).encode()).hexdigest()

        resp = requests.post(
            self._baidu_url,
            data={
                'q': text[:2000],
                'from': from_lang,
                'to': to_lang,
                'appid': self._app_id,
                'salt': salt,
                'sign': sign,
            },
            timeout=10,
        )
        result = resp.json()
        if 'error_code' in result:
            raise RuntimeError(f"百度翻译错误: {result['error_msg']}")

        translated = '\n'.join(item['dst'] for item in result['trans_result'])
        return translated

    def _translate_tencent(self, text: str, from_lang: str, to_lang: str) -> str:
        """腾讯翻译（签名简化版，生产环境建议用官方 SDK）"""
        payload = {
            'Source': from_lang if from_lang != 'auto' else 'auto',
            'Target': to_lang,
            'ProjectId': 0,
            'SourceText': text[:2000],
        }
        headers = {
            'Content-Type': 'application/json',
            'Authorization': self._tencent_auth(payload),
            'X-TC-Action': 'TextTranslate',
            'X-TC-Version': '2018-03-21',
            'X-TC-Region': 'ap-guangzhou',
        }

        resp = requests.post(self._tencent_url, json=payload, headers=headers, timeout=10)
        data = resp.json()
        response = data.get('Response', {})
        if 'Error' in response:
            raise RuntimeError(f"腾讯翻译错误: {response['Error']}")

        return response.get('TargetText', '')

    def _tencent_auth(self, payload: dict) -> str:
        """腾讯 API V3 签名 — 需安装 tencentcloud-sdk-python"""
        raise NotImplementedError(
            "腾讯翻译 API 未配置。可通过环境变量 TRANSLATE_API_TYPE 切换为 baidu/google/mymemory，"
            "或安装 tencentcloud-sdk-python 并配置 SecretId/SecretKey"
        )

    @staticmethod
    def _translate_fallback(text: str, from_lang: str, to_lang: str) -> str:
        """
        免费降级方案：使用 MyMemory 公共翻译 API
        有限额，仅用于开发测试
        """
        try:
            resp = requests.get(
                'https://api.mymemory.translated.net/get',
                params={
                    'q': text[:500],
                    'langpair': f'{from_lang}|{to_lang}',
                },
                timeout=10,
            )
            data = resp.json()
            translated = data.get('responseData', {}).get('translatedText', '')
            if translated:
                return translated
        except Exception as e:
            logger.debug(f"免费翻译降级失败: {e}")

        # 最终降级：截断原文 + 标注
        return f'[Translate required] {text[:200]}'

    def detect_language(self, text: str) -> str:
        """简单语言检测"""
        if not text:
            return 'unknown'

        # 简单规则检测
        cn_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        en_chars = sum(1 for c in text if c.isascii() and c.isalpha())
        total = max(len(text) - text.count(' '), 1)

        cn_ratio = cn_chars / total
        en_ratio = en_chars / total

        if cn_ratio > 0.3:
            return 'zh'
        elif en_ratio > 0.5:
            return 'en'
        else:
            return 'unknown'

    def auto_translate(self, text: str, target_lang: str = 'en') -> Dict:
        """
        自动翻译：先检测源语言再翻译

        Returns:
            包含翻译结果和语言检测信息的字典
        """
        detected = self.detect_language(text)
        if detected == target_lang:
            return {
                'original': text,
                'translated': text,
                'source_lang': detected,
                'target_lang': target_lang,
                'was_translated': False,
            }

        translated = self.translate(text, from_lang='auto', to_lang=target_lang)
        return {
            'original': text,
            'translated': translated,
            'source_lang': detected,
            'target_lang': target_lang,
            'was_translated': True,
        }
