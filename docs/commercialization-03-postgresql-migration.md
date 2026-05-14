# SQLite → PostgreSQL 迁移规范

## 现状问题

- SQLite 单文件，写入串行化（`_wr_lock`），并发受限
- 无连接池，读写分离靠两连接 + 应用层锁
- 不支持网络访问，无法做主从复制
- `INSERT OR REPLACE` 语义在 PostgreSQL 中不同（需 ON CONFLICT）
- 全文搜索依赖 Whoosh（文件索引），PostgreSQL 有内置 `tsvector`

## 目标

- 迁移到 PostgreSQL 14+，保留 SQLite 作为开发/测试环境
- 使用连接池（asyncpg + SQLAlchemy 2.0 async）
- 利用 PostgreSQL 内置全文搜索替代 Whoosh
- 零停机迁移：双写 → 校验 → 切换
- WAL 归档 + 定时备份（pg_dump + WAL-G）

## 实施方案

### 1. 数据库抽象层

引入 SQLAlchemy 2.0 async，根据环境变量切换后端：

```python
# backend/database/engine.py
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///data/sqlite.db"  # 开发默认
)

# asyncpg 专有参数，仅 PostgreSQL 后端生效
connect_args = {}
if DATABASE_URL.startswith("postgresql"):
    connect_args["statement_cache_size"] = 0

engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=10,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,
    connect_args=connect_args,
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

环境变量切换：

```
# 开发
DATABASE_URL=sqlite+aiosqlite:///data/sqlite.db

# 生产
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/flowcapital
```

### 2. ORM 模型定义

涵盖所有业务表：

```python
# backend/database/models_orm.py
from sqlalchemy import Column, String, Integer, Float, Text, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class NewsArticle(Base):
    __tablename__ = "news_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(32), unique=True, nullable=False, index=True)
    title = Column(String(500), nullable=False)
    content = Column(Text)
    summary = Column(Text)
    url = Column(String(2048))
    source = Column(String(100), index=True)
    category = Column(String(100))
    published_at = Column(String(30), index=True)
    author = Column(String(200))
    read_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    tags = Column(Text)
    hotness_score = Column(Float, default=0.0)
    created_at = Column(String(30))
    updated_at = Column(String(30))

    __table_args__ = (
        Index("idx_news_url", "url", unique=True, postgresql_where=url.isnot(None)),
        Index("idx_news_hotness", "hotness_score"),
        Index("idx_news_pub_hotness", "published_at", "hotness_score"),
    )

class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(String(32), nullable=False, index=True)
    model_used = Column(String(50))
    sentiment_score = Column(Float)
    sentiment_label = Column(String(20))
    keywords = Column(Text)
    summary = Column(Text)
    analysis_type = Column(String(50))
    result = Column(Text)
    created_at = Column(String(30))

class GuestBookEntry(Base):
    __tablename__ = "guest_book"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False)
    anonymous_id = Column(String(64), index=True)
    created_at = Column(String(30), index=True)

class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(50), nullable=False, index=True)
    article_id = Column(String(32), index=True)
    client_id = Column(String(64))
    payload = Column(Text)
    created_at = Column(String(30), index=True)
```

### 3. 全文搜索迁移

用 PostgreSQL `tsvector` + 中文分词扩展替代 Whoosh。

**前置条件：** 自建 PostgreSQL 可编译安装扩展；托管服务（RDS / 云数据库）通常不支持 `zhparser` 或 `pg_jieba`。托管场景需确认是否有内置中文分词方案（如 PolarDB 的 `pg_jieba` 内置支持），否则保留 Whoosh 作为备选。

分词扩展安装（二选一，失败时回退另一个）：

```sql
-- 方案 A: zhparser（基于 Simple Chinese Morphological Analysis）
-- 需要先编译安装 zhparser 动态库
CREATE EXTENSION IF NOT EXISTS zhparser;
DROP TEXT SEARCH CONFIGURATION IF EXISTS chinese;
CREATE TEXT SEARCH CONFIGURATION chinese (PARSER = zhparser);
-- zhparser 使用 SCWS 标准 token 类型
ALTER TEXT SEARCH CONFIGURATION chinese ADD MAPPING FOR n,v,a,i,e,l WITH simple;

-- 方案 B: pg_jieba（基于 jieba 分词，与现有 Whoosh 分词器同源）
-- 需要先编译安装 pg_jieba 动态库
CREATE EXTENSION IF NOT EXISTS pg_jieba;
DROP TEXT SEARCH CONFIGURATION IF EXISTS chinese;
CREATE TEXT SEARCH CONFIGURATION chinese (PARSER = jieba);
-- pg_jieba 的 token 类型取决于 jieba 词性标注集，安装后先查询：
-- SELECT DISTINCT alias FROM ts_debug('chinese', '测试文本');
-- 然后按实际 token 类型执行 ADD MAPPING
```

**推荐方案 B（pg_jieba）**，分词结果与现有 jieba-based Whoosh `ChineseAnalyzer` 最接近。注意两个解析器的 token 类型标签不同，`ADD MAPPING` 前需用 `ts_debug` 确认实际标签。

```sql
-- 添加 tsvector 列
ALTER TABLE news_articles ADD COLUMN search_vector tsvector;

-- 创建 GIN 索引
CREATE INDEX idx_news_search ON news_articles USING GIN(search_vector);

-- 自动维护 search_vector 的触发器
CREATE OR REPLACE FUNCTION update_search_vector()
RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('chinese', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('chinese', COALESCE(NEW.content, '')), 'B') ||
        setweight(to_tsvector('chinese', COALESCE(NEW.summary, '')), 'C');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_search_vector
    BEFORE INSERT OR UPDATE ON news_articles
    FOR EACH ROW EXECUTE FUNCTION update_search_vector();
```

搜索查询：

```python
from sqlalchemy import text

async def search_news(query: str, limit: int = 20) -> list[dict]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT article_id, title, summary, source,
                       ts_rank(search_vector, plainto_tsquery('chinese', :query)) AS rank
                FROM news_articles
                WHERE search_vector @@ plainto_tsquery('chinese', :query)
                ORDER BY rank DESC
                LIMIT :limit
            """),
            {"query": query, "limit": limit}
        )
        return [dict(row) for row in result]
```

**Whoosh → PG 全文搜索差异处理：**

| 风险 | 说明 | 缓解措施 |
|------|------|----------|
| 分词差异 | 不同 jieba 版本 / pg_jieba 内置词典可能产生不同 token | 迁移后对 500 条热门搜索对比召回率，差异 > 5% 时调优词典 |
| 排序差异 | `ts_rank` 与 Whoosh BM25 算法不同 | 加入 `hotness_score` 和 `published_at` 作为次级排序 |
| pg_jieba 不可用 | 部分 PG 托管服务不支持自定义扩展 | 回退到 `zhparser` 或保留 Whoosh 作为备选索引 |
| 托管 PG 无扩展 | RDS / 云数据库可能禁止安装任何自定义扩展 | 使用 Whoosh 作为唯一搜索后端；tsvector 方案仅自建 PG 可用 |

### 4. 语法差异适配

| SQLite | PostgreSQL |
|--------|------------|
| `INSERT OR REPLACE` | `INSERT ... ON CONFLICT (article_id) DO UPDATE SET ...` |
| `datetime('now')` | `NOW()` |
| `AUTOINCREMENT` | `SERIAL` 或 `GENERATED ALWAYS AS IDENTITY` |
| `PRAGMA journal_mode=WAL` | 不需要（PostgreSQL 自有 WAL） |
| `LIMIT ? OFFSET ?` | `LIMIT $1 OFFSET $2` |

### 5. `INSERT OR REPLACE` 语义差异（重要）

SQLite 的 `INSERT OR REPLACE` 在冲突时 **DELETE 旧行 → INSERT 新行**，副作用：
- 未在 INSERT 中指定的列丢失（重置为默认值）
- 自增 ID 变化
- 触发 DELETE 触发器

PostgreSQL 的 `ON CONFLICT DO UPDATE` **保留未指定列的原值**。

迁移前必须审计所有调用点，按需选择策略：

```sql
-- 策略 1：完全替换（模拟 SQLite REPLACE 行为）
-- 使用 DO UPDATE SET 显式列出所有列
INSERT INTO news_articles (article_id, title, content, ...)
VALUES (...)
ON CONFLICT (article_id) DO UPDATE SET
    title = EXCLUDED.title,
    content = EXCLUDED.content,
    ...;  -- 所有列都覆盖

-- 策略 2：合并更新（保留旧值，更安全）
INSERT INTO news_articles (article_id, title, content, ...)
VALUES (...)
ON CONFLICT (article_id) DO UPDATE SET
    title = EXCLUDED.title,
    content = EXCLUDED.content,
    updated_at = NOW();  -- 仅覆盖变化字段
```

### 6. 迁移流程

#### 6.1 双写机制

当前 `db_manager.py` 是单例，硬编码 SQLite 连接。迁移需要支持同时写两库：

```python
# backend/database/dual_write.py
class DualWriteManager:
    """双写管理器：同时写入 SQLite 和 PostgreSQL，读取仍走 SQLite"""

    def __init__(self, sqlite_session, pg_session):
        self.sqlite = sqlite_session
        self.pg = pg_session

    async def save_news_article(self, article: dict):
        # 写入 SQLite（主库）
        await self.sqlite.save_news_article(article)
        # 异步写入 PostgreSQL（备库，失败不影响主流程）
        try:
            await self.pg.save_news_article(article)
        except Exception as e:
            logger.warning(f"PG 双写失败（不影响主流程）: {e}")

    # ... 其他方法同理
```

双写期间的写入策略：SQLite 为主（失败则中断请求），PostgreSQL 为备（失败仅告警不阻塞）。

#### 6.2 迁移阶段

```
阶段 0: 代码重构（2-3 周）
  ├── db_manager.py 重写为 async SQLAlchemy（保持 SQLite 后端）
  ├── 所有路由 handler 改为 async def
  ├── 部署验证：功能无退化
  └── 产出：DualWriteManager + PG engine 就绪

阶段 1: 双写（1 周）
  ├── 部署 DualWriteManager，同时写入 SQLite + PostgreSQL
  ├── 历史数据批量迁移（按 published_at 分批 INSERT）
  └── 校验脚本：对比两库 article_id 数量和内容哈希

阶段 2: 读切换（灰度）
  ├── 10% 流量读 PostgreSQL
  ├── 监控响应时间和错误率
  └── 逐步扩大到 100%

阶段 3: 单库运行
  ├── 停 SQLite 写入
  ├── PostgreSQL 独立运行 ≥ 3 天
  └── 备份 SQLite 文件后下线
```

数据迁移覆盖范围：`news_articles`、`analysis_results`、`guest_book`、`event_log`（全部业务表）。

### 7. 备份策略

```bash
# 每日全量备份（通过 PGPASSWORD 环境变量或 .pgpass 文件提供密码）
PGPASSWORD="${DB_PASSWORD}" pg_dump -h localhost -U flowcapital flowcapital | gzip > /backup/daily_$(date +%Y%m%d).sql.gz
```

PostgreSQL 配置文件（`postgresql.conf`）中设置 WAL 归档：

```ini
# WAL 归档（支持 PITR 时间点恢复）
archive_command = 'wal-g wal-push %p'
```

保留策略：

```
每日备份: 保留 30 天
WAL 归档: 保留 7 天
```

恢复验证：`pg_dump` 备份需在隔离环境恢复并校验表行数一致。

### 8. Redis 缓存策略

Redis 保留不变，用途：

| 用途 | TTL | 说明 |
|------|-----|------|
| SimHash 去重指纹 | 24h | 跨批次 URL 去重，已实现 |
| 分析结果缓存 | 1h | scheduler 完成批量情感分析后写入 Redis，API 读取时命中缓存，减少 DB 查询 |

Redis 中的分析结果是 scheduler 预处理后写入的只读缓存，不触发按需 LLM 调用：

```
scheduler → 批量分析 → 写 PG + 写 Redis（1h TTL）
API 请求 → Redis 命中 → 返回（不查 DB）
         → Redis 未命中 → 查 PG → 返回
```

### 9. 连接池配置

```python
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,           # 常规连接数
    max_overflow=10,        # 峰值溢出
    pool_recycle=3600,      # 1 小时回收
    pool_pre_ping=True,     # 连接前检测有效性
    connect_args=connect_args,
)
```

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/database/engine.py` | **新建**，SQLAlchemy async engine + session |
| `backend/database/models_orm.py` | **新建**，NewsArticle / AnalysisResult / GuestBook / EventLog 的 ORM 模型 |
| `backend/database/dual_write.py` | **新建**，双写管理器 |
| `backend/database/db_manager.py` | 重写为 async，基于 SQLAlchemy |
| `backend/database/migration.py` | **新建**，数据迁移脚本（SQLite → PG） |
| `backend/analyzer/search_engine.py` | 替换 Whoosh 为 PostgreSQL 全文搜索；保留 Whoosh 作为 fallback |
| `scripts/scheduler.py` | 改为 async，适配 SQLAlchemy |
| `backend/api/routes/*.py` | 路由 handler 改为 `async def` + async session |
| `requirements.txt` | 新增 `sqlalchemy[asyncio]`、`asyncpg`、`aiosqlite` |

## 验收标准

- [ ] 开发环境仍可使用 SQLite（`DATABASE_URL=sqlite+aiosqlite:///...`）
- [ ] 双写期间 PG 写入失败不影响主流程（仅告警）
- [ ] 双写期间两库 article_id 全部一致
- [ ] PostgreSQL 全文搜索结果与 Whoosh 偏差 ≤ 5%（500 条查询抽样）
- [ ] 所有 `INSERT OR REPLACE` 调用点已审计，`ON CONFLICT` 语义正确
- [ ] 100 并发下 P99 查询延迟 < 200ms
- [ ] pg_dump 备份可在 5 分钟内恢复
- [ ] Redis 分析结果缓存由 scheduler 写入，API 只读命中
- [ ] guest_book、event_log 数据完整迁移
