"""
用户行为埋点 API
接收前端点击、浏览、搜索等行为事件
"""
import json
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.database.db_manager_async import db_async as db

logger = logging.getLogger(__name__)

events_router = APIRouter(prefix="/events", tags=["用户行为"])


class EventModel(BaseModel):
    type: str  # article_click / article_view / search_query / search_click
    article_id: Optional[str] = None
    payload: Optional[dict] = None  # {duration_ms, query, rank, ...}
    client_id: Optional[str] = None


@events_router.post("/track")
async def track_event(event: EventModel):
    """
    接收前端埋点事件，写入 event_log 表
    payload 以 JSON 字符串存入，读取时 json.loads 还原
    """
    try:
        await db.insert_event(
            event_type=event.type,
            article_id=event.article_id,
            payload=json.dumps(event.payload, ensure_ascii=False) if event.payload else None,
            client_id=event.client_id,
        )
        return {'code': 0}
    except Exception as e:
        logger.warning(f"埋点写入失败: {e}")
        return {'code': 0}  # 静默失败，不影响前端
