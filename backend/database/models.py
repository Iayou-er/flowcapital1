"""
数据库模型定义
定义新闻文章和分析结果的数据结构
"""

from datetime import datetime
from typing import List, Optional

class NewsArticle:
    """
    新闻文章模型
    """

    def __init__(self,
                 article_id: str,
                 title: str,
                 content: str = '',
                 summary: str = '',
                 url: str = '',
                 source: str = '华尔街见闻',
                 category: str = '',
                 published_at: str = '',
                 author: str = '',
                 read_count: int = 0,
                 comment_count: int = 0,
                 tags: Optional[List[str]] = None,
                 created_at: Optional[str] = None):
        """
        初始化新闻文章对象

        Args:
            article_id: 文章唯一标识
            title: 文章标题
            content: 文章内容
            summary: 文章摘要
            url: 文章URL
            source: 新闻来源
            category: 文章分类
            published_at: 发布时间
            author: 作者
            read_count: 阅读数
            comment_count: 评论数
            tags: 标签列表
            created_at: 创建时间
        """
        self.article_id = article_id
        self.title = title
        self.content = content
        self.summary = summary
        self.url = url
        self.source = source
        self.category = category
        self.published_at = published_at
        self.author = author
        self.read_count = read_count
        self.comment_count = comment_count
        self.tags = tags if tags is not None else []
        self.created_at = created_at if created_at is not None else datetime.now().isoformat()

    def to_dict(self) -> dict:
        """
        转换为字典格式

        Returns:
            字典格式的新闻文章数据
        """
        return {
            'article_id': self.article_id,
            'title': self.title,
            'content': self.content,
            'summary': self.summary,
            'url': self.url,
            'source': self.source,
            'category': self.category,
            'published_at': self.published_at,
            'author': self.author,
            'read_count': self.read_count,
            'comment_count': self.comment_count,
            'tags': self.tags,
            'created_at': self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict):
        """
        从字典创建新闻文章对象

        Args:
            data: 字典格式的数据

        Returns:
            NewsArticle对象
        """
        return cls(
            article_id=data.get('article_id', ''),
            title=data.get('title', ''),
            content=data.get('content', ''),
            summary=data.get('summary', ''),
            url=data.get('url', ''),
            source=data.get('source', '华尔街见闻'),
            category=data.get('category', ''),
            published_at=data.get('published_at', ''),
            author=data.get('author', ''),
            read_count=data.get('read_count', 0),
            comment_count=data.get('comment_count', 0),
            tags=data.get('tags', []),
            created_at=data.get('created_at', datetime.now().isoformat())
        )

class AnalysisResult:
    """
    分析结果模型
    """

    def __init__(self,
                 article_id: str,
                 model_used: str,
                 sentiment_score: float = 0.0,
                 sentiment_label: str = '',
                 keywords: Optional[List[str]] = None,
                 summary: str = '',
                 analysis_type: str = 'sentiment',
                 result: str = '',
                 created_at: Optional[str] = None):
        """
        初始化分析结果对象

        Args:
            article_id: 关联的文章ID
            model_used: 使用的模型
            sentiment_score: 情感分数 (-1到1)
            sentiment_label: 情感标签 (positive/negative/neutral)
            keywords: 关键词列表
            analysis_type: 分析类型
            result: 分析结果
            created_at: 创建时间
        """
        self.article_id = article_id
        self.model_used = model_used
        self.sentiment_score = sentiment_score
        self.sentiment_label = sentiment_label
        self.keywords = keywords if keywords is not None else []
        self.summary = summary
        self.analysis_type = analysis_type
        self.result = result
        self.created_at = created_at if created_at is not None else datetime.now().isoformat()

    def to_dict(self) -> dict:
        """
        转换为字典格式

        Returns:
            字典格式的分析结果数据
        """
        return {
            'article_id': self.article_id,
            'model_used': self.model_used,
            'sentiment_score': self.sentiment_score,
            'sentiment_label': self.sentiment_label,
            'keywords': self.keywords,
            'summary': self.summary,
            'analysis_type': self.analysis_type,
            'result': self.result,
            'created_at': self.created_at
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            article_id=data.get('article_id', ''),
            model_used=data.get('model_used', ''),
            sentiment_score=data.get('sentiment_score', 0.0),
            sentiment_label=data.get('sentiment_label', ''),
            keywords=data.get('keywords', []),
            summary=data.get('summary', ''),
            analysis_type=data.get('analysis_type', 'sentiment'),
            result=data.get('result', ''),
            created_at=data.get('created_at', datetime.now().isoformat())
        )