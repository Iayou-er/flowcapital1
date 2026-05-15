"""
GraphRAG相关API路由
"""
import os
import logging
from fastapi import APIRouter, HTTPException, Path, Depends
from typing import List, Dict, Any
from pydantic import BaseModel, Field

from backend.database.db_manager_async import db_async as db
from backend.analyzer.text_analyzer import TextAnalyzer
from graph_rag.rag_engine import GraphRAGEngine
from graph_rag.llm_client import CloudLLMClient
from backend.api.auth import verify_api_key

logger = logging.getLogger(__name__)

# 创建GraphRAG路由
graph_rag_router = APIRouter(prefix="/graph-rag", tags=["GraphRAG"])

# 懒初始化 GraphRAG 引擎
_graph_rag_engine = None
_graph_rag_init_error = None

def get_graph_rag_engine() -> GraphRAGEngine:
    global _graph_rag_engine, _graph_rag_init_error
    if _graph_rag_engine is None and _graph_rag_init_error is None:
        try:
            api_key = os.environ.get("LLM_API_KEY", "")
            api_endpoint = os.environ.get("LLM_API_ENDPOINT", "https://dashscope.aliyuncs.com/api/v1")
            api_type = os.environ.get("LLM_API_TYPE", "aliyun")
            if api_key:
                llm_client = CloudLLMClient(
                    api_type=api_type,
                    api_key=api_key,
                    endpoint=api_endpoint
                )
                _graph_rag_engine = GraphRAGEngine(llm_client, db)
            else:
                logger.warning("未配置LLM_API_KEY环境变量，GraphRAG引擎将以受限模式运行")
                _graph_rag_engine = GraphRAGEngine(None, db)
        except Exception as e:
            logger.error(f"初始化GraphRAG引擎失败: {e}")
            _graph_rag_init_error = str(e)
    return _graph_rag_engine

# Pydantic模型定义
class GraphQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="查询问题")
    k: int = Field(5, ge=1, le=20, description="返回结果数量")
    use_cache: bool = True
    from_date: str = None
    to_date: str = None

class GraphBuildRequest(BaseModel):
    limit: int = Field(100, ge=1, le=500, description="构建新闻数量限制")

class GraphInfoResponse(BaseModel):
    status: str
    stats: Dict[str, Any]
    last_build_time: str = None
    nodes: List[str] = []
    edges: List[tuple] = []

# 路由实现
@graph_rag_router.post("/query")
async def query_graph_rag(request: GraphQueryRequest, api_key: str = Depends(verify_api_key)):
    """使用GraphRAG进行查询"""
    engine = get_graph_rag_engine()
    if not engine:
        raise HTTPException(status_code=503, detail=f"GraphRAG引擎未就绪: {_graph_rag_init_error or '请配置LLM_API_KEY'}")
    try:
        result = await engine.query_graph(
            question=request.query,
            k=request.k,
            use_cache=request.use_cache,
            from_date=request.from_date,
            to_date=request.to_date,
        )
        return {"code": 0, "data": result}
    except Exception as e:
        logger.error(f"GraphRAG查询失败: {e}")
        raise HTTPException(status_code=500, detail="GraphRAG查询失败")


@graph_rag_router.post("/build-graph")
async def build_knowledge_graph(
    request: GraphBuildRequest,
    api_key: str = Depends(verify_api_key)
):
    """手动构建知识图谱"""
    engine = get_graph_rag_engine()
    if not engine:
        raise HTTPException(status_code=503, detail=f"GraphRAG引擎未就绪: {_graph_rag_init_error or '请配置LLM_API_KEY'}")
    try:
        graph = await engine.build_knowledge_graph(limit=request.limit)
        return {
            "code": 0,
            "data": {
                "message": "知识图谱构建完成",
                "graph_stats": engine.graph_builder.get_graph_statistics()
            }
        }
    except Exception as e:
        logger.error(f"知识图谱构建失败: {e}")
        raise HTTPException(status_code=500, detail="知识图谱构建失败")


@graph_rag_router.get("/info")
async def get_graph_info(api_key: str = Depends(verify_api_key)):
    """获取知识图谱信息"""
    engine = get_graph_rag_engine()
    if not engine:
        raise HTTPException(status_code=503, detail=f"GraphRAG引擎未就绪: {_graph_rag_init_error or '请配置LLM_API_KEY'}")
    try:
        info = engine.get_graph_info()
        return {"code": 0, "data": info}
    except Exception as e:
        logger.error(f"获取图谱信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取图谱信息失败")


@graph_rag_router.get("/entity/{entity_name}")
async def get_entity_info(entity_name: str = Path(..., min_length=1, max_length=100)):
    """获取特定实体信息"""
    engine = get_graph_rag_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="GraphRAG引擎未就绪")
    try:
        info = engine.get_entity_info(entity_name)
        return {"code": 0, "data": info}
    except Exception as e:
        logger.error(f"获取实体信息失败: {e}")
        raise HTTPException(status_code=500, detail="获取实体信息失败")


class EntityTimelineRequest(BaseModel):
    entity: str = Field(..., min_length=1, max_length=100, description="实体名称")
    days: int = Field(7, ge=1, le=90, description="时间窗口（天）")


@graph_rag_router.post("/entity-timeline")
async def entity_timeline(request: EntityTimelineRequest, api_key: str = Depends(verify_api_key)):
    """获取实体时间线摘要"""
    engine = get_graph_rag_engine()
    if not engine:
        raise HTTPException(status_code=503, detail="GraphRAG引擎未就绪")
    try:
        result = await engine.build_entity_timeline(request.entity, days=request.days)
        if 'error' in result:
            return {"code": 1, "data": result}
        return {"code": 0, "data": result}
    except Exception as e:
        logger.error(f"实体时间线查询失败: {e}")
        raise HTTPException(status_code=500, detail="实体时间线查询失败")


@graph_rag_router.post("/query-test")
async def query_test(api_key: str = Depends(verify_api_key)):
    """简单测试接口"""
    return {
        "code": 0,
        "data": {
            "message": "GraphRAG测试接口正常工作",
            "api_status": "connected" if get_graph_rag_engine() else "disconnected"
        }
    }