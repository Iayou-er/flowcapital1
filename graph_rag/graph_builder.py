"""
图构建器模块
将新闻内容转换为知识图谱
支持实体名称归一化和多文章实体合并
"""
import networkx as nx
import json
import re
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from graph_rag.llm_client import CloudLLMClient

logger = logging.getLogger(__name__)

class GraphBuilder:
    """图构建器 - 将新闻内容转换为知识图谱"""

    def __init__(self, llm_client: CloudLLMClient, max_workers: int = 4):
        self.graph = nx.DiGraph()
        self.llm_client = llm_client
        self.max_workers = max_workers
        self._norm_index: Dict[str, str] = {}  # {归一化名称: 原始名称}

    @staticmethod
    def _normalize_entity_name(name: str) -> str:
        """实体名称归一化：去除常见后缀/前缀差异"""
        name = re.sub(r'[（(][^)）]*[)）]', '', name)
        for suffix in ['公司', '有限公司', '股份有限公司', '集团', '科技']:
            if name.endswith(suffix) and len(name) > len(suffix) + 1:
                name = name[:-len(suffix)]
                break
        return name.strip()

    def _rebuild_norm_index(self):
        """从当前图的所有节点重建归一化索引。在 load_graph 或 build_knowledge_graph 后调用。"""
        self._norm_index.clear()
        for node in self.graph.nodes():
            norm = self._normalize_entity_name(node)
            if norm not in self._norm_index:
                self._norm_index[norm] = node

    def build_entity_graph(self, articles: List[Dict]) -> nx.DiGraph:
        """
        从新闻文章构建实体关系图

        Args:
            articles: 新闻文章列表

        Returns:
            构建好的知识图谱
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        logger.info(f"开始构建包含 {len(articles)} 篇文章的知识图谱")

        # 并行提取每篇文章的实体+关系
        def _process_article(article):
            try:
                entities = self._extract_entities(article)
                relationships = self._extract_relationships(article, entities)
                return (article, entities, relationships, None)
            except Exception as e:
                return (article, [], [], e)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_process_article, a): i for i, a in enumerate(articles)}
            for future in as_completed(futures):
                article, entities, relationships, error = future.result()
                if error:
                    logger.error(f"处理文章 {article.get('title', 'unknown')} 时出错: {error}")
                else:
                    self._add_to_graph(article, entities, relationships)

        self._rebuild_norm_index()
        logger.info(f"知识图谱构建完成，包含 {self.graph.number_of_nodes()} 个节点和 {self.graph.number_of_edges()} 条边")
        return self.graph

    def _extract_entities(self, article: Dict) -> List[str]:
        """提取文章中的实体"""
        try:
            entities_text = """
您是专业的文本分析专家，请从以下新闻内容中提取重要的经济实体：

文章标题：{title}
文章内容：{content}

请以列表格式返回提取的实体（每行一个实体，只返回实体名称，不要说明文字）：
""".format(
                title=article.get('title', ''),
                content=article.get('content', '')
            )

            response = self.llm_client.generate_structured(
                entities_text,
                max_tokens=1024,
                temperature=0.3
            )

            # 解析响应并返回实体列表
            entities_response = response.get('choices', [{}])[0].get('message', {}).get('content', '')
            entities = [e.strip() for e in entities_response.split('\n') if e.strip()]

            # 清理实体，去除关键信息中的特殊字符
            clean_entities = []
            for entity in entities:
                # 过滤掉形容词等非实体词
                if len(entity) > 1 and not any(char in entity for char in ['是', '的', '了', '在', '和', '与', '被', '为']):
                    clean_entities.append(entity)

            logger.debug(f"从文章中提取了 {len(clean_entities)} 个实体")
            return clean_entities[:50]  # 限制实体数量

        except Exception as e:
            logger.error(f"实体提取失败: {e}")
            return []

    def _extract_relationships(self, article: Dict, entities: List[str]) -> List[Dict]:
        """提取实体间的关系"""
        try:
            # 如果实体数量少于2，无法提取关系
            if len(entities) < 2:
                return []

            relationships_text = """
您是专业的实体关系抽取专家，请根据以下新闻内容和实体列表，分析并提取实体间的关系：

新闻标题：{title}
新闻内容：{content}
实体列表：{entities}

请以JSON格式返回关系列表，每条关系包含source、target、relation、description字段：
""".format(
                title=article.get('title', ''),
                content=article.get('content', ''),
                entities=', '.join(entities[:10])  # 限制显示的实体数量
            )

            response = self.llm_client.generate_structured(
                relationships_text,
                max_tokens=1024,
                temperature=0.3
            )

            # 尝试解析JSON响应
            response_text = response.get('choices', [{}])[0].get('message', {}).get('content', '')
            try:
                # 尝试直接解析JSON
                relationships = json.loads(response_text)
                if isinstance(relationships, list):
                    return relationships[:20]  # 限制关系数量
            except:
                pass

            # 如果可能的JSON解析失败，尝试从文本中抽取关系
            relationships = []
            # 添加简单的规则匹配逻辑
            for entity in entities[:5]:  # 限制前5个实体
                for other_entity in entities[5:10]:  # 与后面的实体匹配
                    if entity != other_entity:
                        relationships.append({
                            "source": entity,
                            "target": other_entity,
                            "relation": "提及关系",
                            "description": "在新闻中被同时提及"
                        })

            return relationships[:20]  # 限制关系数量

        except Exception as e:
            logger.error(f"关系提取失败: {e}")
            return []

    def _add_to_graph(self, article: Dict, entities: List[str], relationships: List[Dict]):
        """将实体和关系添加到图中（含名称归一化和实体合并）"""
        try:
            article_id = article.get('article_id', article.get('id', str(hash(article.get('title', 'unknown')))))

            # 添加节点（实体）— 含归一化合并
            for entity in entities:
                if not entity:
                    continue
                norm_name = self._normalize_entity_name(entity)

                # O(1) 查找：用归一化名称索引定位已有实体
                existing_name = self._norm_index.get(norm_name)

                if existing_name:
                    existing = self.graph.nodes[existing_name]
                    refs = existing.get('ref_articles', [])
                    if article_id not in refs:
                        refs.append(article_id)
                        if len(refs) > 50:
                            refs = refs[-50:]
                    self.graph.nodes[existing_name]['ref_articles'] = refs
                    self.graph.nodes[existing_name]['ref_count'] = len(refs)
                    self.graph.nodes[existing_name]['content_hash'] = article.get('content_hash', '')
                else:
                    self.graph.add_node(
                        entity,
                        type="entity",
                        article_id=article_id,
                        title=article.get('title', ''),
                        source=article.get('source', ''),
                        created_at=datetime.now().isoformat(),
                        ref_articles=[article_id],
                        ref_count=1,
                        content_hash=article.get('content_hash', ''),
                    )
                    self._norm_index[norm_name] = entity

            # 添加边（关系）
            for rel in relationships:
                source_entity = rel.get('source', '')
                target_entity = rel.get('target', '')
                relation = rel.get('relation', 'RELATED')

                if source_entity and target_entity and source_entity != target_entity:
                    self.graph.add_edge(
                        source_entity,
                        target_entity,
                        relation=relation,
                        description=rel.get('description', ''),
                        article_id=article_id,
                        created_at=datetime.now().isoformat()
                    )

        except Exception as e:
            logger.error(f"添加节点或边失败: {e}")

    def get_graph_statistics(self) -> Dict[str, Any]:
        """获取图统计信息"""
        return {
            'nodes': self.graph.number_of_nodes(),
            'edges': self.graph.number_of_edges(),
            'is_directed': self.graph.is_directed(),
            'is_multigraph': self.graph.is_multigraph(),
            'density': nx.density(self.graph) if self.graph.number_of_nodes() > 0 else 0
        }