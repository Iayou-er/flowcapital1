"""异步仓库基类"""
import contextlib
import contextvars
import logging
from typing import List, Dict, Optional, Tuple, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..engine import AsyncSessionLocal

logger = logging.getLogger(__name__)

_current_session: contextvars.ContextVar[Optional[AsyncSession]] = \
    contextvars.ContextVar('_current_session', default=None)


class AsyncRepository:
    table_name: str = ""
    columns: List[str] = []

    async def _execute(self, sql: str, params: Dict[str, Any] = None,
                       session: AsyncSession = None):
        s = session or _current_session.get()
        if s:
            return await s.execute(text(sql), params or {})
        async with AsyncSessionLocal() as s:
            return await s.execute(text(sql), params or {})

    async def _fetchone(self, sql: str, params: Dict[str, Any] = None,
                        session: Optional[AsyncSession] = None) -> Optional[Tuple]:
        s = session or _current_session.get()
        if s:
            result = await s.execute(text(sql), params or {})
        else:
            async with AsyncSessionLocal() as s:
                result = await s.execute(text(sql), params or {})
        return result.fetchone()

    async def _fetchall(self, sql: str, params: Dict[str, Any] = None,
                        session: Optional[AsyncSession] = None) -> List[Tuple]:
        s = session or _current_session.get()
        if s:
            result = await s.execute(text(sql), params or {})
        else:
            async with AsyncSessionLocal() as s:
                result = await s.execute(text(sql), params or {})
        return result.fetchall()

    @contextlib.asynccontextmanager
    async def transaction(self):
        """事务上下文管理器（contextvars 隔离，协程安全）"""
        token = None
        async with AsyncSessionLocal() as session:
            token = _current_session.set(session)
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                _current_session.reset(token)
