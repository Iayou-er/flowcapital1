"""
GraphRAG 配置模块
从环境变量读取 LLM 和 GraphRAG 配置
"""
import os


class GraphRAGConfig:
    LLM_API_TYPE = os.environ.get("LLM_API_TYPE", "aliyun")
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
    LLM_API_ENDPOINT = os.environ.get(
        "LLM_API_ENDPOINT",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    )
    LLM_MODEL = os.environ.get("LLM_MODEL", "qwen-plus")

    @staticmethod
    def is_llm_configured() -> bool:
        return bool(GraphRAGConfig.LLM_API_KEY)
