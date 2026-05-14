"""
ORM 模型定义（SQLAlchemy 2.0 declarative）
涵盖所有业务表：news_articles / analysis_results / guest_book / event_log
"""
from sqlalchemy import Column, String, Integer, Float, Text, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(32), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    content = Column(Text)
    summary = Column(Text)
    url = Column(String(2048))
    source = Column(String(100), index=True)
    category = Column(String(100))
    published_at = Column(String(30), index=True)
    author = Column(String(200))
    read_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    tags = Column(Text)
    hotness_score = Column(Float, default=0.0)
    created_at = Column(String(30))
    updated_at = Column(String(30))

    __table_args__ = (
        Index("idx_news_url", "url", unique=True,
              postgresql_where=url.isnot(None)),
        Index("idx_news_hotness", "hotness_score"),
        Index("idx_news_pub_hotness", "published_at", "hotness_score"),
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(32), nullable=False, index=True)
    model_used = Column(String(50))
    sentiment_score = Column(Float)
    sentiment_label = Column(String(20))
    keywords = Column(Text)
    summary = Column(Text)
    analysis_type = Column(String(50))
    result = Column(Text)
    created_at = Column(String(30))


class GuestBookEntry(Base):
    __tablename__ = "guest_book"

    id = Column(Integer, primary_key=True, autoincrement=True)
    anonymous_id = Column(String(64), index=True)
    content = Column(Text, nullable=False)
    created_at = Column(String(30), index=True)


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)
    article_id = Column(String(32), index=True)
    client_id = Column(String(64))
    payload = Column(Text)
    created_at = Column(String(30), index=True)
