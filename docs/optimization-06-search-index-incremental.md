# 搜索索引增量更新规范

## 现状问题

Whoosh 全文搜索索引仅在 `POST /api/news/rebuild-index` 手动调用时重建，或在爬取批次结束后写入。但存在三个缺陷：

1. **批次写入失败不留痕迹** — `add_documents_batch` 异常被 `except` 吞掉，索引可能落后于 DB
2. **无增量删除/更新** — 文章去重或内容修正后，索引中旧条目残留
3. **无定期优化** — 索引碎片化和未合并的段文件导致查询变慢

## 目标

- 写库即写索引，避免 DB 与索引不一致
- 支持增量删除/更新
- 定期自动优化索引段
- 每批次和每日两次一致性检查，覆盖不同时间粒度
- 提供索引健康指标日志
- 承认跨系统事务不可行，用多层检查兜底

## 实施方案

### 1. 写库后成批写入索引

不逐篇写索引（每篇单独 commit 性能差），而是在 `save_news_articles` 返回后，对实际新增/更新的文章批量调用 `add_documents_batch`。

在 `scripts/scheduler.py` 的 `crawl_and_analyze_news()` 中，`save_news_articles` 之后：

```python
# 2.5 增量更新搜索索引（仅新增/更新的文章）
if news_models:
    try:
        from backend.analyzer.search_engine import add_documents_batch
        new_or_updated = [
            {
                'article_id': m.article_id,
                'title': m.title,
                'content': m.content,
                'summary': m.summary,
                'source': m.source,
                'category': m.category,
                'published_at': m.published_at,
            }
            for m in news_models
        ]
        count = add_documents_batch(new_or_updated)
        logger.info(f"搜索索引增量更新: {count} 条")
    except Exception as e:
        logger.warning(f"更新搜索索引失败: {e}")
        # 索引落后于 DB，下次 rebuild-index 或 optimize 时修复
```

### 2. 搜索索引 API 确认

当前 `search_engine.py` 实际函数名：

| 用途 | 正确调用 | 说明 |
|------|----------|------|
| 批量 upsert | `add_documents_batch(docs)` | 内部用 `writer.update_document()`，重复 article_id 自动更新 |
| 单篇 upsert | `add_document(doc)` | 同上，单篇版本 |
| 删除 | `remove_document(article_id)` | `writer.delete_by_term('article_id', ...)` |
| 优化 | `optimize_index()` | `writer.commit(optimize=True)` |
| 获取索引 | `_get_index()` | 模块内部函数，带下划线 |

`add_documents_batch` 已使用 `writer.update_document()`（upsert 语义），重复 `article_id` 直接覆盖不报错。

### 3. 每批次轻量一致性检查

在 `crawl_and_analyze_news()` 的索引写入后立即做轻量检查（只比对数量，不扫全量）：

```python
# 2.6 轻量一致性检查（数量比对）
try:
    from backend.analyzer.search_engine import _get_index
    idx = _get_index()
    reader = idx.reader()
    indexed_count = reader.doc_count()
    reader.close()
    db_count = db.get_news_count()
    lag = db_count - indexed_count
    if lag > 50:
        logger.warning(f"搜索索引落后 DB {lag} 条，将在凌晨全量修复")
    else:
        logger.debug(f"索引一致性: DB={db_count}, 索引={indexed_count}, 差距={lag}")
except Exception as e:
    logger.warning(f"轻量一致性检查失败: {e}")
```

### 4. 每日全量一致性检查

在 `full_analysis()`（每日凌晨 2:00）中增加全量对比修复：

```python
def check_search_index_consistency(db):
    """
    全量对比 DB 和 Whoosh 索引的 article_id 差异，修复缺失条目。
    注意：Whoosh Index 对象无 doc_count_all()，需通过 reader 获取。
    """
    from backend.analyzer.search_engine import _get_index, add_documents_batch

    idx = _get_index()
    reader = idx.reader()
    indexed_ids = set(r['article_id'] for r in reader.all_stored_fields() if r.get('article_id'))
    reader.close()

    db_ids = set(db.get_recent_article_ids(limit=10000))

    missing = db_ids - indexed_ids
    if not missing:
        logger.info("搜索索引全量一致性检查通过")
        return

    logger.warning(f"搜索索引缺失 {len(missing)} 条，开始修复...")
    articles = db.get_news_by_article_ids(list(missing))
    docs = [
        {
            'article_id': a['article_id'],
            'title': a.get('title', ''),
            'content': a.get('content', ''),
            'summary': a.get('summary', ''),
            'source': a.get('source', ''),
            'category': a.get('category', ''),
            'published_at': a.get('published_at', ''),
        }
        for a in articles
    ]
    count = add_documents_batch(docs)
    logger.info(f"索引修复完成: {count} 条")
```

### 5. 索引优化增加阈值判断 + 健康指标

```python
def optimize_index():
    """优化索引段，输出健康指标"""
    idx = _get_index()
    reader = idx.reader()
    doc_count = reader.doc_count()
    doc_count_all = reader.doc_count_all()
    deleted = doc_count_all - doc_count
    reader.close()

    # 健康指标日志
    logger.info(f"索引健康: 文档={doc_count}, 已删除={deleted}, "
                f"删除率={deleted/max(doc_count_all,1)*100:.1f}%")

    if deleted > 100:
        writer = idx.writer()
        writer.commit(optimize=True)  # Whoosh 2.7+ 支持 optimize 参数
        logger.info(f"索引优化完成，清理 {deleted} 条已删除文档")
    else:
        logger.debug(f"索引删除文档数 {deleted}，跳过优化")
```

### 6. 多进程并发安全说明

当前架构中 scheduler 和 API 可能运行在不同进程：

```
scheduler 进程 → Whoosh writer → commit
API 进程 (uvicorn) → Whoosh searcher → search
```

Whoosh 支持 **一写多读** 模型：writer 提交后 searcher 通过 `index.searcher()` 获取最新快照。但如果 writer 未 commit，新数据对 searcher 不可见。

**风险点**: API 进程的 searcher 不会自动刷新。解决方案：在 API 搜索入口每次重新打开 searcher（当前 `search()` 函数已使用 `with idx.searcher() as searcher:` 模式，每次搜索都获取最新 reader 快照，无需额外处理）。

### 7. 移除手动重建端点依赖

保留 `POST /api/news/rebuild-index` 作为紧急恢复手段，但正常流程不再依赖它。

## 跨系统事务说明

Whoosh 和 SQLite 是独立存储，不存在跨系统事务。处理策略：
- **正常路径**: 先写 DB，再写索引。索引失败不影响 DB
- **轻量检查**: 每 15min 比对 DB 和索引数量，差距 > 50 条告警
- **全量修复**: 每日凌晨 2:00 对比 article_id 集合，自动补写缺失条目

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/analyzer/search_engine.py` | `optimize_index()` 加阈值判断 + 健康指标；新增 `check_index_consistency()` |
| `backend/database/db_manager.py` | `get_recent_article_ids`、`get_news_by_article_ids`（与 02 规范共享） |
| `scripts/scheduler.py` | `crawl_and_analyze_news()` 中 save 后批量写索引 + 轻量检查；`full_analysis()` 中调用全量一致性检查 |

## 验收标准

- [ ] 新文章入库后可在搜索中检索到
- [ ] 删除文章后索引中对应条目不存在
- [ ] 每 15min 轻量检查输出数量差距，大于 50 条告警
- [ ] 每日凌晨全量检查自动修复缺失条目
- [ ] 索引优化日志包含文档数、删除数、删除率
- [ ] 索引写入失败不影响爬取和入库流程
- [ ] API 搜索始终读取最新提交的索引快照
