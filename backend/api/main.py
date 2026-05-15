"""
FastAPI 应用主入口
"""
import os
import logging
import time
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    project_root = Path(__file__).resolve().parent.parent.parent
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file)
        print(f"已加载环境变量: {env_file}")
    else:
        print(f"未找到 .env 文件，仅使用系统环境变量")
except ImportError:
    print("未安装 python-dotenv，仅使用系统环境变量 (pip install python-dotenv)")

from backend.api.routes import news, analysis, graph_rag, guest_book, events, metrics
from backend.database.db_manager_async import db_async as db
from backend.database.redis_client import redis_client
from backend.middleware.rate_limiter import rate_limiter, RATE_LIMIT_CONFIG, DEFAULT_RATE_LIMIT
from backend.middleware.security_headers import SecurityHeadersMiddleware

_cleanup_task: asyncio.Task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时启动定时清理任务，关闭时取消"""
    global _cleanup_task

    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(300)  # 每 5 分钟
            try:
                rate_limiter.cleanup()
            except Exception:
                logging.getLogger(__name__).warning("频率限制器清理失败", exc_info=True)

    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    yield
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass


_start_time = time.time()

app = FastAPI(
    title="经济新闻分析系统 API",
    description="经济新闻爬取、分析和知识图谱检索系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置 — 生产环境仅允许前端域名，方法限制为 GET/POST/OPTIONS
CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:3001"
)
cors_origins_list = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]

# 生产环境禁用通配符（开发环境允许）
IS_PRODUCTION = os.environ.get("ENVIRONMENT", "development").lower() == "production"
if IS_PRODUCTION and cors_origins_list == ["*"]:
    cors_origins_list = ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)
app.add_middleware(GZipMiddleware, minimum_size=500)  # >500字节自动压缩
app.add_middleware(SecurityHeadersMiddleware)        # 安全响应头


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """HTTP 指标中间件 — 记录请求数和耗时到 Prometheus"""
    import time as _time
    t0 = _time.time()
    response = await call_next(request)
    duration = (_time.time() - t0) * 1000
    try:
        from backend.monitoring.metrics import HTTP_REQUESTS, HTTP_DURATION
        path = request.scope.get("route", {}).path or request.url.path
        HTTP_REQUESTS.labels(
            method=request.method, path=path, status_code=str(response.status_code)
        ).inc()
        HTTP_DURATION.labels(method=request.method, path=path).observe(duration)
    except Exception:
        logging.getLogger('api.metrics').warning("指标记录失败", exc_info=True)
    return response


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """API 访问日志"""
    import time as _time
    t0 = _time.time()
    response = await call_next(request)
    duration = (_time.time() - t0) * 1000
    logging.getLogger('api.access').info(
        '%s %s %s %s %.0fms',
        request.client.host if request.client else "-",
        request.method, request.url.path,
        response.status_code, duration
    )
    return response


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """HTTP 频率限制中间件 — 基于客户端 IP"""
    client_ip = request.client.host if request.client else "unknown"
    path = request.url.path

    # 查找匹配的频率限制配置（精确匹配优先）
    limit, window = RATE_LIMIT_CONFIG.get(path, DEFAULT_RATE_LIMIT)

    if not rate_limiter.is_allowed(client_ip, limit=limit, window=window):
        remaining = 0
        retry_after = rate_limiter.retry_after(client_ip, window=window)
        return JSONResponse(
            status_code=429,
            content={
                "code": 429,
                "data": None,
                "message": "请求过于频繁，请稍后再试"
            },
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": str(remaining),
                "Retry-After": str(retry_after),
            }
        )

    # 正常处理，附加频率限制头
    response = await call_next(request)
    remaining = rate_limiter.remaining(client_ip, limit=limit, window=window)
    response.headers["X-RateLimit-Limit"] = str(limit)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


# 定期清理过期记录（每 5 分钟）
app.include_router(news.news_router, prefix="/api")
app.include_router(analysis.analysis_router, prefix="/api")
app.include_router(graph_rag.graph_rag_router, prefix="/api")
app.include_router(guest_book.guest_book_router, prefix="/api")
app.include_router(events.events_router, prefix="/api")
app.include_router(metrics.metrics_router, prefix="")  # /metrics 不需要 /api 前缀


# ── 管理后台 ──
from fastapi import APIRouter, Depends
from backend.api.auth import verify_api_key

admin_router = APIRouter(prefix="/api/admin", tags=["管理"], dependencies=[Depends(verify_api_key)])


@admin_router.get("/status")
async def admin_status():
    """系统状态面板"""
    status = {
        "service": "FlowCapital",
        "version": "1.0.0",
        "uptime_seconds": int(time.time() - _start_time),
        "database": {},
        "search": {},
        "news": {},
        "analysis": {},
    }
    try:
        row = await db.query_one("SELECT COUNT(*), MAX(created_at), MAX(published_at) FROM news_articles")
        status["news"] = {
            "total": row[0] if row else 0,
            "last_crawled": row[1] or '',
            "latest_published": row[2] or '',
        }
        row2 = await db.query_one("SELECT COUNT(*) FROM analysis_results")
        status["analysis"] = {"total_analyzed": row2[0] if row2 else 0}
        status["database"]["status"] = "connected"
    except Exception as e:
        status["database"] = {"status": "error", "error": str(e)}

    try:
        from backend.analyzer.search_engine import _get_index
        idx = _get_index()
        status["search"] = {"indexed_docs": idx.doc_count() if idx else 0, "status": "ready"}
    except Exception as e:
        status["search"] = {"status": "error", "error": str(e)}

    # 磁盘使用
    try:
        import os, shutil
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
        total, used, free = shutil.disk_usage(data_dir)
        status["disk"] = {
            "total_gb": round(total / 1024**3, 1),
            "used_gb": round(used / 1024**3, 1),
            "free_gb": round(free / 1024**3, 1),
        }
    except Exception:
        logging.getLogger(__name__).warning("管理状态: 磁盘使用查询失败", exc_info=True)
        status["disk"] = {"status": "unavailable"}

    return {"code": 0, "data": status}


app.include_router(admin_router)

logger = logging.getLogger(__name__)


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    """统一 HTTP 异常返回格式 — 生产环境隐藏错误详情"""
    if IS_PRODUCTION and exc.status_code >= 500:
        detail = "服务器内部错误，请稍后重试"
    else:
        detail = exc.detail
    if exc.status_code >= 500:
        logger.error("HTTP %d: %s", exc.status_code, exc.detail, exc_info=True)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.status_code,
            "data": None,
            "message": detail
        }
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """参数校验错误格式化 — 生产环境隐藏字段细节"""
    if IS_PRODUCTION:
        return JSONResponse(
            status_code=422,
            content={
                "code": 422,
                "data": None,
                "message": "请求参数格式错误"
            }
        )
    errors = []
    for error in exc.errors():
        field = " -> ".join(str(loc) for loc in error["loc"])
        errors.append(f"{field}: {error['msg']}")
    return JSONResponse(
        status_code=422,
        content={
            "code": 422,
            "data": None,
            "message": "; ".join(errors)
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """兜底全局异常处理 — 生产环境只返回通用消息"""
    logger.error("未捕获异常: %s", exc, exc_info=True)
    if IS_PRODUCTION:
        return JSONResponse(
            status_code=500,
            content={
                "code": 500,
                "data": None,
                "message": "服务器内部错误，请稍后重试"
            }
        )
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "data": None,
            "message": f"服务器内部错误: {type(exc).__name__}"
        }
    )


@app.get("/api/health")
async def health_check():
    """健康检查接口"""
    # 检查 SQLite
    db_ok = False
    try:
        row = await db.query_one("SELECT 1")
        db_ok = row is not None
    except Exception:
        logging.getLogger(__name__).warning("健康检查: 数据库连接失败", exc_info=True)

    # 检查 Whoosh
    whoosh_ok = False
    try:
        from backend.analyzer.search_engine import _get_index
        idx = _get_index()
        whoosh_ok = idx is not None
    except Exception:
        logging.getLogger(__name__).warning("健康检查: Whoosh 索引不可用", exc_info=True)

    # 数据库统计
    news_count = 0
    try:
        row = await db.query_one("SELECT COUNT(*) FROM news_articles")
        news_count = row[0] if row else 0
    except Exception:
        logging.getLogger(__name__).warning("健康检查: 统计查询失败", exc_info=True)

    return {
        "status": "ok" if (db_ok and whoosh_ok) else "degraded",
        "service": "经济新闻分析系统",
        "version": "1.0.0",
        "uptime_seconds": int(time.time() - _start_time),
        "metrics": {
            "news_count": news_count,
        },
        "checks": {
            "sqlite": "connected" if db_ok else "disconnected",
            "redis": "connected" if await redis_client.is_available() else "not_configured",
            "whoosh": "ready" if whoosh_ok else "not_ready",
        }
    }
