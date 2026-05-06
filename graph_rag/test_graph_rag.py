"""
GraphRAG模块测试脚本
"""
import sys
import os
import logging

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph_rag.llm_client import CloudLLMClient
from graph_rag.graph_builder import GraphBuilder
from graph_rag.rag_engine import GraphRAGEngine
from graph_rag.config import GraphRAGConfig
from backend.database.db_manager import db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_llm_client():
    """测试LLM客户端"""
    logger.info("测试LLM客户端...")
    if not GraphRAGConfig.is_llm_configured():
        logger.warning("未配置LLM_API_KEY，跳过LLM客户端测试")
        return True
    try:
        client = CloudLLMClient(
            api_type=GraphRAGConfig.LLM_API_TYPE,
            api_key=GraphRAGConfig.LLM_API_KEY,
            endpoint=GraphRAGConfig.LLM_API_ENDPOINT
        )
        logger.info("LLM客户端创建成功")
        return True
    except Exception as e:
        logger.error(f"LLM客户端测试失败: {e}")
        return False


def test_graph_builder():
    """测试图构建器"""
    logger.info("测试图构建器...")
    test_articles = [
        {
            'article_id': 'test_1',
            'title': '中国经济增长数据分析',
            'content': '中国经济在2023年表现出强劲的增长势头，GDP增长率达到5.2%，超出预期。政府采取了多项措施来刺激经济增长，包括减税降费和扩大内需。',
            'summary': '中国2023年GDP增长5.2%，政府采取措施刺激经济',
            'url': 'http://example.com/test1',
            'source': '新浪财经',
            'category': '宏观经济',
            'published_at': '2023-12-01T10:00:00Z',
            'author': '财经记者',
            'read_count': 1000,
            'comment_count': 50,
            'tags': ['经济', 'GDP', '增长']
        },
        {
            'article_id': 'test_2',
            'title': '股市表现分析',
            'content': 'A股市场在近期表现出明显的波动性，上证指数上涨2.3%，深证成指上涨3.1%。投资者对政策利好反应积极。',
            'summary': 'A股市场上涨，投资者反应积极',
            'url': 'http://example.com/test2',
            'source': '新浪财经',
            'category': '股市动态',
            'published_at': '2023-12-01T11:00:00Z',
            'author': '分析师',
            'read_count': 800,
            'comment_count': 30,
            'tags': ['股市', '市场', '指数']
        }
    ]
    try:
        client = CloudLLMClient(
            api_type="aliyun",
            api_key="test_key",
            endpoint="https://dashscope.aliyuncs.com/api/v1"
        ) if GraphRAGConfig.is_llm_configured() else None
        builder = GraphBuilder(client)
        graph = builder.build_entity_graph(test_articles)
        stats = builder.get_graph_statistics()
        logger.info(f"图构建测试完成: {stats}")
        return True
    except Exception as e:
        logger.error(f"图构建器测试失败: {e}")
        return False


def test_rag_engine():
    """测试RAG引擎"""
    logger.info("测试RAG引擎...")
    try:
        engine = GraphRAGEngine(None, db)
        info = engine.get_graph_info()
        logger.info(f"图信息测试完成: {info}")
        return True
    except Exception as e:
        logger.error(f"RAG引擎测试失败: {e}")
        return False


def main():
    logger.info("开始GraphRAG模块测试...")
    success = True
    success &= test_llm_client()
    success &= test_graph_builder()
    success &= test_rag_engine()
    if success:
        logger.info("所有测试通过!")
        return 0
    else:
        logger.error("部分测试失败!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
