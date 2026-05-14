# 跨文档 LLM 推理规范

## 现状问题

GraphRAG 的实体提取和 QA 都是单篇文章/单次查询的独立 LLM 调用。对于跨时间窗口的聚合问题（如"比亚迪本周舆情变化"）无法直接回答，因为：

- 实体提取缺少跨文章去重和合并（同一公司在不同文章中出现不会合并为一个节点）
- QA 仅检索图邻居和单篇新闻，无时间维度聚合
- 无法回答趋势类问题（"上升/下降/转折"）

## 目标

- 同一实体的多篇文章按时间窗口摘要合并
- QA 支持时间范围参数，可回答趋势类问题
- 不引入 Agent 框架，保持单轮 LLM 调用架构
- 控制实体属性大小，防止热门实体膨胀
- 实体名称归一化，减少同义不同名导致的节点分裂

## 实施方案

### 1. 实体节点合并（含名称归一化）

在 `GraphBuilder._add_to_graph()` 中，对实体名称做归一化后再合并：

```python
import re

@staticmethod
def _normalize_entity_name(name: str) -> str:
    """实体名称归一化：去除常见后缀/前缀差异"""
    # 去除括号内容: "比亚迪(002594)" → "比亚迪"
    name = re.sub(r'[（(][^)）]*[)）]', '', name)
    # 去除常见后缀
    for suffix in ['公司', '有限公司', '股份有限公司', '集团', '科技']:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            name = name[:-len(suffix)]
            break
    return name.strip()
```

在 `GraphBuilder` 中新增归一化名称到原始名称的查找字典（避免每次 O(n) 扫描全图节点）：

```python
class GraphBuilder:
    def __init__(self, llm_client, max_workers=4):
        self.graph = nx.DiGraph()
        self.llm_client = llm_client
        self.max_workers = max_workers
        self._norm_index: Dict[str, str] = {}  # {归一化名称: 原始名称}
```

在 `_add_to_graph` 中使用 `_norm_index` 做 O(1) 查找：

```python
def _add_to_graph(self, article: Dict, entities: List[str], relationships: List[Dict]):
    try:
        article_id = article.get('article_id', article.get('id', str(hash(article.get('title', 'unknown')))))

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
                # 更新 content_hash（使用最新文章的 hash）
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

        for rel in relationships:
            ...
    except Exception as e:
        logger.error(f"添加节点或边失败: {e}")
```

**注意**: 归一化仅用于判断合并，图中仍保留原始名称。避免"比亚迪公司"被改写为"比亚迪"丢失信息。

`_norm_index` 需在加载已有图后初始化（否则增量更新时索引为空，无法合并已有节点）。在 `GraphBuilder` 中新增 `_rebuild_norm_index()`：

```python
def _rebuild_norm_index(self):
    """从当前图的所有节点重建归一化索引。在 load_graph 或 build_knowledge_graph 后调用。"""
    self._norm_index.clear()
    for node in self.graph.nodes():
        norm = self._normalize_entity_name(node)
        if norm not in self._norm_index:
            self._norm_index[norm] = node
```

在 `GraphRAGEngine.load_graph()` 和 `build_knowledge_graph()` 末尾调用 `self.graph_builder._rebuild_norm_index()`。

### 2. 实体时间线摘要

新增 `GraphRAGEngine.build_entity_timeline(entity, days=7)`：

```python
from datetime import datetime, timedelta

def build_entity_timeline(self, entity: str, days: int = 7) -> dict:
    """
    对某个实体的近期所有引用文章做 LLM 摘要合并，
    输出该实体在时间窗口内的关键变化。
    先用归一化查找实体，再从 DB 获取文章按时间排序后送给 LLM。
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
    # 方法名: get_news_by_article_ids
    articles = self.db_manager.get_news_by_article_ids(ref_ids)
    cutoff = datetime.now() - timedelta(days=days)
    articles = [
        a for a in articles
        if a and self._within_days(a.get('published_at', ''), cutoff)
    ]
    # 按发布时间排序
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

    # 方法名: generate_response
    summary = self.llm_client.generate_response(prompt, max_tokens=300)
    return {
        'entity': entity,
        'summary': summary,
        'article_count': len(articles),
        'days': days,
    }

@staticmethod
def _within_days(date_str: str, cutoff: datetime) -> bool:
    """判断日期是否在 cutoff 之后（统一解析两种日期格式）"""
    if not date_str:
        return False
    dt = _parse_date_to_datetime(date_str)
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
    for a in articles[:20]:  # 最多传 20 篇给 LLM
        title = a.get('title', '')
        pub_at = a.get('published_at', '')[:10]
        summary = (a.get('summary', '') or a.get('content', ''))[:100]
        lines.append(f"- [{pub_at}] {title}: {summary}")
    return '\n'.join(lines)
```

### 3. QA 增加时间参数（含缓存 key 修正）

`query_graph()` 增加 `from_date` / `to_date` 参数：

```python
def query_graph(self, question: str, k: int = 5, use_cache: bool = True,
                from_date: str = None, to_date: str = None) -> dict:
    # 缓存 key 包含时间参数，避免不同时间范围的查询命中同一缓存
    if use_cache:
        cache_key = f"query_{hash(question)}_{k}_{from_date}_{to_date}"
        cached_result = self._cache.get(cache_key)
        if cached_result and (datetime.now() - cached_result.get('timestamp', datetime.now())).seconds < self.cache_ttl:
            return cached_result['data']

    ...
    context = self._collect_context(question, relevant_entities,
                                     from_date=from_date, to_date=to_date)
    result = self._generate_answer(question, context,
                                    from_date=from_date, to_date=to_date)

    # 更新缓存时使用正确的 key
    if use_cache:
        ...
        self._cache[cache_key] = {'data': result, 'timestamp': datetime.now()}
    ...
```

`_collect_context` 增加时间筛选参数：

```python
def _collect_context(self, question: str, relevant_entities: List[str],
                     from_date: str = None, to_date: str = None) -> str:
    ...
    if self.db_manager and len(relevant_entities) > 0:
        news_list = self.db_manager.get_latest_news(limit=20)
        for news in news_list:
            pub_at = news.get('published_at', '')
            if from_date and pub_at < from_date:
                continue
            if to_date and pub_at > to_date:
                continue
            ...
```

`_generate_answer` 中增加时间维度提示：

```python
prompt = f"""
上下文时间范围: {from_date or '不限'} ~ {to_date or '不限'}
上下文:
{context}

问题: {question}

如果问题涉及趋势或变化，请按时间线描述；否则直接回答。回答需要是中文，200 字以内。
"""
```

### 4. API 暴露

```python
# POST /api/graph-rag/entity-timeline
class EntityTimelineRequest(BaseModel):
    entity: str
    days: int = 7

@router.post("/api/graph-rag/entity-timeline")
async def entity_timeline(request: EntityTimelineRequest):
    result = engine.build_entity_timeline(request.entity, days=request.days)
    return {'code': 0, 'data': result}

# POST /api/graph-rag/query (扩展)
# body: {"question": "比亚迪本周情况", "from_date": "2026-05-07", "to_date": "2026-05-14"}
```

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `graph_rag/graph_builder.py` | `_add_to_graph()` 实体合并 + 名称归一化 `_normalize_entity_name()` + `_rebuild_norm_index()`；节点增加 `content_hash` |
| `graph_rag/rag_engine.py` | 新增 `build_entity_timeline()`、`_within_days()`、`_parse_date_to_datetime()`、`_format_articles()`；`query_graph()` 缓存 key 和时间参数；`load_graph()` / `build_knowledge_graph()` 末尾调用 `_rebuild_norm_index()` |
| `backend/api/routes/graph_rag.py` | 新增 `/entity-timeline`；扩展 `/query` 接受 `from_date`/`to_date` |

## 验收标准

- [ ] 同一实体出现于 5 篇文章，图谱中仅为 1 个节点，`ref_count = 5`
- [ ] "比亚迪"和"比亚迪公司"归一化后合并为同一实体
- [ ] `entity-timeline` 返回的摘要包含时间序列描述
- [ ] "比亚迪本周情况" 回答仅引用本周内文章
- [ ] 不同 `from_date` 的查询不会命中同一缓存
- [ ] 单实体单篇文章时直接返回原文摘要，不调用 LLM
- [ ] 日期比较兼容 ISO 和空格分隔两种格式
