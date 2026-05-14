"""
GraphRAG引擎模块
实现知识图谱检索增强生成
支持增量更新、图持久化、实体时间线、时间范围QA
"""
import networkx as nx
import hashlib
import pickle
import logging
import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

from graph_rag.llm_client import CloudLLMClient
from graph_rag.graph_builder import GraphBuilder

logger = logging.getLogger(__name__)

GRAPH_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'knowledge_graph.pkl')


class GraphRAGEngine:
    """GraphRAG引擎"""

    def __init__(self, llm_client: CloudLLMClient, db_manager, cache_ttl: int = 3600):
        self.llm_client = llm_client
        self.db_manager = db_manager
        self.graph_builder = GraphBuilder(llm_client)
        self.graph = None
        self.cache_ttl = cache_ttl  # 缓存过期时间（秒）
        self._last_build_time: Optional[datetime] = None
        self._cache: Dict[str, Any] = {}  # 简单内存缓存
        self._cache_max_size = 100

        # 尝试从磁盘加载已有图谱
        if not self.load_graph() and llm_client:
            logger.info("无已有图谱，将在首次查询或构建时创建")

    # ── 图持久化 ──

    def save_graph(self):
        """将图谱序列化到磁盘"""
        if self.graph is None:
            return
        os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
        with open(GRAPH_PATH, 'wb') as f:
            pickle.dump(self.graph, f)
        logger.info(f"图谱已保存: {self.graph.number_of_nodes()} 节点, {self.graph.number_of_edges()} 边")

    def load_graph(self) -> bool:
        """从磁盘加载图谱"""
        if os.path.exists(GRAPH_PATH):
            try:
                with open(GRAPH_PATH, 'rb') as f:
                    self.graph = pickle.load(f)
                self._last_build_time = datetime.now()
                # 重建 norm_index
                self.graph_builder.graph = self.graph
                self.graph_builder._rebuild_norm_index()
                logger.info(f"图谱已加载: {self.graph.number_of_nodes()} 节点, {self.graph.number_of_edges()} 边")
                return True
            except Exception as e:
                logger.warning(f"图谱加载失败: {e}")
                self.graph = None
        return False

    # ── 知识图谱构建 ──

    async def build_knowledge_graph(self, limit: int = 100) -> nx.DiGraph:
        """
        构建知识图谱

        Args:
            limit: 获取新闻数量限制

        Returns:
            构建好的知识图谱
        """
        logger.info("开始构建知识图谱...")

        try:
            news_list, total = await self.db_manager.get_latest_news(limit=limit)
            logger.info(f"获取到 {len(news_list)}/{total} 篇新闻用于图谱构建")

            self.graph = self.graph_builder.build_entity_graph(news_list)
            self._last_build_time = datetime.now()
            self.graph_builder._rebuild_norm_index()

            logger.info(f"知识图谱构建完成: {self.graph_builder.get_graph_statistics()}")
            self.save_graph()
            return self.graph

        except Exception as e:
            logger.error(f"知识图谱构建失败: {e}")
            raise

    # ── 增量更新 ──

    async def incremental_update(self, articles: list) -> int:
        """
        增量更新知识图谱：仅处理新增/变更文章，追加节点和边。
        通过 content_hash 检测内容变更，变更时删除旧节点后重新提取。

        Returns:
            实际处理的文章数
        """
        if not self.graph:
            await self.build_knowledge_graph(limit=50)
            return 0

        # 构建 {article_id: content_hash}
        article_map = {}
        for a in articles:
            aid = a.get('article_id', '')
            if not aid:
                continue
            content = (a.get('title', '') + a.get('content', '') + a.get('summary', ''))
            article_map[aid] = hashlib.md5(content.encode()).hexdigest()

        # 扫描图中已有 article_id → content_hash
        existing = {}  # article_id → {content_hash, nodes}
        for node in list(self.graph.nodes()):
            aid = self.graph.nodes[node].get('article_id', '')
            ch = self.graph.nodes[node].get('content_hash', '')
            if aid and ch:
                if aid not in existing:
                    existing[aid] = {'content_hash': ch, 'nodes': []}
                existing[aid]['nodes'].append(node)

        # 分类：新文章 vs 内容更新
        new_articles = []
        for a in articles:
            aid = a.get('article_id', '')
            if not aid or aid not in article_map:
                continue
            new_hash = article_map[aid]
            old = existing.get(aid)
            if old is None:
                new_articles.append(a)
            elif old['content_hash'] != new_hash:
                logger.info(f"文章内容更新，重新提取实体: {a.get('title', '')}")
                for node_name in old['nodes']:
                    self.graph.remove_node(node_name)
                new_articles.append(a)

        if not new_articles:
            return 0

        for article in new_articles:
            try:
                aid = article.get('article_id', '')
                if aid in article_map:
                    article['content_hash'] = article_map[aid]
                entities = self.graph_builder._extract_entities(article)
                relationships = self.graph_builder._extract_relationships(article, entities)
                self.graph_builder._add_to_graph(article, entities, relationships)
            except Exception as e:
                logger.warning(f"文章实体提取失败: {article.get('title', '')}: {e}")
                continue

        self._last_build_time = datetime.now()
        self.save_graph()
        return len(new_articles)

    # ── 查询 ──

    async def query_graph(self, question: str, k: int = 5, use_cache: bool = True,
                    from_date: str = None, to_date: str = None) -> Dict[str, Any]:
        """
        使用GraphRAG进行查询（支持时间范围参数）

        Args:
            question: 问题查询
            k: 返回相关度最高的k个结果
            use_cache: 是否使用缓存
            from_date: 起始日期 (YYYY-MM-DD)
            to_date: 截止日期 (YYYY-MM-DD)

        Returns:
            查询结果
        """
        if use_cache:
            cache_key = f"query_{hash(question)}_{k}_{from_date}_{to_date}"
            cached_result = self._cache.get(cache_key)
            if cached_result and (datetime.now() - cached_result.get('timestamp', datetime.now())).seconds < self.cache_ttl:
                logger.info("使用缓存结果")
                return cached_result['data']

        try:
            if not self.graph:
                logger.info("知识图谱不存在，正在构建...")
                await self.build_knowledge_graph(limit=50)

            relevant_entities = self._find_relevant_entities(question)
            logger.info(f"找到相关实体数量: {len(relevant_entities)}")

            context = await self._collect_context(question, relevant_entities,
                                             from_date=from_date, to_date=to_date)
            logger.info(f"收集到上下文长度: {len(context)}")

            result = self._generate_answer(question, context,
                                            from_date=from_date, to_date=to_date)

            result.update({
                'graph_stats': self.graph_builder.get_graph_statistics(),
                'query_time': datetime.now().isoformat(),
                'relevant_entities': relevant_entities[:20],
                'retrieval_count': len(relevant_entities)
            })

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
            all_nodes = list(self.graph.nodes()) if self.graph else []

            if len(all_nodes) > 0 and self.llm_client:
                prompt = f"""
                请根据以下问题，从知识图谱的实体中找出最相关的几个实体:

                问题: {question}
                实体列表: {', '.join(all_nodes[:20])}

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
                    return entities[:20]
                except Exception:
                    pass

            return all_nodes[:20]

        except Exception as e:
            logger.error(f"实体查找失败: {e}")
            return list(self.graph.nodes())[:20] if self.graph else []

    async def _collect_context(self, question: str, relevant_entities: List[str],
                         from_date: str = None, to_date: str = None) -> str:
        """收集图中实体的相关上下文（含时间筛选）"""
        try:
            if not self.graph:
                return "没有可用的图谱数据"

            context_parts = []

            for entity in relevant_entities[:10]:
                try:
                    neighbors = list(self.graph.neighbors(entity))
                    node_data = self.graph.nodes.get(entity, {})

                    if node_data:
                        entity_context = f"实体 '{entity}' 的相关信息:\n"
                        for neighbor in neighbors[:5]:
                            neighbor_data = self.graph.nodes.get(neighbor, {})
                            entity_context += f"  - 和 '{neighbor}' 的关系: {self.graph.get_edge_data(entity, neighbor, {}).get('relation', '关联')}\n"
                        context_parts.append(entity_context)
                except Exception:
                    continue

            if self.db_manager and len(relevant_entities) > 0:
                news_list, _ = await self.db_manager.get_latest_news(limit=20)
                for news in news_list:
                    pub_at = news.get('published_at', '')
                    if from_date and pub_at < from_date:
                        continue
                    if to_date and pub_at > to_date:
                        continue

                    news_content = (news.get('title', '') + ' ' + news.get('content', '')).lower()
                    if any(entity.lower() in news_content for entity in relevant_entities):
                        news_text = f"新闻标题: {news.get('title', '')}\n"
                        news_text += f"新闻摘要: {news.get('summary', '')}\n"
                        context_parts.append(news_text)

            return "\n\n".join(context_parts[:3])

        except Exception as e:
            logger.error(f"收集上下文失败: {e}")
            return "上下文获取失败"

    def _generate_answer(self, question: str, context: str,
                         from_date: str = None, to_date: str = None) -> Dict[str, Any]:
        """生成最终答案（含时间维度提示）"""
        try:
            prompt = f"""
上下文时间范围: {from_date or '不限'} ~ {to_date or '不限'}
上下文:
{context}

问题: {question}

如果问题涉及趋势或变化，请按时间线描述；否则直接回答。回答需要是中文，200 字以内。
            """

            response = self.llm_client.generate_structured(
                prompt,
                max_tokens=512,
                temperature=0.5
            )

            answer = response.get('choices', [{}])[0].get('message', {}).get('content', '').strip()

            return {
                'question': question,
                'answer': answer,
                'context_used': context[:500] + "..." if len(context) > 500 else context,
                'confidence': 0.8
            }

        except Exception as e:
            logger.error(f"答案生成失败: {e}")
            return {
                'question': question,
                'answer': f"无法生成答案: {str(e)}",
                'context_used': context,
                'confidence': 0.0
            }

    # ── 实体时间线 ──

    async def build_entity_timeline(self, entity: str, days: int = 7) -> dict:
        """
        对某个实体的近期所有引用文章做 LLM 摘要合并，
        输出该实体在时间窗口内的关键变化。
        """
        if not self.graph:
            return {'error': '知识图谱未构建'}

        # 归一化查找
        norm_entity = GraphBuilder._normalize_entity_name(entity)
        matched = None
        for node in self.graph.nodes():
            if GraphBuilder._normalize_entity_name(node) == norm_entity:
                matched = node
                break

        if not matched:
            return {'error': f'实体 {entity} 不存在'}

        node = self.graph.nodes[matched]
        ref_ids = node.get('ref_articles', [])
        articles = await self.db_manager.get_news_by_article_ids(ref_ids)
        cutoff = datetime.now() - timedelta(days=days)
        articles = [
            a for a in articles
            if a and self._within_days(a.get('published_at', ''), cutoff)
        ]
        articles.sort(key=lambda a: a.get('published_at', ''))

        if not articles:
            return {'entity': entity, 'summary': '', 'article_count': 0, 'days': days}
        if len(articles) == 1:
            return {
                'entity': entity,
                'summary': articles[0].get('summary', '') or articles[0].get('title', ''),
                'article_count': 1,
                'days': days,
            }

        prompt = f"""实体: {entity}
时间范围: 最近 {days} 天
相关文章 ({len(articles)} 篇):

{self._format_articles(articles)}

请对该实体在这段时间内的变化趋势做一个不超过 200 字的摘要，聚焦：
1. 关键事件（时间序列）
2. 情感走向（正面→负面 or 负面→正面）
3. 核心结论"""

        summary = self.llm_client.generate_response(prompt, max_tokens=300)
        return {
            'entity': entity,
            'summary': summary,
            'article_count': len(articles),
            'days': days,
        }

    @staticmethod
    def _within_days(date_str: str, cutoff: datetime) -> bool:
        """判断日期是否在 cutoff 之后"""
        if not date_str:
            return False
        dt = GraphRAGEngine._parse_date_to_datetime(date_str)
        return dt is not None and dt >= cutoff

    @staticmethod
    def _parse_date_to_datetime(date_str: str) -> Optional[datetime]:
        """统一解析 ISO 和空格分隔两种日期格式"""
        try:
            s = date_str.strip()[:19]
            if 'T' in s:
                return datetime.fromisoformat(s)
            else:
                return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _format_articles(articles: list) -> str:
        """格式化文章列表为 prompt 文本"""
        lines = []
        for a in articles[:20]:
            title = a.get('title', '')
            pub_at = a.get('published_at', '')[:10]
            summary = (a.get('summary', '') or a.get('content', ''))[:100]
            lines.append(f"- [{pub_at}] {title}: {summary}")
        return '\n'.join(lines)

    # ── 图信息 ──

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
            'nodes': list(self.graph.nodes())[:20],
            'edges': list(self.graph.edges())[:10]
        }

    def get_entity_info(self, entity: str) -> Dict[str, Any]:
        """获取实体信息"""
        if not self.graph:
            return {'error': 'Graph not built'}

        try:
            node_data = self.graph.nodes.get(entity, {})
            edges_data = list(self.graph.edges(entity, data=True))

            related_entities = []
            for edge in edges_data[:5]:
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
