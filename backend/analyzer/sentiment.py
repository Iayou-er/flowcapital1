"""
情感分析模块
优先使用 SnowNLP 进行中文情感分析，支持 LLM 增强模式
叠加财经领域词典修正金融语境误判
"""

import logging
import json
from typing import List, Dict

logger = logging.getLogger(__name__)

# 尝试导入 SnowNLP
try:
    from snownlp import SnowNLP
    HAS_SNOWNLP = True
except ImportError:
    HAS_SNOWNLP = False
    logger.warning("SnowNLP 未安装，将使用基础词典情感分析。pip install snownlp")

from backend.analyzer.finance_sentiment_dict import (
    POSITIVE_WORDS, NEGATIVE_WORDS, MODIFIERS, NEGATION_PREFIXES
)


class SentimentAnalyzer:
    """
    中文情感分析器
    优先级：SnowNLP > 词典分析
    """

    # SnowNLP 单例，避免重复加载模型
    _snownlp_cache = {}

    def _get_snownlp(self, text: str) -> 'SnowNLP':
        """获取或创建 SnowNLP 实例"""
        import hashlib
        key = hashlib.md5(text.encode('utf-8', errors='replace')).hexdigest()
        if key not in self._snownlp_cache:
            self._snownlp_cache[key] = SnowNLP(text)
            if len(self._snownlp_cache) > 1000:
                # LRU: 删除最早的一半
                oldest = list(self._snownlp_cache.keys())[:500]
                for k in oldest:
                    del self._snownlp_cache[k]
        return self._snownlp_cache[key]

    def analyze_sentiment(self, text: str) -> Dict:
        """
        分析文本情感

        Args:
            text: 待分析的文本

        Returns:
            包含情感分析结果的字典:
                - sentiment_score: 情感分数 (-1.0 ~ 1.0)
                - sentiment_label: 情感标签 (positive/negative/neutral)
                - positive_words: 正面关键词列表
                - negative_words: 负面关键词列表
                - confidence: 置信度 (0.0 ~ 1.0)
                - finance_hits: 财经词典命中词列表
        """
        if not text or not text.strip():
            return self._empty_result()

        if HAS_SNOWNLP:
            result = self._snownlp_analysis(text)
        else:
            result = self._dict_analysis(text)

        return self._apply_finance_dict(text, result)

    def _snownlp_analysis(self, text: str) -> Dict:
        """使用 SnowNLP 进行情感分析"""
        try:
            # 截取前 2000 字符避免过长处理
            truncated = text[:2000] if len(text) > 2000 else text
            # 复用 SnowNLP 实例
            s = self._get_snownlp(truncated)
            raw_score = s.sentiments
            normalized_score = round(raw_score * 2 - 1, 4)

            # 情感标签
            if normalized_score > 0.15:
                label = 'positive'
            elif normalized_score < -0.15:
                label = 'negative'
            else:
                label = 'neutral'

            # 置信度：越靠近两端越确定
            confidence = round(abs(normalized_score), 4)

            # 从文本中提取情感关键词（简化版）
            positive_words, negative_words = self._extract_sentiment_keywords(truncated)

            return {
                'sentiment_score': normalized_score,
                'sentiment_label': label,
                'positive_words': positive_words,
                'negative_words': negative_words,
                'confidence': confidence
            }
        except Exception as e:
            logger.error(f"SnowNLP 分析失败，降级到词典分析: {e}")
            return self._dict_analysis(text)

    def _extract_sentiment_keywords(self, text: str) -> tuple:
        """从文本中提取情感相关关键词"""
        import jieba
        words = [w for w in jieba.cut(text) if len(w) > 1]

        positive_set = {
            '利好', '增长', '上升', '改善', '盈利', '上涨', '突破',
            '积极', '乐观', '提振', '推动', '促进', '复苏', '回暖',
            '强势', '扩张', '强劲', '繁荣', '发展', '提升', '增强',
            '利好消息', '利好政策', '超预期', '创新高', '新高',
            '反弹', '企稳', '翻红', '领涨',
        }

        negative_set = {
            '下跌', '下降', '衰退', '恶化', '疲软', '萎缩',
            '下滑', '负面', '利空', '忧虑', '悲观', '危机', '风险',
            '暴跌', '亏损', '裁员', '违约', '爆雷', '退市',
            '不及预期', '大幅下跌', '利空消息', '跳水', '跌停',
        }

        positive = list(set(w for w in words if w in positive_set))
        negative = list(set(w for w in words if w in negative_set))
        return positive[:10], negative[:10]

    def _dict_analysis(self, text: str) -> Dict:
        """基础词典情感分析（SnowNLP 不可用时的降级方案）"""
        import jieba
        import math

        # 去重后的情感词典
        positive_words = {
            '利好', '增长', '上升', '改善', '向好', '上涨', '盈利',
            '强劲', '繁荣', '发展', '突破', '积极', '乐观', '强势',
            '提振', '推动', '促进', '刺激', '提升', '增强', '扩张',
            '复苏', '回暖', '超预期', '创新高'
        }

        negative_words = {
            '下跌', '下降', '亏损', '衰退', '危机', '恶化', '疲软',
            '萎缩', '下滑', '负面', '利空', '忧虑', '悲观', '风险',
            '暴跌', '裁员', '违约', '爆雷'
        }

        # 程度副词
        degree_words = {
            '非常': 2.0, '特别': 2.0, '极其': 3.0, '超级': 3.0,
            '十分': 2.0, '很': 1.5, '比较': 1.2, '稍微': 0.8,
            '有点': 1.1, '略微': 1.1, '大幅': 1.8, '大幅下跌': 1.8
        }

        # 否定词
        negation_words = {'不', '无', '非', '未', '否', '别', '莫', '勿', '休'}

        words = list(jieba.cut(text))
        score = 0.0
        pos_found, neg_found = [], []

        i = 0
        while i < len(words):
            word = words[i]

            # 检查前一个词是否为否定词或程度词
            prev_word = words[i - 1] if i > 0 else ''
            is_negation = prev_word in negation_words
            degree = degree_words.get(prev_word, 1.0) if prev_word else 1.0

            if word in positive_words:
                word_score = degree * (1 if not is_negation else -1)
                score += word_score
                pos_found.append(word)
            elif word in negative_words:
                word_score = -degree * (1 if not is_negation else -1)
                score += word_score
                neg_found.append(word)

            i += 1

        # 标准化
        normalized_score = math.tanh(score / 5.0) if abs(score) < 10 else (1.0 if score > 0 else -1.0)
        normalized_score = round(normalized_score, 4)

        if normalized_score > 0.1:
            label = 'positive'
        elif normalized_score < -0.1:
            label = 'negative'
        else:
            label = 'neutral'

        confidence = min(1.0, abs(normalized_score))

        return {
            'sentiment_score': normalized_score,
            'sentiment_label': label,
            'positive_words': list(set(pos_found)),
            'negative_words': list(set(neg_found)),
            'confidence': round(confidence, 4)
        }

    @staticmethod
    def _match_sentiment_words(text: str, word_list: list) -> list:
        """
        按词长降序匹配，已匹配区间加锁防止子串冲突。
        返回 [(word, weight, position), ...]
        """
        hits = []
        occupied = set()

        for word, weight in sorted(word_list, key=lambda x: -len(x[0])):
            start = 0
            while True:
                pos = text.find(word, start)
                if pos == -1:
                    break
                positions = set(range(pos, pos + len(word)))
                if not positions & occupied:
                    occupied |= positions
                    hits.append((word, weight, pos))
                start = pos + 1

        return hits

    def _apply_finance_dict(self, text: str, base_result: dict) -> dict:
        """在 SnowNLP/词典 结果之上叠加财经词典"""
        finance_score = 0.0
        hit_words = []

        all_hits = []
        all_hits.extend(self._match_sentiment_words(text, POSITIVE_WORDS))
        all_hits.extend(self._match_sentiment_words(text, NEGATIVE_WORDS))

        for word, weight, pos in all_hits:
            # 查找前文否定前缀（10 字符窗口内）
            prefix = text[max(0, pos - 10):pos]
            negated = any(neg in prefix for neg in NEGATION_PREFIXES)
            # 同时检查是否有否定词紧邻（如"不构成利好"）
            if not negated and pos > 0 and text[pos - 1] == '不':
                negated = True

            # 查找前文程度修饰词（20 字符窗口内）
            modifier = 1.0
            prefix_20 = text[max(0, pos - 20):pos]
            for mod_word, mod_weight in sorted(MODIFIERS.items(), key=lambda x: -len(x[0])):
                if mod_word in prefix_20:
                    modifier = mod_weight
                    break

            effective = (-weight if negated else weight) * modifier
            finance_score += effective
            neg_label = '(否定反转)' if negated else ''
            hit_words.append(f'{effective:+.1f}:{word}{neg_label}(x{modifier})')

        # 融合权重: SnowNLP 0.6 + 财经词典 0.4
        base_score = base_result.get('sentiment_score', 0.0)
        clipped_finance = max(-1.0, min(1.0, finance_score))
        final_score = round(base_score * 0.6 + clipped_finance * 0.4, 4)
        final_score = max(-1.0, min(1.0, final_score))

        if final_score > 0.15:
            label = 'positive'
        elif final_score < -0.15:
            label = 'negative'
        else:
            label = 'neutral'

        return {
            **base_result,
            'sentiment_score': final_score,
            'sentiment_label': label,
            'finance_hits': hit_words,
        }

    def _empty_result(self) -> Dict:
        """返回空情感分析结果"""
        return {
            'sentiment_score': 0.0,
            'sentiment_label': 'neutral',
            'positive_words': [],
            'negative_words': [],
            'confidence': 0.0,
            'finance_hits': [],
        }

    def analyze_multiple_texts(self, texts: List[str]) -> List[Dict]:
        """批量分析多个文本的情感"""
        return [self.analyze_sentiment(t) for t in texts if t]

    def get_sentiment_summary(self, texts: List[str]) -> Dict:
        """获取文本列表的整体情感摘要"""
        if not texts:
            return {
                'total_texts': 0,
                'positive_count': 0,
                'negative_count': 0,
                'neutral_count': 0,
                'average_sentiment': 0.0,
                'sentiment_distribution': {}
            }

        results = self.analyze_multiple_texts(texts)
        if not results:
            return self._empty_summary()

        positive_count = sum(1 for r in results if r['sentiment_label'] == 'positive')
        negative_count = sum(1 for r in results if r['sentiment_label'] == 'negative')
        neutral_count = sum(1 for r in results if r['sentiment_label'] == 'neutral')

        total = len(results)
        average_sentiment = round(sum(r['sentiment_score'] for r in results) / total, 4)

        return {
            'total_texts': total,
            'positive_count': positive_count,
            'negative_count': negative_count,
            'neutral_count': neutral_count,
            'average_sentiment': average_sentiment,
            'sentiment_distribution': {
                'positive': positive_count,
                'negative': negative_count,
                'neutral': neutral_count
            }
        }

    def _empty_summary(self) -> Dict:
        return {
            'total_texts': 0,
            'positive_count': 0,
            'negative_count': 0,
            'neutral_count': 0,
            'average_sentiment': 0.0,
            'sentiment_distribution': {}
        }
