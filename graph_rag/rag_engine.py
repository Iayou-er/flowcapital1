"""
GraphRAG引擎模块
实现知识图谱检索增强生成
"""
import networkx as nx
import logging
from typing import List, Dict, Any
from datetime import datetime
from collections import Counter

from graph_rag.llm_client import CloudLLMClient
from graph_rag.graph_builder import GraphBuilder

logger = logging.getLogger(__name__)

class GraphRAGEngine:
    """GraphRAG引擎"""

    def __init__(self, llm_client: CloudLLMClient, db_manager, cache_ttl: int = 3600):
        self.llm_client = llm_client
        self.db_manager = db_manager
        self.graph_builder = GraphBuilder(llm_client)
        self.graph = None
        self.cache_ttl = cache_ttl  # 缓存过期时间（秒）
        self._last_build_time = None
        self._cache: Dict[str, Any] = {}  # 简单内存缓存
        self._cache_max_size = 100

    def build_knowledge_graph(self, limit: int = 100) -> nx.DiGraph:
        """
        构建知识图谱

        Args:
            limit: 获取新闻数量限制

        Returns:
            构建好的知识图谱
        """
        logger.info("开始构建知识图谱...")

        try:
            # 从数据库获取最新新闻
            news_list = self.db_manager.get_latest_news(limit=limit)
            logger.info(f"获取到 {len(news_list)} 篇新闻用于图谱构建")

            # 构建图
            self.graph = self.graph_builder.build_entity_graph(news_list)
            self._last_build_time = datetime.now()

            logger.info(f"知识图谱构建完成: {self.graph_builder.get_graph_statistics()}")
            return self.graph

        except Exception as e:
            logger.error(f"知识图谱构建失败: {e}")
            raise

    def query_graph(self, question: str, k: int = 5, use_cache: bool = True) -> Dict[str, Any]:
        """
        使用GraphRAG进行查询

        Args:
            question: 问题查询
            k: 返回相关度最高的k个结果
            use_cache: 是否使用缓存

        Returns:
            查询结果
        """
        # 检查缓存
        if use_cache:
            cache_key = f"query_{hash(question)}_{k}"
            cached_result = self._cache.get(cache_key)
            if cached_result and (datetime.now() - cached_result.get('timestamp', datetime.now())).seconds < self.cache_ttl:
                logger.info("使用缓存结果")
                return cached_result['data']

        try:
            # 确保图存在
            if not self.graph:
                logger.info("知识图谱不存在，正在构建...")
                self.build_knowledge_graph(limit=50)  # 构建50篇新闻的图谱

            # 1. 在图中搜索相关的实体和路径
            relevant_entities = self._find_relevant_entities(question)
            logger.info(f"找到相关实体数量: {len(relevant_entities)}")

            # 2. 获取相关上下文
            context = self._collect_context(question, relevant_entities)
            logger.info(f"收集到上下文长度: {len(context)}")

            # 3. 使用LLM处理和回答
            result = self._generate_answer(question, context)

            # 4. 添加结果统计信息
            result.update({
                'graph_stats': self.graph_builder.get_graph_statistics(),
                'query_time': datetime.now().isoformat(),
                'relevant_entities': relevant_entities[:20],  # 限制返回实体数量
                'retrieval_count': len(relevant_entities)
            })

            # 缓存结果
            if use_cache:
                if len(self._cache) >= self._cache_max_size:
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                self._cache[cache_key] = {
                    'data': result,
                    'timestamp': datetime.now()
                }

            logger.info("GraphRAG查询完成")
            return result

        except Exception as e:
            logger.error(f"GraphRAG查询失败: {e}")
            return {
                'answer': f"查询失败: {str(e)}",
                'error': str(e),
                'related_entities': [],
                'context': '',
                'graph_stats': self.graph_builder.get_graph_statistics() if self.graph else {}
            }

    def _find_relevant_entities(self, question: str) -> List[str]:
        """在图中查找相关实体"""
        try:
            # 简单实现：从图中提取所有节点作为备用
            all_nodes = list(self.graph.nodes()) if self.graph else []

            # 通过LLM理解问题并提取关键词相关的实体
            if len(all_nodes) > 0 and self.llm_client:
                prompt = f"""
                请根据以下问题，从知识图谱的实体中找出最相关的几个实体:

                问题: {question}
                实体列表: {', '.join(all_nodes[:20])}  # 限制显示数量

                请以列表格式返回最相关的实体(每行一个实体):
                """

                try:
                    response = self.llm_client.generate_structured(
                        prompt,
                        max_tokens=512,
                        temperature=0.5
                    )
                    response_text = response.get('choices', [{}])[0].get('message', {}).get('content', '')
                    entities = [e.strip() for e in response_text.split('\n') if e.strip() and len(e.strip()) > 1]
                    return entities[:20]  # 限制返回实体数量
                except:
                    # 如果LLM调用失败，使用简单关键词匹配
                    pass

            # 如果失败，返回图中的前20个实体
            return all_nodes[:20]

        except Exception as e:
            logger.error(f"实体查找失败: {e}")
            # 返回图中的全部实体
            return list(self.graph.nodes())[:20] if self.graph else []

    def _collect_context(self, question: str, relevant_entities: List[str]) -> str:
        """收集图中实体的相关上下文"""
        try:
            if not self.graph:
                return "没有可用的图谱数据"

            context_parts = []

            # 获取实体的直接连接节点（邻居）
            for entity in relevant_entities[:10]:  # 限制实体数量
                try:
                    # 获取邻接节点
                    neighbors = list(self.graph.neighbors(entity))
                    # 获取实体属性
                    node_data = self.graph.nodes.get(entity, {})

                    if node_data:
                        entity_context = f"实体 '{entity}' 的相关信息:\n"
                        # 遍历连接的节点
                        for neighbor in neighbors[:5]:  # 限制邻居数量
                            neighbor_data = self.graph.nodes.get(neighbor, {})
                            entity_context += f"  - 和 '{neighbor}' 的关系: {self.graph.get_edge_data(entity, neighbor, {}).get('relation', '关联')}\n"
                        context_parts.append(entity_context)
                except:
                    continue

            # 从数据库获取更多上下文信息
            try:
                # 获取这些实体相关联的新闻
                contextual_news = []
                if self.db_manager and len(relevant_entities) > 0:
                    # 获取前几条新闻用于上下文
                    news_list = self.db_manager.get_latest_news(limit=5)
                    for news in news_list:
                        # 检查新闻是否包含相关实体
                        news_content = (news.get('title', '') + ' ' + news.get('content', '')).lower()
                        if any(entity.lower() in news_content for entity in relevant_entities):
                            contextual_news.append(news)

                    # 从相关新闻中提取上下文
                    for news in contextual_news:
                        news_text = f"新闻标题: {news.get('title', '')}\n"
                        news_text += f"新闻摘要: {news.get('summary', '')}\n"
                        context_parts.append(news_text)

            except Exception as e:
                logger.warning(f"获取文本上下文时出错: {e}")

            return "\n\n".join(context_parts[:3])  # 限制上下文数量

        except Exception as e:
            logger.error(f"收集上下文失败: {e}")
            return "上下文获取失败"

    def _generate_answer(self, question: str, context: str) -> Dict[str, Any]:
        """生成最终答案"""
        try:
            # 构建提示词
            prompt = f"""
            请基于以下提供的上下文回答问题:

            上下文:
            {context}

            问题:
            {question}

            请以专业、准确的方式回答，并在回答后简要说明依据的上下文。回答需要是中文，且包含在200字以内。
            """

            # 调用API获取答案
            response = self.llm_client.generate_structured(
                prompt,
                max_tokens=512,
                temperature=0.5
            )

            # 解析响应
            answer = response.get('choices', [{}])[0].get('message', {}).get('content', '').strip()

            return {
                'question': question,
                'answer': answer,
                'context_used': context[:500] + "..." if len(context) > 500 else context,
                'confidence': 0.8  # 简单的置信度估计
            }

        except Exception as e:
            logger.error(f"答案生成失败: {e}")
            return {
                'question': question,
                'answer': f"无法生成答案: {str(e)}",
                'context_used': context,
                'confidence': 0.0
            }

    def get_graph_info(self) -> Dict[str, Any]:
        """获取图信息"""
        if not self.graph:
            return {
                'status': 'not_built',
                'stats': {}
            }
        return {
            'status': 'built',
            'stats': self.graph_builder.get_graph_statistics(),
            'last_build_time': self._last_build_time.isoformat() if self._last_build_time else None,
            'nodes': list(self.graph.nodes())[:20],  # 只返回部分节点
            'edges': list(self.graph.edges())[:10]   # 只返回部分边
        }

    def get_entity_info(self, entity: str) -> Dict[str, Any]:
        """获取实体信息"""
        if not self.graph:
            return {'error': 'Graph not built'}

        try:
            node_data = self.graph.nodes.get(entity, {})
            edges_data = list(self.graph.edges(entity, data=True))

            # 获取相关实体信息
            related_entities = []
            for edge in edges_data[:5]:  # 限制结果数量
                related_entities.append({
                    'entity': edge[1],
                    'relation': edge[2].get('relation', 'related'),
                    'description': edge[2].get('description', '')
                })

            return {
                'entity': entity,
                'attributes': node_data,
                'related_entities': related_entities,
                'in_degree': self.graph.in_degree(entity),
                'out_degree': self.graph.out_degree(entity)
            }

        except Exception as e:
            return {'error': str(e)}