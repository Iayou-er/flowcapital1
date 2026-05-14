"""
数据库引擎（SQLAlchemy 2.0 async）
根据 DATABASE_URL 环境变量切换 SQLite / PostgreSQL 后端
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///data/sqlite.db"  # 开发默认
)

# asyncpg 专有参数，仅 PostgreSQL 后端生效
connect_args = {}
if DATABASE_URL.startswith("postgresql"):
    connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,
    connect_args=connect_args,
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
