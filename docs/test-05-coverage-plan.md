# 自动化测试覆盖计划

## 现状

- 测试文件：仅 1 个（`graph_rag/test_graph_rag.py`）
- 测试框架：无（test_graph_rag.py 不用 pytest，无 assert）
- 后端 Python 代码约 6250 行，覆盖率为 0%

---

## 分层测试策略

### L1：单元测试（目标覆盖率 60%）

**工具**：pytest + pytest-asyncio + pytest-cov

**范围**：

| 模块 | 文件 | 测试对象 | 用例数 |
|------|------|----------|--------|
| 去重 | `analyzer/dedup.py` | SimHash 计算、汉明距离、跨批次去重 | 8 |
| 情感 | `analyzer/sentiment.py` | SnowNLP 得分、词典回退、分数归一化 | 6 |
| 情感词典 | `analyzer/finance_sentiment_dict.py` | 正负词匹配、程度副词、否定前缀 | 10 |
| 翻译 | `analyzer/translator.py` | 缓存命中、同语言直返、回退方案（mock 外部 API） | 5 |
| 搜索 | `analyzer/search_engine.py` | 中文分词、查询构建、索引读写 | 6 |
| 文本分析 | `analyzer/text_analyzer.py` | 关键词提取、摘要生成、实体识别 | 8 |
| 频率限制 | `middleware/rate_limiter.py` | 滑动窗口计数、拒绝/放行、清理 | 8 |
| 安全头 | `middleware/security_headers.py` | CSP/HSTS 响应头验证 | 4 |
| 认证 | `api/auth.py` | API Key 验证、CSRF 校验、路径匹配 | 8 |
| SQL 工具 | `database/sql_helpers.py` | IN 子句构建 PG/SQLite 分支 | 6 |
| 热度 | `scripts/hotness.py`（提取后） | 时间衰减公式 | 5 |
| **合计** | | | **74** |

### L2：集成测试（目标覆盖率 30%）

**工具**：pytest + aiosqlite（内存库）+ httpx.AsyncClient（TestClient）

| 模块 | 测试对象 | 用例数 |
|------|----------|--------|
| Repository CRUD | NewsRepository 增删改查（SQLite 内存） | 10 |
| Repository CRUD | AnalysisRepository 增删改查 | 5 |
| Repository CRUD | GuestBookRepository 增删改查 | 5 |
| API 路由 | `/api/news/latest` 返回格式和分页 | 6 |
| API 路由 | `/api/news/search` 搜索结果 | 4 |
| API 路由 | `/api/analysis` 统计端点 | 4 |
| API 路由 | `/api/guest/messages` 分页和 XSS 过滤 | 5 |
| API 路由 | 认证端点 401/403 响应 | 5 |
| API 路由 | 频率限制 429 响应 | 4 |
| 调度器 | `run_full_analysis` 管线顺序 | 4 |
| **合计** | | **52** |

### L3：端到端测试（目标覆盖率 10%）

**工具**：pytest + Playwright（前端）或 httpx（后端全链路）

| 场景 | 步骤 | 用例数 |
|------|------|--------|
| 爬取→展示 | 爬取 1 个稳定源 → 检查 API 返回 | 2 |
| 搜索→详情 | 搜索关键词 → 点击文章 → 查看翻译 | 2 |
| 留言流程 | 分配 ID → 登录 → 留言 → 查看留言列表 | 2 |
| 认证保护 | 无 Key 访问写操作 → 401 | 3 |
| **合计** | | **9** |

---

## 基础设施搭建

### 第 1 步：安装依赖

```
# requirements-dev.txt
pytest==8.0
pytest-asyncio==0.23
pytest-cov==5.0
pytest-mock==3.12
httpx==0.28
aiosqlite==0.20
```

### 第 2 步：创建目录结构

```
tests/
├── conftest.py               # 共享 fixtures（db, client, app）
├── unit/
│   ├── test_dedup.py
│   ├── test_sentiment.py
│   ├── test_finance_dict.py
│   ├── test_translator.py
│   ├── test_search_engine.py
│   ├── test_text_analyzer.py
│   ├── test_rate_limiter.py
│   ├── test_security_headers.py
│   ├── test_auth.py
│   └── test_graph_rag.py      # 从 graph_rag/ 迁移
├── integration/
│   ├── conftest.py           # DB fixtures
│   ├── test_news_repository.py
│   ├── test_analysis_repository.py
│   ├── test_guest_book_repository.py
│   ├── test_news_routes.py
│   ├── test_analysis_routes.py
│   ├── test_guest_book_routes.py
│   ├── test_auth_routes.py
│   └── test_rate_limit.py
└── e2e/
    ├── test_crawl_to_api.py
    ├── test_search_flow.py
    └── test_guest_book_flow.py
```

### 第 3 步：编写 `conftest.py`

```python
# tests/conftest.py
import os
import pytest

# 必须在任何 backend 导入之前设置，因为 engine.py 在模块级别读取 DATABASE_URL
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENABLE_AUTH"] = "true"
os.environ["API_KEY"] = "test-api-key-for-ci"

@pytest.fixture
async def db():
    from backend.database.db_manager_async import db_async
    await db_async.init_db()
    yield db_async

@pytest.fixture
async def client():
    from httpx import AsyncClient, ASGITransport
    from backend.api.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

### 第 4 步：CI 集成

更新 `.github/workflows/deploy.yml`（在 deploy job 之前添加 test job）：

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: python -m pytest tests/ --cov=backend --cov=graph_rag --cov-report=term

  deploy:
    needs: test   # 测试通过才部署
    runs-on: ubuntu-latest
    # ... 原有部署步骤
```

同时在 PR 触发时也运行测试（`.github/workflows/pr.yml` 或使用 `on: [push, pull_request]`）。

---

## 实施路线

| 阶段 | 内容 | 用例数 | 时间估算 |
|------|------|--------|----------|
| 1 | 基础设施 + conftest + 前 15 个单元测试 | 15 | 2 天 |
| 2 | 剩余单元测试（59 个） | 59 | 3 天 |
| 3 | 集成测试（52 个） | 52 | 3 天 |
| 4 | E2E 测试（9 个）+ CI 集成 | 9 | 1 天 |
| **合计** | | **135** | **9 天** |

---

## 验收标准

- [ ] `pytest` 在 CI 中运行，0 失败
- [ ] `backend/analyzer/` 覆盖率 > 60%
- [ ] `backend/api/` 覆盖率 > 50%
- [ ] `backend/middleware/` 覆盖率 > 80%
- [ ] 每个新 PR 必须包含相关模块的测试
- [ ] `test_graph_rag.py` 迁移到 `tests/` 标准目录
