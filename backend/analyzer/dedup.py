"""
新闻去重/聚类模块
基于 SimHash 算法合并多源爬取的重复报道，桶索引优化 O(n²)→O(n)
"""

import hashlib
import logging
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)


class SimHash:
    """SimHash 指纹生成与比较"""

    def __init__(self, tokens: List[str], f: int = 64):
        self.f = f
        self.hash = self._compute(tokens)

    def _compute(self, tokens: List[str]) -> int:
        v = [0] * self.f
        for token in tokens:
            h = int(hashlib.md5(token.encode('utf-8')).hexdigest(), 16)
            for i in range(self.f):
                v[i] += 1 if (h >> i) & 1 else -1

        fingerprint = 0
        for i in range(self.f):
            if v[i] > 0:
                fingerprint |= (1 << i)
        return fingerprint

    def distance(self, other: 'SimHash') -> int:
        """汉明距离"""
        x = self.hash ^ other.hash
        return bin(x).count('1')


def _tokenize(text: str) -> List[str]:
    """将文本分词为 SimHash 输入"""
    import jieba
    return [w for w in jieba.lcut(text) if len(w) > 1]


def _text_features(article: Dict) -> str:
    """提取文章的关键文本（标题+摘要前200字）"""
    title = article.get('title', '')
    summary = (article.get('summary', '') or '')[:200]
    return title + ' ' + summary


def _simhash_buckets(hash_val: int, segments: int = 4) -> List[str]:
    """将 64 位哈希分成 N 段，每段作为桶 key"""
    mask = (1 << (64 // segments)) - 1
    keys = []
    for i in range(segments):
        segment = (hash_val >> (i * 16)) & mask
        keys.append(f"{i}:{segment}")
    return keys


class NewsDeduplicator:
    """新闻去重器 — 基于 SimHash 桶索引 + 汉明距离聚类"""

    def __init__(self, threshold: int = 4):
        """
        Args:
            threshold: 汉明距离阈值，<= 此值视为重复。64位 SimHash 通常取 3~6
        """
        self.threshold = threshold
        self._segments = 4  # 将 64 位分 4 段，每段 16 位

    def deduplicate(self, articles: List[Dict]) -> List[Dict]:
        """
        对新闻列表去重，桶索引优化

        原理：
        - 若两篇 SimHash 汉明距离 ≤ threshold(=4)，则 4 段中至少有一段完全相同
        - 将 64 位哈希分 4 段，每段 16 位作为桶 key
        - 只比较落入同一桶的候选对，避免 O(n²)

        Args:
            articles: 新闻条目列表

        Returns:
            去重后的新闻列表
        """
        if not articles:
            return []

        # 预计算所有文章的 SimHash
        simhashes = []
        for article in articles:
            tokens = _tokenize(_text_features(article))
            simhashes.append(SimHash(tokens))

        n = len(articles)
        used = [False] * n
        # Union-Find 风格的聚类
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

        # 桶索引：{bucket_key: [article_indices]}
        buckets = {}
        for idx, sh in enumerate(simhashes):
            for key in _simhash_buckets(sh.hash, self._segments):
                buckets.setdefault(key, []).append(idx)

        # 只比较同一桶内的候选对
        compared = set()
        for bucket_key, indices in buckets.items():
            m = len(indices)
            if m < 2:
                continue
            for a in range(m):
                for b in range(a + 1, m):
                    i, j = indices[a], indices[b]
                    if i > j:
                        i, j = j, i
                    pair = (i, j)
                    if pair in compared:
                        continue
                    compared.add(pair)
                    dist = simhashes[i].distance(simhashes[j])
                    if dist <= self.threshold:
                        union(i, j)

        # 每簇取 content 最长的代表
        clusters = {}
        for idx in range(n):
            root = find(idx)
            if root not in clusters:
                clusters[root] = []
            clusters[root].append(idx)

        results = []
        for cluster_indices in clusters.values():
            best_idx = max(cluster_indices, key=lambda i: len(articles[i].get('content', '') or ''))
            results.append(articles[best_idx])

        removed = n - len(results)
        if removed > 0:
            logger.info(
                f"SimHash 去重: {n} 条 → {len(results)} 条, 移除 {removed} 条, "
                f"实际比较 {len(compared)} 对 (桶索引优化)"
            )

        return results

    def find_duplicates(self, articles: List[Dict]) -> List[Tuple[Dict, Dict, int]]:
        """
        找出所有重复对及其汉明距离（桶索引优化版）

        Returns:
            列表 of (article_a, article_b, distance)
        """
        simhashes = []
        for article in articles:
            tokens = _tokenize(_text_features(article))
            simhashes.append(SimHash(tokens))

        n = len(articles)
        buckets = {}
        for idx, sh in enumerate(simhashes):
            for key in _simhash_buckets(sh.hash, self._segments):
                buckets.setdefault(key, []).append(idx)

        duplicates = []
        compared = set()
        for indices in buckets.values():
            m = len(indices)
            if m < 2:
                continue
            for a in range(m):
                for b in range(a + 1, m):
                    i, j = indices[a], indices[b]
                    if i > j:
                        i, j = j, i
                    pair = (i, j)
                    if pair in compared:
                        continue
                    compared.add(pair)
                    dist = simhashes[i].distance(simhashes[j])
                    if dist <= self.threshold:
                        duplicates.append((articles[i], articles[j], dist))

        return duplicates
