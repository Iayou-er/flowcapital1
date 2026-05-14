# GraphRAG 增量更新规范

## 现状问题

知识图谱需通过 `POST /api/graph-rag/build-graph` 手动构建，未集成到定时调度器。每次构建全量重建，无增量机制。

## 目标

- 调度器每次爬取后自动触发增量图更新
- 仅处理新增文章，追加节点和边，不重建全图
- 支持定期全量重建作为兜底（每日凌晨）
- 控制 LLM 调用成本，避免无意义消耗

## 实施方案

### 1. 模块级单例

在 `scripts/scheduler.py` 顶部初始化 GraphRAG 引擎为模块级单例，避免每次调度重复创建：

```python
import os

_graph_rag_engine = None

def _get_rag_engine():
    global _graph_rag_engine
    if _graph_rag_engine is None:
        from graph_rag.rag_engine import GraphRAGEngine
        from graph_rag.llm_client import CloudLLMClient
        llm = CloudLLMClient(
            api_type=os.getenv('LLM_API_TYPE', 'openai'),
            api_key=os.getenv('LLM_API_KEY'),
            model=os.getenv('LLM_MODEL', 'qwen-plus')
        )
        _graph_rag_engine = GraphRAGEngine(llm, db)
    return _graph_rag_engine
```

### 2. 增量构建入口

在 `scripts/scheduler.py` 的 `crawl_and_analyze_news()` 末尾新增：

```python
# 7. 增量更新知识图谱
try:
    engine = _get_rag_engine()
    # news_models 是 NewsArticle ORM 对象列表，需转为 dict 列表传给 builder
    article_dicts = [
        {
            'article_id': m.article_id,
            'title': m.title,
            'content': m.content,
            'summary': m.summary,
            'source': m.source,
            'published_at': m.published_at,
        }
        for m in news_models
    ]
    _incremental_update_with_limit(article_dicts)
except Exception as e:
    logger.warning(f"图谱增量更新失败: {e}")
```

### 3. 增量更新方法

在 `GraphRAGEngine` 中新增 `incremental_update(articles)`：

**去重策略**: 同时检查 `article_id` 和 `content_hash`。内容相同跳过，内容变化（同一 article_id 不同内容 = 文章被更新）则删除旧实体后重新提取。

```
1. 计算每篇文章的 content_hash
2. 遍历图节点，构建 {article_id: content_hash} 映射
3. 过滤：article_id 不存在 → 新文章；article_id 存在但 hash 不同 → 内容更新
4. 对内容更新的文章，先从图中删除其所有旧节点和关联边
5. 对增量集调用 GraphBuilder._extract_entities / _extract_relationships
6. 调用 GraphBuilder._add_to_graph 追加节点和边
7. 更新 _last_build_time
```

核心实现：

```python
import hashlib

def incremental_update(self, articles: list) -> int:
    if not self.graph:
        self.build_knowledge_graph(limit=50)
        return 0

    # 构建 {article_id: (content_hash, 旧hash或None)}
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
            # 内容变化：删除旧节点后重新提取
            logger.info(f"文章内容更新，重新提取实体: {a.get('title', '')}")
            for node_name in old['nodes']:
                self.graph.remove_node(node_name)
            new_articles.append(a)
        # else: 内容相同，跳过

    if not new_articles:
        return 0

    for article in new_articles:
        try:
            # 将 content_hash 注入 article dict，供 _add_to_graph 写入节点属性
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
```

**注意**: `_add_to_graph` 需同步写入 `content_hash` 到节点属性，修改 `graph_builder.py` 中的对应位置。

### 4. LLM 调用成本控制

```python
# 调度器中限制每日增量 LLM 调用次数
_DAILY_LLM_CALLS = 0
_MAX_DAILY_LLM_CALLS = 500  # 每日上限，防止异常情况下费用失控

def _incremental_update_with_limit(article_dicts):
    global _DAILY_LLM_CALLS
    if _DAILY_LLM_CALLS >= _MAX_DAILY_LLM_CALLS:
        logger.warning(f"LLM 调用已达日上限 {_MAX_DAILY_LLM_CALLS}，跳过图谱增量更新")
        return
    # 预估：每篇文章 2 次 LLM 调用（实体 + 关系）
    estimated = len(article_dicts) * 2
    if _DAILY_LLM_CALLS + estimated > _MAX_DAILY_LLM_CALLS:
        article_dicts = article_dicts[:(_MAX_DAILY_LLM_CALLS - _DAILY_LLM_CALLS) // 2]
    engine = _get_rag_engine()
    count = engine.incremental_update(article_dicts)
    _DAILY_LLM_CALLS += count * 2
```

在 `full_analysis()`（凌晨 2:00）中重置计数器 `_DAILY_LLM_CALLS = 0`。

### 5. 全量重建兜底

在 `full_analysis()` 末尾新增凌晨 2:00 全量重建：

```python
engine = _get_rag_engine()
engine.build_knowledge_graph(limit=500)
```

### 6. 图持久化

在 `GraphRAGEngine` 中新增：

```python
import pickle
import os

GRAPH_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'knowledge_graph.pkl')

def save_graph(self):
    os.makedirs(os.path.dirname(GRAPH_PATH), exist_ok=True)
    with open(GRAPH_PATH, 'wb') as f:
        pickle.dump(self.graph, f)

def load_graph(self):
    if os.path.exists(GRAPH_PATH):
        with open(GRAPH_PATH, 'rb') as f:
            self.graph = pickle.load(f)
        self._last_build_time = datetime.now()
        return True
    return False
```

在 `__init__` 末尾调用 `self.load_graph()`，若返回 False 则在首次查询时触发 `build_knowledge_graph`。

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `graph_rag/rag_engine.py` | 新增 `incremental_update()`、`save_graph()`、`load_graph()`；`__init__` 末尾加载磁盘图 |
| `graph_rag/graph_builder.py` | `_add_to_graph()` 节点属性中增加 `content_hash` |
| `scripts/scheduler.py` | 新增 `_get_rag_engine()`、`_DAILY_LLM_CALLS` 计数器；`crawl_and_analyze_news()` 末尾增量更新；`full_analysis()` 末尾全量重建 |

## 验收标准

- [ ] 每 15 分钟爬取后图谱自动更新，无需手动 API 调用
- [ ] 增量文章数 = 新增节点/边来源文章数
- [ ] 同一文章内容更新后旧实体删除、新实体重新提取
- [ ] 凌晨 2:00 全量重建后图统计与 DB 新闻量一致
- [ ] 服务重启后图谱从磁盘恢复，不丢失
- [ ] 日 LLM 调用量不超过 500 次上限

## 已知局限

| 局限 | 说明 | 缓解措施 |
|------|------|----------|
| API 绕过限额 | `POST /api/graph-rag/build-graph` 不经过调度器的 `_DAILY_LLM_CALLS` 计数器，手动调用不受 500 次/日限制 | 个人项目影响可控；生产环境可在 API 端点内读取同一计数器或 Redis 全局计数 |
| 计数器重启丢失 | `_DAILY_LLM_CALLS` 是进程内存变量，调度器重启后归零 | 凌晨 2:00 本身就会重置计数器，重启时间偏离凌晨时当日限额会重新计算；可改用 Redis 持久化计数解决 |
| 共享实体引用丢失 | 内容变更时 `remove_node` 删除节点，若该节点被多篇文章共享（`ref_articles` 含其他文章 ID），其他文章的引用一并丢失 | 新闻内容变更极少发生；每日凌晨 2:00 全量重建自动修复；可改为仅从节点 `ref_articles` 中移除当前文章 ID，当 `ref_articles` 为空时才删除节点 |
