# AsyncDatabaseManager 拆分规范

## 现状

`backend/database/db_manager_async.py` — 790 行，1 个类 `AsyncDatabaseManager`，包含：
- 4 张表的完整 CRUD（news, analysis, guest_book, event_log）
- 缓存失效逻辑
- SQL 构建辅助（IN 子句兼容）
- 数据库初始化

**隐患**：改 news 表查询要遍历 790 行；加新表只能在类上追加方法；`_build_in_clause` 等工具方法和业务查询混在一起。

---

## 目标架构

```
backend/database/
├── __init__.py
├── engine.py                # 引擎（不变）
├── models_orm.py            # ORM 模型（不变）
├── base.py                  # AsyncRepository 基类
├── repositories/
│   ├── __init__.py
│   ├── news.py              # NewsRepository(news_articles 表)
│   ├── analysis.py          # AnalysisRepository(analysis_results 表)
│   ├── guest_book.py        # GuestBookRepository(guest_book 表)
│   └── events.py            # EventRepository(event_log 表)
├── cache.py                 # 缓存失效（从 db_manager_async 提取）
├── sql_helpers.py           # _build_in_clause, _row_to_dict 等
├── migration.py             # 迁移（不变）
├── db_manager.py            # 同步版 DatabaseManager（保留为回退）
└── redis_client.py          # Redis 客户端（不变）
```

---

## 拆分步骤

### 第 1 步：提取 SQL 工具 `sql_helpers.py`

```python
# 从 db_manager_async.py 移出通用函数
def build_in_clause(param_name: str, values: List[Any], negate: bool = False,
                    is_pg: bool = False) -> Tuple[str, Dict[str, Any]]:
    """IN 子句兼容 PG / SQLite"""
    ...

def row_to_dict(row, cols: List[str]) -> dict:
    """行 → 字典，处理 tags/keywords 逗号分隔"""
    ...

# 列名常量
NEWS_LIGHT_COLS = [...]
NEWS_ALL_COLS = [...]
ANALYSIS_COLS = [...]
EVENT_COLS = [...]
GUEST_COLS = [...]
```

### 第 2 步：定义基类 `repositories/base.py`

```python
from sqlalchemy.ext.asyncio import AsyncSession
from ..engine import AsyncSessionLocal    # repositories/ 在 database/ 子目录下

class AsyncRepository:
    """异步仓库基类"""
    table_name: str
    columns: List[str]

    def __init__(self):
        self._session: Optional[AsyncSession] = None  # 当前事务 session（可选）

    async def _execute(self, sql: str, params: dict = None,
                       session: AsyncSession = None):
        s = session or self._session
        if s:
            return await s.execute(text(sql), params or {})
        async with AsyncSessionLocal() as s:
            return await s.execute(text(sql), params or {})

    async def _fetchone(self, sql: str, params: Dict[str, Any] = None,
                        session: Optional[AsyncSession] = None) -> Optional[Tuple]:
        ...

    async def _fetchall(self, sql: str, params: Dict[str, Any] = None,
                        session: Optional[AsyncSession] = None) -> List[Tuple]:
        ...

    @contextlib.asynccontextmanager
    async def transaction(self):
        """事务上下文管理器：同 session 内的多次写入原子提交"""
        async with AsyncSessionLocal() as session:
            self._session = session
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                self._session = None
```

> `transaction()` 解决批量写入的原子性问题。`save_news_articles` 等在单 session 内执行多次 INSERT 的方法使用此上下文。

### 第 3 步：拆分 Repository

按表拆分，每个 Repository 继承 `AsyncRepository`。

**`repositories/news.py`**：

```python
class NewsRepository(AsyncRepository):
    table_name = "news_articles"
    columns = NEWS_ALL_COLS

    async def save(self, article: dict) -> bool: ...
    async def save_batch(self, articles: List[dict]) -> int: ...
    async def get_latest(self, page=1, limit=20, source=None, category=None): ...
    async def get_by_id(self, article_id: str): ...
    async def get_by_ids(self, article_ids: List[str]): ...
    async def search(self, query, page, limit, category, source): ...
    async def get_fresh(self, excluded_sources, limit): ...
    async def get_hot(self, limit): ...
    async def get_categories(self): ...
    async def reset_hotness(self): ...
    async def delete_duplicates(self): ...
```

**`repositories/analysis.py`**：

```python
class AnalysisRepository(AsyncRepository):
    table_name = "analysis_results"
    columns = ANALYSIS_COLS

    async def save(self, result: dict) -> bool: ...
    async def save_batch(self, results: List[dict]) -> int: ...
    async def get_by_article_id(self, article_id: str): ...
    async def get_by_article_ids(self, article_ids: List[str]) -> Dict[str, dict]: ...
    async def get_stats(self): ...
```

**`repositories/guest_book.py`** 和 **`repositories/events.py`** 类似。

### 第 4 步：提取缓存管理 `cache.py`

```python
class CacheManager:
    """缓存失效管理（仅覆盖当前已有的失效逻辑）"""
    async def invalidate_news_list(self):
        """清除新闻列表分页缓存"""
        for page in range(1, 6):
            for limit in (12, 15, 20, 24, 50):
                redis_client.delete(f"news:latest:{page}:{limit}")
                redis_client.delete(f"news:media:{page}:{limit}")
```

### 第 5 步：重构 `AsyncDatabaseManager` 为门面

```python
class AsyncDatabaseManager:
    """异步数据库门面（保持向后兼容）"""
    def __init__(self):
        self.news = NewsRepository()
        self.analysis = AnalysisRepository()
        self.guest_book = GuestBookRepository()
        self.events = EventRepository()
        self.cache = CacheManager()

    async def init_db(self):
        """初始化数据库表（从原 AsyncDatabaseManager 迁移）"""
        from .engine import engine
        from .models_orm import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("ORM 表初始化完成")

    # 代理方法（保持现有调用方不变）
    async def get_latest_news(self, **kwargs): return await self.news.get_latest(**kwargs)
    async def save_news_article(self, a): return await self.news.save(a)
    async def save_news_articles(self, articles): return await self.news.save_batch(articles)
    async def get_analysis_by_article_id(self, aid): return await self.analysis.get_by_article_id(aid)
    async def get_analysis_by_article_ids(self, aids): return await self.analysis.get_by_article_ids(aids)
    async def save_guest_message(self, aid, content): return await self.guest_book.save(aid, content)
    async def get_guest_messages(self, page, limit): return await self.guest_book.get_messages(page, limit)
    async def insert_event(self, event): return await self.events.save(event)
    # ... 其余代理方法

db_async = AsyncDatabaseManager()
```

### 第 6 步：渐进迁移调用方

- 第一阶段：`AsyncDatabaseManager` 作为门面，所有旧调用不报错
- 第二阶段：新代码直接使用 `db_async.news.xxx()` 替代 `db_async.get_latest_news()`
- 第三阶段：移除门面中的代理方法

---

## 测试策略

| 层级 | 内容 | 工具 |
|------|------|------|
| 单元 | `sql_helpers.build_in_clause` PG/SQLite 分支 | pytest |
| 集成 | 每个 Repository 对 SQLite 内存库 CRUD | pytest + aiosqlite |
| 集成 | 每个 Repository 对 PG 的 ON CONFLICT 行为 | pytest + asyncpg |

---

## 验收标准

- [ ] 每个 Repository 类不超过 200 行
- [ ] 旧调用代码无需修改（门面代理）
- [ ] `_build_in_clause` 有 4 个以上测试用例（空列表、单值、多值、否定）
- [ ] SQLite 和 PostgreSQL 双后端集成测试通过
- [ ] `db_manager_async.py` 缩减到 100 行以内（仅门面）
