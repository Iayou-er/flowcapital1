"""
数据库模块初始化
"""

from .db_manager import DatabaseManager, db  # deprecated: 同步版，仅保留作为回退
from .db_manager_async import db_async
from .models import NewsArticle, AnalysisResult

__all__ = ['DatabaseManager', 'db', 'db_async', 'NewsArticle', 'AnalysisResult']