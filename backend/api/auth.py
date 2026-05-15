"""
API 认证中间件
提供 API Key 认证，保护关键接口（写操作、管理操作）
"""
import os
import logging
from urllib.parse import urlparse
from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from typing import Optional

logger = logging.getLogger(__name__)

# 从环境变量读取配置
API_KEY_HEADER_NAME = "X-API-Key"
API_KEY = os.environ.get("API_KEY", "")
ENABLE_AUTH = os.environ.get("ENABLE_AUTH", "true").lower() == "true"

# 写操作/管理操作路由 — 必须认证（支持前缀匹配）
PROTECTED_PATHS = [
    "/api/graph-rag/build-graph",
    "/api/graph-rag/query",
    "/api/graph-rag/info",
    "/api/graph-rag/entity-timeline",
    "/api/graph-rag/query-test",
    "/api/news/rebuild-index",
    "/api/news/translate",
    "/api/guest/message",
    "/api/admin",
    "/metrics",
]

# 公开路由（允许匿名访问的写操作，受频率限制保护）
PUBLIC_WRITE_PATHS = [
    "/api/guest/assign-id",
    "/api/guest/login",
]

# 只读路由（无需认证）
# - /api/news/* (查询)
# - /api/analysis (查询)
# - /api/graph-rag/query, /api/graph-rag/info (查询)
# - /api/guest/messages (查询)
# - /api/health


api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


async def verify_csrf(request: Request):
    """
    CSRF 保护：验证 Origin/Referer 头与 CORS 允许的来源匹配。
    仅对公开写操作（/api/guest/login 等）生效。
    由于所有 POST 端点使用 JSON body，简单表单 CSRF 已被 Pydantic 验证阻断。
    """
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return

    origin = request.headers.get("origin") or request.headers.get("referer", "")
    if not origin:
        return  # 非浏览器客户端（curl 等）放行

    cors_origins = os.environ.get("CORS_ORIGINS", "")
    allowed = [o.strip().rstrip("/") for o in cors_origins.split(",") if o.strip()]

    if not allowed:
        return  # 未配置 CORS 来源时放行

    parsed = urlparse(origin)
    origin_normalized = f"{parsed.scheme}://{parsed.netloc}"
    for allowed_origin in allowed:
        if allowed_origin == "*" or origin_normalized == allowed_origin:
            return

    logger.warning("CSRF 校验失败: origin=%s, allowed=%s", origin, allowed)
    raise HTTPException(status_code=403, detail="跨站请求被拒绝")


def _is_protected_path(path: str) -> bool:
    """判断路径是否需要 API Key 认证"""
    for protected in PROTECTED_PATHS:
        if path == protected or path.startswith(protected + "/"):
            return True
    return False


def _is_public_write(path: str) -> bool:
    """判断路径是否为公开写操作（允许匿名但受频率限制）"""
    return path in PUBLIC_WRITE_PATHS


async def verify_api_key(request: Request, api_key: Optional[str] = Depends(api_key_header)):
    """
    验证 API Key
    仅对写操作/管理操作路由生效，读操作和公开路径直接放行
    """
    # 如果未开启认证，直接放行
    if not ENABLE_AUTH:
        return

    path = request.url.path

    # 只读路由和公开路径无需认证
    if not _is_protected_path(path):
        return

    # 需要认证的路由
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="未提供 API Key。请在请求头中设置: X-API-Key"
        )

    if api_key != API_KEY:
        raise HTTPException(
            status_code=403,
            detail="API Key 无效"
        )

    return api_key


async def optional_api_key(request: Request, api_key: Optional[str] = Depends(api_key_header)):
    """
    可选认证中间件
    用于读操作路由：提供 Key 则验证，不提供也放行
    """
    if not ENABLE_AUTH:
        return

    if api_key and api_key != API_KEY:
        raise HTTPException(
            status_code=403,
            detail="API Key 无效"
        )

    return api_key
