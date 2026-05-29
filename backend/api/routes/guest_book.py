"""
留言板 API 路由
匿名用户实名留言板 — Session 绑定模式，防止 ID 伪造/冒用
"""
import html
import re
import random
import uuid
import time
from fastapi import APIRouter, HTTPException, Query, Depends, Request
from pydantic import BaseModel, Field, field_validator
from backend.database.db_manager_async import db_async as db
from backend.database.redis_client import redis_client
from backend.api.auth import verify_api_key, verify_csrf

try:
    import nh3
    _has_nh3 = True
except ImportError:
    nh3 = None
    _has_nh3 = False

guest_book_router = APIRouter(prefix="/guest", tags=["留言板"])

# Session 存储：{session_token: {anonymous_id, created_at}}
# 优先使用 Redis，降级使用内存
import asyncio
_SESSION_PREFIX = "guest_session:"
_SESSION_TTL = 86400  # 24 小时
_mem_sessions: dict = {}  # 内存降级存储
_mem_sessions_lock = asyncio.Lock()


def _session_key(token: str) -> str:
    return f"{_SESSION_PREFIX}{token}"


async def _create_session(anonymous_id: str) -> str:
    """创建会话并返回 session_token"""
    token = uuid.uuid4().hex
    data = {"anonymous_id": anonymous_id, "created_at": time.time()}
    redis_key = _session_key(token)
    try:
        if await redis_client.is_available():
            import json
            await redis_client.set(redis_key, data, ttl=_SESSION_TTL)
        else:
            async with _mem_sessions_lock:
                _mem_sessions[token] = data
    except Exception:
        async with _mem_sessions_lock:
            _mem_sessions[token] = data
    return token


async def _resolve_session(token: str) -> str | None:
    """从 session 解析 anonymous_id，无效返回 None"""
    try:
        redis_key = _session_key(token)
        if await redis_client.is_available():
            data = await redis_client.get(redis_key)
            if data:
                return data.get("anonymous_id") if isinstance(data, dict) else None
        async with _mem_sessions_lock:
            return _mem_sessions.get(token, {}).get("anonymous_id")
    except Exception:
        async with _mem_sessions_lock:
            return _mem_sessions.get(token, {}).get("anonymous_id")


def _sanitize_text(text: str) -> str:
    """清理用户输入，防止 XSS（nh3 优先，回退到 html.escape）"""
    if _has_nh3:
        text = nh3.clean(text, tags=set(), attributes={}, strip=True)
    else:
        text = html.escape(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


class GuestLoginRequest(BaseModel):
    """客户端确认登录时发送的请求"""
    anonymous_id: str = Field(..., min_length=1, max_length=50, description="服务端分配的匿名ID")


class GuestMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000, description="留言内容")
    session_token: str = Field(..., min_length=1, max_length=64, description="登录会话令牌")

    @field_validator('content')
    @classmethod
    def validate_content(cls, v):
        return v.strip()


@guest_book_router.get("/assign-id")
async def assign_anonymous_id():
    """分配匿名用户ID（随机6位数，如：匿名用户 482937）"""
    try:
        random_num = random.randint(100000, 999999)
        anonymous_id = f"匿名用户 {random_num}"
        return {"code": 0, "data": {"anonymous_id": anonymous_id}}
    except Exception as e:
        raise HTTPException(status_code=500, detail="分配匿名ID失败")


@guest_book_router.post("/login", dependencies=[Depends(verify_csrf)])
async def guest_login(request: GuestLoginRequest):
    """客户端确认登录，创建 Session 并返回 session_token"""
    try:
        # 验证 anonymous_id 格式：匿名用户 + 6位数字
        if not re.match(r'^匿名用户 \d{6}$', request.anonymous_id):
            raise HTTPException(status_code=400, detail="匿名ID格式不正确")

        token = await _create_session(request.anonymous_id)
        return {"code": 0, "data": {"session_token": token}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="登录失败")


@guest_book_router.post("/message", dependencies=[Depends(verify_api_key)])
async def post_message(request: GuestMessageRequest):
    """提交留言 — 通过 session_token 验证身份，不依赖客户端传递 anonymous_id"""
    if not request.content or not request.content.strip():
        raise HTTPException(status_code=400, detail="留言内容不能为空")

    # 从 session 解析 anonymous_id，防止 ID 伪造
    anonymous_id = await _resolve_session(request.session_token)
    if not anonymous_id:
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")

    content = _sanitize_text(request.content)
    if len(content) > 2000:
        raise HTTPException(status_code=400, detail="留言内容过长")
    if len(content) < 1:
        raise HTTPException(status_code=400, detail="留言内容不能为空")

    try:
        success = await db.save_guest_message(anonymous_id, content)
        if success:
            return {"code": 0, "data": {"message": "留言成功"}}
        else:
            raise HTTPException(status_code=500, detail="留言保存失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="留言失败")


@guest_book_router.get("/messages")
async def get_messages(
    page: int = Query(1, ge=1, le=1000, description="页码"),
    limit: int = Query(20, ge=1, le=100, description="每页数量"),
):
    """分页获取留言列表"""
    try:
        messages, total = await db.get_guest_messages(page=page, limit=limit)
        return {"code": 0, "data": messages, "count": len(messages), "total": total, "page": page}
    except Exception as e:
        logger.error("获取留言失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")
