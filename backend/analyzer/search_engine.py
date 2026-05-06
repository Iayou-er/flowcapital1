"""
全文搜索引擎
基于 Whoosh 实现中文新闻的多关键词组合搜索
"""

import os
import logging
from typing import List, Dict, Optional, Tuple
from whoosh.index import create_in, open_dir, Index
from whoosh.fields import Schema, TEXT, ID, DATETIME
from whoosh.qparser import QueryParser, MultifieldParser, GroupPlugin, FuzzyTermPlugin
from datetime import datetime
import jieba
import threading

logger = logging.getLogger(__name__)

# 索引路径
INDEX_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'whoosh_index')


class ChineseAnalyzer:
    """基于 jieba 的 Whoosh 中文分词分析器"""

    def __call__(self, value, positions=False, chars=False, keeporiginal=False,
                 removestops=True, start_pos=0, start_chars=0, **kwargs):
        from whoosh.analysis import Token
        pos = start_pos
        for word in jieba.lcut(value):
            word = word.strip()
            if len(word) < 2:
                continue
            token = Token()
            token.text = word
            if positions:
                token.pos = pos
            pos += 1
            yield token

    def __repr__(self):
        return "ChineseAnalyzer()"


def _parse_date(val) -> Optional[datetime]:
    """将 ISO 日期字符串转为 datetime 对象"""
    if val is None or val == '':
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        return None


# 懒初始化
_schema = Schema(
    article_id=ID(stored=True, unique=True),
    title=TEXT(stored=True, analyzer=ChineseAnalyzer(), field_boost=2.0),
    content=TEXT(stored=False, analyzer=ChineseAnalyzer()),
    summary=TEXT(stored=False, analyzer=ChineseAnalyzer()),
    source=TEXT(stored=True, analyzer=ChineseAnalyzer()),
    category=TEXT(stored=True, analyzer=ChineseAnalyzer()),
    published_at=DATETIME(stored=True),
)

_index: Optional[Index] = None
_lock = threading.Lock()


def _get_index() -> Index:
    global _index
    os.makedirs(INDEX_DIR, exist_ok=True)
    # Whoosh 索引标识：目录下存在 _MAIN_*.toc 文件
    toc_files = [f for f in os.listdir(INDEX_DIR) if f.startswith('_MAIN_') and f.endswith('.toc')]
    if toc_files:
        _index = open_dir(INDEX_DIR)
    else:
        _index = create_in(INDEX_DIR, _schema)
        logger.info("Whoosh 索引已创建")
    return _index


def rebuild_index(news_list: List[Dict]) -> int:
    """
    重建索引：用于首次初始化或批量导入

    Args:
        news_list: 新闻列表（字典，需包含 article_id/title/content/source/category/published_at）

    Returns:
        索引的新闻数量
    """
    idx = _get_index()
    writer = idx.writer()
    count = 0
    for news in news_list:
        try:
            writer.add_document(
                article_id=news.get('article_id', ''),
                title=news.get('title', ''),
                content=news.get('content', ''),
                summary=news.get('summary', ''),
                source=news.get('source', ''),
                category=news.get('category', ''),
                published_at=_parse_date(news.get('published_at')),
            )
            count += 1
        except Exception as e:
            logger.warning(f"索引新闻失败: {e}")
    writer.commit()
    logger.info(f"Whoosh 重建索引完成: {count} 条")
    return count


def add_document(news: Dict):
    """添加单条新闻到索引"""
    idx = _get_index()
    writer = idx.writer()
    try:
        writer.update_document(
            article_id=news.get('article_id', ''),
            title=news.get('title', ''),
            content=news.get('content', ''),
            summary=news.get('summary', ''),
            source=news.get('source', ''),
            category=news.get('category', ''),
            published_at=_parse_date(news.get('published_at')),
        )
    except Exception as e:
        logger.warning(f"索引更新失败: {e}")
    finally:
        writer.commit()


def add_documents_batch(news_list: List[Dict]) -> int:
    """批量添加新闻到索引（单个 writer，单次 commit）"""
    if not news_list:
        return 0
    idx = _get_index()
    writer = idx.writer()
    count = 0
    for news in news_list:
        try:
            writer.update_document(
                article_id=news.get('article_id', ''),
                title=news.get('title', ''),
                content=news.get('content', ''),
                summary=news.get('summary', ''),
                source=news.get('source', ''),
                category=news.get('category', ''),
                published_at=_parse_date(news.get('published_at')),
            )
            count += 1
        except Exception as e:
            logger.warning(f"索引更新失败: {e}")
    writer.commit()
    return count


def remove_document(article_id: str):
    """从索引中删除新闻"""
    idx = _get_index()
    writer = idx.writer()
    try:
        writer.delete_by_term('article_id', article_id)
    except Exception as e:
        logger.warning(f"索引删除失败: {e}")
    finally:
        writer.commit()


def optimize_index():
    """压缩优化索引（建议在每日调度任务中调用）"""
    try:
        idx = _get_index()
        writer = idx.writer()
        writer.commit(optimize=True)
        logger.info("Whoosh 索引优化完成")
    except Exception as e:
        logger.warning(f"索引优化失败: {e}")


def search(
    query: str,
    page: int = 1,
    limit: int = 20,
    category: str = None,
    source: str = None,
) -> Tuple[List[str], int]:
    """
    全文搜索

    Args:
        query: 搜索查询（支持多词，如 "AI 芯片 半导体"）
        page: 页码
        limit: 每页数量
        category: 按分类过滤
        source: 按来源过滤

    Returns:
        (article_id 列表, 总结果数)
    """
    if not query or not query.strip():
        return [], 0

    idx = _get_index()

    # 多字段搜索：标题 > 正文 > 摘要
    qp = MultifieldParser(
        ['title', 'content', 'summary'],
        schema=_schema,
    )
    qp.add_plugin(FuzzyTermPlugin())
    qp.add_plugin(GroupPlugin())

    try:
        q = qp.parse(query)
    except Exception as e:
        logger.warning(f"查询解析失败: {e}")
        return [], 0

    # 分类/来源过滤
    filter_terms = []
    if category:
        filter_terms.append(('category', category))
    if source:
        filter_terms.append(('source', source))

    with idx.searcher() as searcher:
        results = searcher.search(q, limit=page * limit, filter=filter_terms if filter_terms else None)

        article_ids = [r['article_id'] for r in results]
        total = len(results)

    # 分页
    offset = (page - 1) * limit
    return article_ids[offset:offset + limit], total
