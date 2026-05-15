# SQLite 高并发写锁争用修复规范

## 现状

`backend/database/engine.py` 已修复为 SQLite `pool_size=1`。但 SQLite 的单写者限制意味着：

- 8 个 uvicorn worker × 各自持有一个连接
- 任意时刻只有 1 个 worker 能执行 `INSERT/UPDATE`
- 其他 7 个 worker 收到 `database is locked` 后必须重试

**时间窗口**：当前写入耗时约 5-50ms，8 worker 并发下约 0.5%-5% 的请求会遇到锁。

**表现**：
- 前端偶尔看到 500 错误（SQLite 锁超时，默认 5s）
- 爬虫批量写入时阻塞读请求
- 分析结果写入与新闻列表读取互相竞争

---

## 修复方案

### 方案 A：WAL 模式 + 忙等待（短期，推荐先做）

SQLite 的 WAL 模式允许"读写并发"（1 写 + N 读同时进行）。

**已启用**（`db_manager.py` 中），但需验证 `db_manager_async.py` 的 SQLAlchemy 连接是否也启用：

```python
# engine.py — 在 create_async_engine 中增加
engine = create_async_engine(
    DATABASE_URL,
    pool_size=1,
    connect_args={
        **connect_args,
        "timeout": 10,  # 锁等待超时 10s（默认 5s）
    },
)
```

同时添加 WAL 模式初始化：

```python
# 在 init_db() 中自动启用 WAL
async def init_db(self):
    async with engine.begin() as conn:
        if not _IS_PG:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            await conn.execute(text("PRAGMA busy_timeout=10000"))
        await conn.run_sync(Base.metadata.create_all)
```

### 方案 B：写操作队列（中期）

对写操作进行排队，避免锁争用：

```python
# backend/database/write_queue.py
import asyncio
from typing import Any, Callable, Coroutine

class WriteQueue:
    """SQLite 写操作串行化队列"""
    def __init__(self, execute_fn: Callable[..., Coroutine]):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task = None
        self._execute = execute_fn  # 注入实际执行函数

    async def start(self):
        self._task = asyncio.create_task(self._worker())

    async def _worker(self):
        while True:
            future, sql, params = await self._queue.get()
            try:
                result = await self._execute(sql, params)
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)

    async def enqueue(self, sql: str, params: dict) -> Any:
        future = asyncio.Future()
        await self._queue.put((future, sql, params))
        return await future

# 使用示例（依赖 refactor-02 拆分后的 AsyncRepository._execute）
# write_queue = WriteQueue(execute_fn=db_async.news._execute)
```

影响：写入延迟增加（排队等待），但消除锁错误。

### 方案 C：写操作自动重试（推荐组合）

在 Repository 基类中添加自动重试装饰器：

```python
import asyncio
import sqlite3
from functools import wraps

from sqlalchemy import exc as sa_exc

def retry_on_lock(max_retries: int = 3, delay: float = 0.1):
    """SQLite database is locked 自动重试

    匹配策略：先按异常类（sqlite3.OperationalError / sqlalchemy.exc.OperationalError），
    再 fallback 字符串匹配，覆盖不同驱动封装的差异。
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (sqlite3.OperationalError, sa_exc.OperationalError) as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"数据库锁，重试 {attempt + 1}/{max_retries}: {e}")
                        await asyncio.sleep(delay * (2 ** attempt))
                        continue
                    raise
                except Exception as e:
                    # fallback: aiosqlite 或其他封装可能只透传字符串消息
                    msg = str(e).lower()
                    if "database is locked" in msg or "operationalerror" in msg:
                        if attempt < max_retries - 1:
                            logger.warning(f"数据库锁(字符串匹配)，重试 {attempt + 1}/{max_retries}: {e}")
                            await asyncio.sleep(delay * (2 ** attempt))
                            continue
                    raise
        return wrapper
    return decorator
```

### 方案 D：切换到 PostgreSQL（长期）

SQLite 是单文件嵌入式数据库，不适合多 worker 生产环境。见 `docs/commercialization-03-postgresql-migration.md`。

---

## 优先级

| 方案 | 影响 | 工作量 | 推荐顺序 |
|------|------|--------|----------|
| A. WAL + busy_timeout | 降低 90% 锁错误 | 5 行改动 | **立即** |
| C. 自动重试 | 消除剩余 10% 锁错误 | 30 行 | **立即** |
| B. 写队列 | 串行化保证 | 60 行 | 中期（如果 A+C 不够） |
| D. PG 迁移 | 根本解决 | 见独立文档 | 长期 |

---

## 验收标准

- [ ] WAL 模式在日志中确认启用（`PRAGMA journal_mode` 返回 `wal`）
- [ ] 并发写入测试：10 个协程同时 INSERT，0 个 `database is locked` 错误
- [ ] `busy_timeout=10000` 生效（10 秒超时）
- [ ] 写操作重试日志中出现 `retry attempt` 时数量 < 3
