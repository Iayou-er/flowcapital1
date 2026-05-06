"""
数据库模块初始化
"""

from .db_manager import DatabaseManager, db
from .models import NewsArticle, AnalysisResult

__all__ = ['DatabaseManager', 'db', 'NewsArticle', 'AnalysisResult']