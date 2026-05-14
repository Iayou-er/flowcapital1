"""Prometheus /metrics 端点"""
from fastapi import APIRouter, Response
from backend.monitoring.metrics import MULTIPROC_DIR, use_registry
from prometheus_client import generate_latest

metrics_router = APIRouter(tags=["监控"])


@metrics_router.get("/metrics")
async def metrics():
    if MULTIPROC_DIR:
        data = generate_latest()       # 多进程：从共享内存聚合所有 worker
    else:
        data = generate_latest(use_registry)
    return Response(content=data, media_type="text/plain")
