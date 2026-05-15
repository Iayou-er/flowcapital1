"""EventRepository — event_log 表 CRUD"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

from sqlalchemy import text

from .base import AsyncRepository
from ..sql_helpers import EVENT_COLS
from ..engine import AsyncSessionLocal
from ..retry import retry_on_lock

logger = logging.getLogger(__name__)


class EventRepository(AsyncRepository):
    table_name = "event_log"
    columns = EVENT_COLS

    @retry_on_lock(max_retries=3, delay=0.1)
    async def save(
        self, event_type: str, article_id: Optional[str] = None,
        payload: Optional[str] = None, client_id: Optional[str] = None,
    ) -> bool:
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text(
                        "INSERT INTO event_log (event_type, article_id, payload, client_id, created_at) "
                        "VALUES (:etype, :aid, :payload, :cid, :created_at)"
                    ),
                    {
                        "etype": event_type,
                        "aid": article_id,
                        "payload": payload,
                        "cid": client_id,
                        "created_at": datetime.now().isoformat(),
                    },
                )
                await session.commit()
                return True
        except Exception as e:
            logger.warning("写入事件失败: %s", e)
            return False

    async def get_recent(self, hours: int = 24) -> List[Dict]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        try:
            async with AsyncSessionLocal() as session:
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(EVENT_COLS)} FROM event_log "
                            "WHERE created_at >= :since ORDER BY created_at DESC"
                        ),
                        {"since": since},
                    )
                ).fetchall()
                return [dict(zip(EVENT_COLS, r)) for r in rows]
        except Exception as e:
            logger.error("获取事件失败: %s", e)
            return []
