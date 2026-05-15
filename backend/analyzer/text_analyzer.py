"""
文本分析模块
提供文本处理和分析功能
"""

import re
import logging
from typing import List, Dict, Set, Optional
from collections import Counter
from .sentiment import SentimentAnalyzer

logger = logging.getLogger(__name__)

# 尝试导入 SnowNLP
try:
    from snownlp import SnowNLP
    HAS_SNOWNLP = True
except ImportError:
    HAS_SNOWNLP = False
    logger.warning("SnowNLP 未安装，自动摘要功能将使用降级方案。pip install snownlp")


class TextAnalyzer:
    """
    文本分析器
    综合性的文本分析工具
    """

    def __init__(self):
        """
        初始化文本分析器
        """
        self.sentiment_analyzer = SentimentAnalyzer()

    def extract_keywords(self, text: str, top_k: int = 10) -> List[str]:
        """
        提取文本中的关键词（jieba 中文分词 + 词频统计）

        Args:
            text: 待分析的文本
            top_k: 返回关键词数量

        Returns:
            关键词列表
        """
        if not text:
            return []

        import jieba

        # jieba 中文分词
        words = jieba.lcut(text)

        # 过滤掉短词、纯标点、纯数字和常见停用词
        stop_words = {
            '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都',
            '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会',
            '着', '没有', '看', '好', '自己', '这', '那', '他', '她', '它', '们',
            '与', '为', '但', '而', '及', '被', '把', '从', '对', '以', '将',
            '向', '或', '其', '可', '所', '如', '等', '还', '更', '能', '已',
        }

        # 统计词频
        word_counts = Counter(
            w for w in words
            if len(w) > 1
            and w not in stop_words
            and not w.isdigit()
            and not all(c in '，。！？、；：""''（）【】《》…—·\t\r\n ' for c in w)
        )

        return [word for word, _count in word_counts.most_common(top_k)]

    def generate_summary(self, text: str, max_sentences: int = 3) -> str:
        """
        生成文本摘要

        Args:
            text: 待摘要文本
            max_sentences: 摘要句子数量（默认 3）

        Returns:
            摘要文本
        """
        if not text or not text.strip():
            return ''

        # 文本过短直接返回
        if len(text) < 50:
            return text.strip()

        if HAS_SNOWNLP:
            try:
                # SnowNLP 基于 TextRank 算法提取关键句
                s = SnowNLP(text)
                sentences = s.summary(max_sentences)
                if sentences:
                    return '。'.join(sentences).replace('\n', '')
            except Exception as e:
                logger.warning(f"SnowNLP 摘要失败，使用降级方案: {e}")

        # 降级方案：取前 3 个句号分隔的句子
        parts = text.replace('\n', ' ').split('。')
        return '。'.join([p.strip() for p in parts[:max_sentences] if p.strip()]) + '。'

    def analyze_text(self, text: str) -> Dict:
        """
        综合分析文本

        Args:
            text: 待分析的文本

        Returns:
            综合分析结果
        """
        if not text:
            return {
                'word_count': 0,
                'char_count': 0,
                'sentiment': {},
                'keywords': [],
                'summary': ''
            }

        # 基础统计
        word_count = len(text.split())
        char_count = len(text)

        # 情感分析
        sentiment = self.sentiment_analyzer.analyze_sentiment(text)

        # 关键词提取
        keywords = self.extract_keywords(text, 10)

        # 自动生成摘要（替代原来的 text[:100] + '...'）
        summary = self.generate_summary(text, max_sentences=3)

        return {
            'word_count': word_count,
            'char_count': char_count,
            'sentiment': sentiment,
            'keywords': keywords,
            'summary': summary
        }

    def analyze_news_article(self, article: Dict) -> Dict:
        """
        分析新闻文章

        Args:
            article: 新闻文章字典

        Returns:
            包含分析结果的文章
        """
        content = article.get('content', '') or article.get('summary', '')

        if not content:
            return article

        # 文本分析
        analysis = self.analyze_text(content)

        # 合并分析结果到文章中
        result = article.copy()
        result['analysis'] = analysis
        result['sentiment_score'] = analysis['sentiment']['sentiment_score']
        result['sentiment_label'] = analysis['sentiment']['sentiment_label']
        result['auto_summary'] = analysis['summary']

        return result

    def analyze_multiple_articles(self, articles: List[Dict]) -> List[Dict]:
        """
        批量分析多个文章

        Args:
            articles: 文章列表

        Returns:
            包含分析结果的文章列表
        """
        results = []
        for article in articles:
            results.append(self.analyze_news_article(article))
        return results

    def get_economic_trend_analysis(self, articles: List[Dict]) -> Dict:
        """
        获取经济趋势分析（聚合单篇情感，而非拼接所有内容后分析）

        Args:
            articles: 新闻文章列表

        Returns:
            经济趋势分析结果
        """
        if not articles:
            return {
                'total_articles': 0,
                'trend_sentiment': 'neutral',
                'dominant_keywords': [],
                'sentiment_summary': {}
            }

        # 逐篇分析情感，聚合结果
        scores = []
        labels = []
        all_keywords = []
        for article in articles:
            content = article.get('content', '') or article.get('summary', '')
            if content:
                result = self.sentiment_analyzer.analyze_sentiment(content[:2000])
                scores.append(result['sentiment_score'])
                labels.append(result['sentiment_label'])
            keywords = self.extract_keywords(content, 5)
            all_keywords.extend(keywords)

        # 聚合情感
        avg_score = round(sum(scores) / len(scores), 4) if scores else 0.0
        pos_count = labels.count('positive')
        neg_count = labels.count('negative')
        if avg_score > 0.15:
            trend_label = 'positive'
        elif avg_score < -0.15:
            trend_label = 'negative'
        else:
            trend_label = 'neutral'

        # 关键词频率
        keyword_freq = Counter(all_keywords)
        dominant_keywords = [word for word, _freq in keyword_freq.most_common(10)]

        return {
            'total_articles': len(articles),
            'trend_sentiment': trend_label,
            'dominant_keywords': dominant_keywords,
            'sentiment_summary': {
                'average_sentiment': avg_score,
                'positive_count': pos_count,
                'negative_count': neg_count,
            }
        }

    # ── P1: 实体识别 ──

    # 财经实体词典（沪深300+知名企业+经济术语）
    _FINANCE_COMPANIES = {
        '阿里巴巴', '腾讯', '华为', '京东', '美团', '比亚迪', '宁德时代',
        '贵州茅台', '工商银行', '建设银行', '农业银行', '中国银行', '招商银行',
        '中国平安', '中国人寿', '中信证券', '海通证券', '华泰证券',
        '万科', '碧桂园', '恒大', '融创', '保利',
        '格力', '美的', '海尔', '小米', 'OPPO', 'vivo', '大疆',
        '中芯国际', '寒武纪', '海光信息', '龙芯中科', '华为海思',
        '药明康德', '恒瑞医药', '迈瑞医疗', '百济神州',
        '隆基绿能', '通威股份', '阳光电源', '天合光能',
        '宁德', '蔚来', '理想', '小鹏', '特斯拉',
    }
    _FINANCE_ORGANIZATIONS = {
        '央行', '证监会', '银保监会', '财政部', '发改委', '统计局',
        '美联储', '欧央行', '日央行', 'IMF', '世界银行',
    }
    _FINANCE_INDICATORS = {
        '沪深300', '上证指数', '深证成指', '创业板指', '科创50',
        'GDP', 'CPI', 'PPI', 'PMI', 'LPR', 'MLF', 'OMO',
    }

    def extract_entities(self, text: str, top_k: int = 15) -> Dict[str, List[str]]:
        """
        P1: 实体识别 — jieba 词性标注 + 财经词典匹配

        Returns:
            {'companies': [], 'people': [], 'places': [], 'indicators': [], 'organizations': []}
        """
        if not text:
            return {'companies': [], 'people': [], 'places': [], 'indicators': [], 'organizations': []}

        import jieba.posseg as pseg

        companies = set()
        people = set()
        places = set()
        indicators = set()
        organizations = set()

        words = pseg.lcut(text[:3000])
        for word, flag in words:
            if len(word) < 2:
                continue
            # 词典匹配
            if word in self._FINANCE_COMPANIES:
                companies.add(word)
                continue
            if word in self._FINANCE_ORGANIZATIONS:
                organizations.add(word)
                continue
            if word in self._FINANCE_INDICATORS:
                indicators.add(word)
                continue
            # 词性分类
            if flag.startswith('nr'):
                people.add(word)
            elif flag.startswith('ns'):
                places.add(word)
            elif flag == 'nz':
                if any(kw in word for kw in ['公司', '集团', '银行', '基金', '证券', '保险', '科技']):
                    companies.add(word)
                elif any(kw in word for kw in ['央行', '部', '委', '局', '会']):
                    organizations.add(word)
            # 经济指标匹配
            if flag == 'eng' or re.match(r'^[A-Z]{2,6}$', word):
                indicators.add(word)

        return {
            'companies': list(companies)[:top_k],
            'people': list(people)[:5],
            'places': list(places)[:5],
            'indicators': list(indicators)[:5],
            'organizations': list(organizations)[:5],
        }

    # ── P2: 内容质量评分 ──

    @staticmethod
    def score_quality(article: Dict) -> Dict:
        """
        P2: 内容质量评分（0-100）

        信号:
        - 内容长度 (30分): 200-2000字最佳
        - 来源可信度 (25分): 金十/财联>传统媒体>自媒体
        - 信息密度 (20分): 数字/百分比出现频率
        - 时效性 (15分): 距现在越近越高
        - 结构化 (10分): 段落/引用数
        """
        content = article.get('content', '') or ''
        title = article.get('title', '') or ''
        source = article.get('source', '')
        pub_at = article.get('published_at', '')
        text = title + ' ' + content
        text_len = len(text)

        # 内容长度
        if text_len >= 500:
            length_score = 30
        elif text_len >= 200:
            length_score = 25
        elif text_len >= 80:
            length_score = 15
        else:
            length_score = 5

        # 来源可信度
        high_trust = {'金十数据', '财联社', '财联社RSS', '东方财富', '东方财富公告', '华尔街见闻'}
        medium_trust = {'新浪财经', '第一财经', '网易财经', '同花顺', '同花顺API', '百度财经', '36氪', '虎嗅', '钛媒体'}
        if source in high_trust:
            trust_score = 25
        elif source in medium_trust:
            trust_score = 18
        else:
            trust_score = 10

        # 信息密度（数字/百分比占比）
        import re as _re
        numbers = len(_re.findall(r'\d+\.?\d*%?', text))
        density = numbers / max(text_len / 100, 1)
        density_score = min(20, int(density * 10))

        # 时效性
        try:
            if pub_at and pub_at.startswith('202'):
                from datetime import datetime
                dt = datetime.fromisoformat(pub_at[:19]) if 'T' in pub_at else datetime.strptime(pub_at[:19], '%Y-%m-%d %H:%M:%S')
                hours_ago = (datetime.now() - dt).total_seconds() / 3600
                if hours_ago < 1:
                    time_score = 15
                elif hours_ago < 6:
                    time_score = 12
                elif hours_ago < 24:
                    time_score = 8
                elif hours_ago < 72:
                    time_score = 4
                else:
                    time_score = 1
            else:
                time_score = 5
        except Exception:
            time_score = 5

        # 结构化
        paragraphs = len([p for p in content.split('\n') if len(p) > 20])
        struct_score = min(10, paragraphs)

        total = length_score + trust_score + density_score + time_score + struct_score
        return {
            'total': total,
            'length': length_score,
            'trust': trust_score,
            'density': density_score,
            'timeliness': time_score,
            'structure': struct_score,
            'label': 'high' if total >= 70 else ('medium' if total >= 45 else 'low'),
        }

    # ── P3: 热点话题检测 ──

    @staticmethod
    def detect_hot_topics(articles: List[Dict], top_k: int = 10) -> List[Dict]:
        """
        P3: 热点话题检测 — 基于时间窗口的 TF 变化

        将文章按发布时间分组，比较最近窗口 vs 前一窗口的词频变化，
        识别正在升温的关键词。

        Returns:
            [{'keyword': str, 'current_count': int, 'prev_count': int, 'trend': 'rising'|'falling'|'stable'}]
        """
        if len(articles) < 10:
            return []

        import jieba
        from collections import Counter
        from datetime import datetime, timedelta

        # 按发布时间排序
        sorted_articles = sorted(articles, key=lambda a: a.get('published_at', ''), reverse=True)

        # 取最近 50% 和之前 50%
        mid = len(sorted_articles) // 2
        recent = sorted_articles[:mid]
        older = sorted_articles[mid:]

        stop_words = {'的', '了', '在', '是', '和', '就', '不', '也', '很', '到', '说',
                      '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这', '那'}

        def extract_words(arts):
            words = []
            for a in arts:
                text = (a.get('title', '') + ' ' + (a.get('content', '') or a.get('summary', ''))[:200])
                for w in jieba.lcut(text):
                    if len(w) > 1 and w not in stop_words and not w.isdigit():
                        words.append(w)
            return Counter(words)

        recent_words = extract_words(recent)
        older_words = extract_words(older)

        # 计算趋势
        results = []
        all_keywords = set(list(recent_words.keys())[:50] + list(older_words.keys())[:50])
        for kw in all_keywords:
            cur = recent_words.get(kw, 0)
            prev = older_words.get(kw, 0)
            if cur < 3 and prev < 3:
                continue
            if cur > prev * 1.5:
                trend = 'rising'
            elif cur < prev * 0.5:
                trend = 'falling'
            else:
                trend = 'stable'
            results.append({'keyword': kw, 'current_count': cur, 'prev_count': prev, 'trend': trend})

        results.sort(key=lambda x: x['current_count'] - x['prev_count'], reverse=True)
        return results[:top_k]

    # ── P4: 情感细分 ──

    def sentiment_breakdown(self, articles: List[Dict]) -> Dict:
        """
        P4: 情感趋势按来源和分类细分

        Returns:
            {'by_source': {source: {avg_score, positive, negative, neutral, count}},
             'by_category': {category: ...},
             'by_entity': {entity: {avg_score, count}}}
        """
        if not articles:
            return {'by_source': {}, 'by_category': {}, 'by_entity': {}}

        from collections import defaultdict

        source_data = defaultdict(lambda: {'scores': [], 'positive': 0, 'negative': 0, 'neutral': 0})
        category_data = defaultdict(lambda: {'scores': [], 'positive': 0, 'negative': 0, 'neutral': 0})
        entity_data = defaultdict(lambda: {'scores': [], 'count': 0})

        for article in articles:
            src = article.get('source', '未知')
            cat = article.get('category', '未分类')
            content = article.get('content', '') or article.get('summary', '')
            title = article.get('title', '')

            # 情感分数（取已有分析结果或即时计算）
            text = (title + ' ' + content)[:500]
            if text.strip():
                result = self.sentiment_analyzer.analyze_sentiment(text)
                score = result['sentiment_score']
                label = result['sentiment_label']
            else:
                score, label = 0.0, 'neutral'

            source_data[src]['scores'].append(score)
            source_data[src][label] += 1

            category_data[cat]['scores'].append(score)
            category_data[cat][label] += 1

            # 实体情感
            entities = self.extract_entities(content)
            for company in entities.get('companies', [])[:5]:
                entity_data[company]['scores'].append(score)
                entity_data[company]['count'] += 1

        def _summarize(data):
            result = {}
            for key, val in data.items():
                scores = val.pop('scores')
                result[key] = {
                    **val,
                    'avg_score': round(sum(scores) / len(scores), 4) if scores else 0,
                    'count': len(scores),
                }
            return dict(sorted(result.items(), key=lambda x: x[1]['count'], reverse=True))

        return {
            'by_source': _summarize(source_data),
            'by_category': _summarize(category_data),
            'by_entity': _summarize(entity_data),
        }

    # ── P5: 文章聚类 ──

    @staticmethod
    def cluster_articles(articles: List[Dict], threshold: float = 0.4) -> List[Dict]:
        """
        P5: 文章聚类 — TF-IDF + cosine similarity + 阈值聚类

        对非重复文章做主题聚类，发现相关报道群。

        Returns:
            [{'label': str, 'size': int, 'articles': [...], 'keywords': [...]}]
        """
        if len(articles) < 3:
            return []

        import jieba
        import math
        from collections import Counter

        # 1. 构建文档词频矩阵
        stop_words = {'的', '了', '在', '是', '和', '就', '不', '也', '很', '到', '说',
                      '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这', '那',
                      '与', '为', '但', '而', '及', '被', '把', '从', '对', '以', '将'}

        docs = []
        for a in articles:
            text = (a.get('title', '') + ' ' + (a.get('content', '') or a.get('summary', ''))[:500])
            words = [w for w in jieba.lcut(text) if len(w) > 1 and w not in stop_words]
            docs.append(Counter(words))

        # 2. 计算 IDF
        N = len(docs)
        df = Counter()
        for doc in docs:
            for word in set(doc.keys()):
                df[word] += 1

        idf = {w: math.log((N + 1) / (df[w] + 1)) + 1 for w in df}

        # 3. 文档向量 + cosine similarity
        def cosine(d1, d2):
            common = set(d1.keys()) & set(d2.keys())
            dot = sum(d1[w] * idf.get(w, 1) * d2[w] * idf.get(w, 1) for w in common)
            norm1 = math.sqrt(sum((d1[w] * idf.get(w, 1))**2 for w in d1))
            norm2 = math.sqrt(sum((d2[w] * idf.get(w, 1))**2 for w in d2))
            return dot / (norm1 * norm2) if norm1 and norm2 else 0

        # 4. 阈值聚类
        n = len(articles)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for i in range(n):
            for j in range(i + 1, n):
                if cosine(docs[i], docs[j]) >= threshold:
                    union(i, j)

        # 5. 组织结果
        clusters = {}
        for i in range(n):
            root = find(i)
            clusters.setdefault(root, []).append(i)

        results = []
        for indices in clusters.values():
            if len(indices) < 2:
                continue
            # 提取簇关键词
            combined = Counter()
            for idx in indices:
                combined.update({w: c for w, c in docs[idx].items() if len(w) > 1})
            keywords = [w for w, _ in combined.most_common(5)]

            # 取最短标题为标签
            titles = [(len(articles[i].get('title', '')), articles[i].get('title', '')) for i in indices]
            titles.sort()
            label = titles[0][1][:40] if titles else '未命名'

            results.append({
                'label': label,
                'size': len(indices),
                'keywords': keywords,
                'article_ids': [articles[i].get('article_id', '') for i in indices],
            })

        results.sort(key=lambda x: x['size'], reverse=True)
        return results[:20]