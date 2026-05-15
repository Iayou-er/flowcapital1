"""GuestBookRepository — guest_book 表 CRUD"""
import logging
from datetime import datetime
from typing import List, Dict, Tuple

from sqlalchemy import text

from .base import AsyncRepository
from ..sql_helpers import GUEST_COLS
from ..engine import AsyncSessionLocal
from ..retry import retry_on_lock

logger = logging.getLogger(__name__)


class GuestBookRepository(AsyncRepository):
    table_name = "guest_book"
    columns = GUEST_COLS

    @retry_on_lock(max_retries=3, delay=0.1)
    async def save(self, anonymous_id: str, content: str) -> bool:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(
                        "INSERT INTO guest_book (anonymous_id, content, created_at) "
                        "VALUES (:aid, :content, :created_at)"
                    ),
                    {"aid": anonymous_id, "content": content, "created_at": datetime.now().isoformat()},
                )
                await session.commit()
                return True
        except Exception as e:
            logger.error("保存留言失败: %s", e)
            return False

    async def get_messages(
        self, page: int = 1, limit: int = 20
    ) -> Tuple[List[Dict], int]:
        try:
            async with AsyncSessionLocal() as session:
                total = (
                    await session.execute(text("SELECT COUNT(*) FROM guest_book"))
                ).scalar() or 0
                offset = (page - 1) * limit
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(GUEST_COLS)} FROM guest_book "
                            "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
                        ),
                        {"limit": limit, "offset": offset},
                    )
                ).fetchall()
                return [dict(zip(GUEST_COLS, r)) for r in rows], total
        except Exception as e:
            logger.error("获取留言失败: %s", e)
            return [], 0

    async def count(self) -> int:
        try:
            async with AsyncSessionLocal() as session:
                return (
                    await session.execute(text("SELECT COUNT(*) FROM guest_book"))
                ).scalar() or 0
        except Exception:
            return 0
