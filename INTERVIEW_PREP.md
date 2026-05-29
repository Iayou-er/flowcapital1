# FlowCapital 面试准备文档

## 一句话概述

FlowCapital 是一套**财经新闻自动采集→NLP情感分析→知识图谱→可视化**的全链路平台，覆盖 25 个数据源，支持 GraphRAG 智能问答，日均处理数千条新闻。

---

## 一、项目架构（必问：画出架构图）

```
┌─────────────────────────────────────────────────────────┐
│                      用户浏览器                           │
└────────────┬────────────────────────────┬────────────────┘
             │ HTTPS (443)                │
             ▼                            │
┌────────────────────────┐                │
│   Nginx (反向代理)      │                │
│   - SSL/TLS 终结        │                │
│   - 静态文件服务         │                │
│   - API 反向代理         │                │
│   - Edge 缓存 (15min)    │                │
│   - 限流 (30r/s per IP)  │                │
│   - API Key 服务端注入    │                │
└────────┬───────────────┘                │
         │ /api/*                         │ / (静态)
         ▼                                ▼
┌────────────────────┐    ┌────────────────────┐
│  FastAPI (uvicorn)  │    │  React 18 SPA      │
│  8 workers          │    │  Ant Design 5      │
│  port 8001          │    │  Recharts          │
│                     │    │  React Router v6   │
│  ┌───────────────┐  │    │  Axios (25s 超时)   │
│  │ 7 组路由       │  │    └────────────────────┘
│  │ - 新闻 CRUD    │  │
│  │ - 分析聚合     │  │
│  │ - GraphRAG     │  │
│  │ - 留言板       │  │
│  │ - 用户行为     │  │
│  │ - Prometheus   │  │
│  │ - Admin 面板   │  │
│  └───────────────┘  │
└────────┬───────────┘
         │
    ┌────┴────┬──────────┬──────────┐
    ▼         ▼          ▼          ▼
┌──────┐ ┌──────┐  ┌────────┐  ┌──────────┐
│SQLite│ │Redis │  │Whoosh  │  │GraphRAG   │
│/ PG  │ │缓存   │  │全文搜索 │  │知识图谱   │
│      │ │+降级  │  │+jieba  │  │(NetworkX) │
└──────┘ └──────┘  └────────┘  └──────────┘
         ▲
         │ (异步任务)
┌────────┴──────────────────┐
│  Scheduler (独立进程)      │
│  ┌─────────────────────┐  │
│  │ crawl_and_analyze() │  │ ← 每 15 分钟
│  │ - 25 源并行爬取       │  │
│  │ - 三层去重 (SimHash)  │  │
│  │ - NLP 分析 (4线程池)  │  │
│  │ - 搜索索引增量更新     │  │
│  │ - GraphRAG 增量构建   │  │
│  │ - 缓存预热            │  │
│  ├─────────────────────┤  │
│  │ full_analysis()     │  │ ← 每天凌晨 2 点
│  │ - 全量 NLP 重分析     │  │
│  │ - 热点计算 + 旧事件清理│  │
│  │ - 图谱全量重建        │  │
│  │ - 索引一致性检查      │  │
│  └─────────────────────┘  │
│  Playwright (Chromium)     │ ← 反爬动态渲染
└───────────────────────────┘
```

---

## 二、核心技术栈

| 层 | 技术 | 版本 | 用途 |
|----|------|------|------|
| **Web 框架** | FastAPI | 0.115 | 异步 API，自动 OpenAPI 文档 |
| **ASGI 服务器** | uvicorn | 0.34 | 8 workers，--limit-concurrency 100 |
| **ORM** | SQLAlchemy 2.0 | 2.0 | 异步引擎，支持 SQLite/PostgreSQL |
| **NLP - 分词** | jieba | 0.42 | 中文分词 + 词性标注 |
| **NLP - 情感** | SnowNLP | 0.12 | 朴素贝叶斯情感分析 |
| **NLP - 摘要** | SnowNLP | - | TextRank 抽取式摘要 |
| **NLP - 去重** | SimHash | 自研 | 64 位指纹 + 4 段桶索引 |
| **全文搜索** | Whoosh | 2.7 | jieba 分词器 + PG tsvector 降级 |
| **知识图谱** | NetworkX + LLM | 3.4 | 实体关系抽取 + 有向图 |
| **浏览器自动化** | Playwright | 1.46 | Chromium headless 反爬 |
| **前端框架** | React 18 | 18.2 | SPA，懒加载路由 |
| **UI 库** | Ant Design 5 | 5.29 | 企业级组件 |
| **图表** | Recharts | 2.6 | PieChart, BarChart, LineChart |
| **HTTP 客户端** | Axios | 1.3 | 拦截器 + 超时 + 状态通知 |
| **反向代理** | Nginx | alpine | HTTPS, 缓存, 限流, API Key 注入 |
| **监控** | Prometheus + Grafana | - | 指标采集 + 仪表盘 |
| **部署** | systemd (裸机) | - | API + Scheduler 双服务 |
| **备选部署** | Docker Compose | - | API + Scheduler + Nginx 三容器 |

---

## 三、面试高频考点详解

### 3.1 为什么用 FastAPI 而不是 Flask/Django？

**回答要点：**

1. **异步原生支持**：25 个数据源并行爬取，Flask 需要额外装 gevent/asyncio。FastAPI 的 `async/await` 是语言级支持。
2. **自动 OpenAPI 文档**：`/docs` 自动生成 Swagger UI，调试和团队协作方便。
3. **类型安全**：Pydantic 模型自动校验请求参数，减少运行时错误。
4. **性能**：uvicorn 是多进程 + uvloop，单 worker 就能处理数千并发连接。
5. **依赖注入**：`Depends()` 做鉴权、限流很方便，不需要装饰器嵌套。

> 实际对比：这个项目有 40+ 个 API 端点，如果要用 Flask 的 Blueprint 写，路由注册和参数校验代码量会多 30%+。

---

### 3.2 为什么用 SQLite 而不是 MySQL/PostgreSQL？

**回答要点：**

1. **零运维**：不需要单独安装数据库服务，数据文件就是 `data/sqlite.db`，备份直接 `cp`。
2. **足够用**：日均处理几千条新闻，SQLite 在 WAL 模式下读并发能达到 1000+ QPS。
3. **设计了迁移路径**：`backend/database/engine.py` 通过 `DATABASE_URL` 环境变量即可切换到 PG。`migration.py` 提供了完整的 SQLite→PG 迁移脚本。
4. **异步驱动**：`aiosqlite` 让 SQLite 也能支持 FastAPI 的 async 生态。

> 面试官可能会追问：SQLite 写锁问题怎么解决？答：WAL 模式（`PRAGMA journal_mode=WAL`）让读不阻塞写。另外 scheduler 是单进程写入，API 是 8 workers 只读（分析结果由 scheduler 写入），写冲突极少。

---

### 3.3 25 个数据源并行爬取怎么实现的？

**回答要点：**

1. **源分类**：14 个快速源（API/RSS）+ 10 个慢速源（网页抓取），分两批并行执行。
2. **爬取器模式**：每个源实现自己的 fetcher（继承 `BaseFetcher`），通过注册表统一管理。
3. **orchestrator.py**：`asyncio.gather` 并行调度所有源，全局 180s 超时。
4. **熔断器**（`circuit.py`）：单个源连续失败 3 次 → 冷却 5 分钟，防止卡住整个爬取。
5. **4 级内容抽取管道**（`pipeline.py`）：域名特定选择器 → 通用选择器 → 文本密度评分 → 隐藏域兜底。
6. **Playwright**：微信搜狗、同花顺等需要 JS 渲染的源，用 Chromium headless 动态抓取。

> 面试官追问：怎么处理反爬？答：Playwright 设置中文 locale、随机延迟（0.1s）、轮换 User-Agent。另外熔断器避免高频请求被封 IP。

---

### 3.4 去重怎么做的？

**回答要点：**

1. **三层去重**：
   - **URL 去重**：同批次相同 URL 只保留一条（数据库也有 UNIQUE 约束）
   - **SimHash 批内去重**：计算标题+正文的 64 位 SimHash 指纹，Hamming 距离 ≤4 视为重复
   - **SimHash 跨批去重**：对新文章计算指纹后查 Redis（24h TTL），与历史指纹对比
2. **SimHash 原理**：对文本做分词→加权哈希→合并→降维，得到 64 位指纹。相似文本的指纹 Hamming 距离很小。
3. **优化**：4 段桶索引（每段 16 位），只比较落在同一桶的指纹，避免 O(n²)。

---

### 3.5 NLP 情感分析怎么做的？

**回答要点：**

1. **双引擎**：
   - **SnowNLP**：朴素贝叶斯分类器，输出 0-1 情感分数，映射到 [-1, 1]
   - **财经词典**：32 个正向短语（"超预期""利好"）、32 个负向短语（"暴跌""亏损"），带程度副词加权和否定词（10 字符窗口）检测
2. **融合策略**：60% SnowNLP + 40% 词典，因为 SnowNLP 训练语料偏电商评价，在财经领域准确率不够，词典做领域修正。
3. **阈值**：> 0.15 正向，< -0.15 负向，之间中性。不用 0 作为分界是因为 NLP 对短文本的置信度低，需要缓冲区。

> 面试官追问：有没有考虑过用 BERT？答：考虑过，但 SnowNLP + 词典在 RTX 3060 上推理 1000 条新闻需要 5 秒（CPU），BERT 需要 30+ 秒。对于日均几千条的量，轻量方案性价比更高。未来可以 Finetune 一个财经专用的小模型。

---

### 3.6 GraphRAG 是什么？怎么实现的？

**回答要点：**

1. **GraphRAG 概念**：不同于传统 RAG（向量检索 + 上下文拼接），GraphRAG 先构建知识图谱，查询时从图谱中检索相关实体和关系，再交给 LLM 生成答案。
2. **构建流程**：
   - LLM 从新闻中抽取实体（公司、人物、指标、事件）和关系
   - 用 NetworkX 构建有向图，节点是实体，边是关系
   - 持久化为 JSON 文件
3. **查询流程**：
   - 用户问题 → LLM 抽取问题中的实体
   - 在图谱中检索相关子图（BFS 1-2 跳邻居）
   - 拼接子图信息 + 相关新闻片段作为 context
   - LLM 基于结构化知识生成回答
4. **LLM 选择**：支持阿里云 DashScope（qwen-plus）/ OpenAI / 百度文心。通过 `.env` 切换。

> 面试官追问：为什么不用向量数据库？答：图谱对实体间关系的表达更精确（A 持股 B 30%），向量检索只能做语义相似度，容易丢失结构化信息。两者可以互补——图谱做精确查询，向量做模糊召回。

---

### 3.7 前端状态管理和缓存策略？

**回答要点：**

1. **缓存策略**：Stale-While-Revalidate 模式
   - sessionStorage 存数据 + 时间戳
   - 渲染时先读缓存（0ms 展示），后台重新请求 API
   - 新闻 TTL 5 分钟，分析 TTL 1 分钟
2. **轮询**：首页每 60s 用 `GET /api/news/fresh`（轻量接口，只返回最新时间戳）检查新内容
3. **请求取消**：组件卸载时 AbortController 取消飞行中的请求，避免内存泄漏和竞态
4. **API Key 安全**：Key 不在前端 bundle 中，由 Nginx 在服务端注入 `X-API-Key` 请求头

---

### 3.8 安全措施有哪些？

**回答要点：**

1. **API Key 认证**：敏感接口（GraphRAG、翻译、管理）需要 X-API-Key
2. **CSRF 防护**：留言板登录/发帖校验 Origin/Referer
3. **限流**：Nginx 层 30r/s + FastAPI 中间件按路径限流（留言板 5/min，搜索 30/min）
4. **安全头**：HSTS、X-Content-Type-Options、X-Frame-Options
5. **Key 不落地前端**：Nginx `proxy_set_header X-API-Key` 注入

---

## 四、重点案例分析：分析页性能优化

> 这可能是面试中最有价值的项目故事。准备 3-5 分钟讲清楚。

### 问题

用户访问分析页（`/analysis`）时，页面长时间转圈或报"无法连接服务器"。

### 排查过程

1. **前端现象**：Axios 返回通用网络错误，不是超时。说明不是"后端慢"，而是"后端不可达"。
2. **查日志**：uvicorn worker 在处理 `/api/analysis` 时 CPU 100%。
3. **读代码**：`analyze_news()` 对每篇文章做 SnowNLP 情感 + jieba 关键词 + TextRank 摘要，并发仅 4 线程（`asyncio.Semaphore(4)`）。
4. **定位根因**：NLP 是 CPU 密集型任务，Python GIL 导致 worker 进程的事件循环被 NLP 线程抢占，无法及时 accept 新连接。Nginx `proxy_connect_timeout 10s` 后返回错误。

### 为什么"切到留言板再回来就好了"？

后端处理在用户离开期间完成了，结果写入 Redis 缓存（300s TTL）。第二次请求命中缓存，秒返。

### 解决方案

**API 端点不再做 NLP**。`analyze_news()` 改为纯 DB 查询 + 聚合：

| 改动前 | 改动后 |
|--------|--------|
| 查 `analysis_results` 表 → 缺失的文章实时 NLP → 聚合返回 | 查 `analysis_results` 表 → 缺失的直接填中性值 → 聚合返回 |
| 首次请求 30-60s | 始终 1-3s |
| CPU 峰值 100% | CPU 平稳 |

NLP 全部交给 scheduler 进程（每 15 分钟预计算）。极端情况最新 15 分钟的文章缺少分析结果，下一轮 scheduler 补上。

### 学到什么

1. **请求路径中不应该有 CPU/IO 密集型任务**：应该异步化或预计算。
2. **缓存预热比缓存策略更重要**：Redis 的 TTL 和淘汰策略只是"怎么存"，预热决定"存什么"。
3. **Python GIL 的影响**：多线程 CPU 密集型任务会阻塞事件循环。正确做法是用独立进程（scheduler）跑 CPU 任务。
4. **监控先行**：如果有 Prometheus 的请求延迟直方图，能更快发现 P99 延迟毛刺。

---

## 五、数据库设计

### news_articles 表（新闻主表）

| 字段 | 类型 | 说明 |
|------|------|------|
| article_id | VARCHAR(32) UNIQUE | URL 的 MD5 前 16 位，唯一标识 |
| title | VARCHAR(500) | 标题 |
| content | TEXT | 正文 |
| summary | TEXT | 摘要 |
| url | VARCHAR(2048) | 原文链接 |
| source | VARCHAR(100) INDEX | 来源（新浪财经、华尔街见闻等） |
| category | VARCHAR(100) | 分类（宏观、股票、外汇等 10 类） |
| published_at | VARCHAR(30) INDEX | 发布时间 |
| read_count | INT DEFAULT 0 | 阅读数 |
| hotness_score | FLOAT DEFAULT 0 | 热度分（时间衰减算法） |
| tags | TEXT | JSON 标签数组 |
| created_at / updated_at | VARCHAR(30) | 时间戳 |

### analysis_results 表（预计算分析结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| article_id | VARCHAR(32) INDEX | 关联新闻 |
| sentiment_score | FLOAT | -1 到 1 情感分 |
| sentiment_label | VARCHAR(20) | positive/neutral/negative |
| keywords | TEXT | 逗号分隔的关键词 |
| summary | TEXT | TextRank 自动摘要 |
| model_used | VARCHAR(50) | snownlp / dict |
| analysis_type | VARCHAR(50) | sentiment / full |

### 设计要点

- article_id 用 URL 的 MD5→16 位，不用自增 ID。好处是同一 URL 重新爬取时能 UPSERT 更新，不会重复插入。
- 分析结果独立建表，和新闻主表解耦。scheduler 写，API 只读。
- 支持 SQLite 和 PostgreSQL 双模式，通过 `DATABASE_URL` 环境变量切换。SQL 用兼容写法（`sql_helpers.py`）。

---

## 六、面试模拟问答

### Q1: 为什么选这个项目？

> 我对财经和 NLP 都感兴趣，想做一个能落地的全栈项目。技术栈覆盖了"数据采集→处理→分析→可视化"的完整链路，涉及异步编程、NLP 算法、缓存策略、系统架构设计等多方面技能，能充分展示我的全栈能力。

### Q2: 最大的技术挑战是什么？

> 分析页的性能瓶颈。NLP 处理在 API 请求内同步执行，导致请求超时。我做了根因分析，最终把 NLP 从请求路径移到后台调度器，API 改为纯读取预计算结果。这个优化把响应时间从 30-60s 降到 1-3s。

### Q3: 如果要支持 100 倍的数据量，怎么扩展？

- **数据库**：SQLite → PostgreSQL + 读写分离（API 读从库，scheduler 写主库）
- **NLP**：SnowNLP → GPU 推理（ONNX 加速），或换成 FinBERT 等预训练模型
- **缓存**：Redis Cluster 分片，分析结果缓存 TTL 拉长到 1h
- **爬取**：增加机器数，用消息队列（Redis Stream / Kafka）分发任务
- **搜索**：Whoosh → Elasticsearch，分布式索引

### Q4: 为什么不用 Celery 做异步任务？

> 当前规模不需要。scheduler 是单进程 Python 脚本，两个 asyncio 循环覆盖了"15 分钟爬取"和"每日分析"的需求。引入 Celery 会增加 RabbitMQ/Redis 依赖和运维复杂度。当未来爬取源增加到 50+、需要分布式执行时再引入。

### Q5: 代码里有没有单元测试？覆盖率怎么样？

> 有 pytest + pytest-asyncio。测试覆盖了熔断器、热度算法、SQL 兼容工具等核心逻辑。数据库测试用内存 SQLite（`sqlite+aiosqlite:///:memory:`），不需要外部依赖。

### Q6: 前端主题切换怎么实现的？

> Ant Design 5 的 ConfigProvider + CSS 变量。定义了两套调色板（日间 8 色、夜间 8 色），通过 React Context 全局共享，localStorage 持久化用户偏好。

---

## 七、系统关键数字（面试时可以引用）

| 指标 | 数值 |
|------|------|
| 数据源数量 | 25 个 |
| API 端点数量 | 40+ |
| 爬取频率 | 每 15 分钟 |
| 全量分析频率 | 每天凌晨 2:00 |
| API workers | 8 进程 |
| NLP 并发 | 4 线程池 |
| 前端缓存 TTL | 新闻 5min / 分析 1min |
| Redis 降级容量 | 200 条（内存模式） |
| 限流 | Nginx 30r/s + 路径级限流 |
| 图表类型 | 饼图、柱状图、折线图、标签云 |
| 前端包大小 | 懒加载，每个页面独立 chunk |

---

## 八、面试前的检查清单

- [ ] 能在白板上画出系统架构图
- [ ] 能解释爬取→去重→存储→NLP→缓存→API→前端的完整数据流
- [ ] 能讲清楚分析页性能优化的完整故事（问题→排查→根因→方案→结论）
- [ ] 了解每个技术选型的替代方案和 trade-off
- [ ] 能回答"如果再来一次，你会怎么设计"（预计算 + 更好的 NLP 模型）
- [ ] 准备好 2-3 个"我主动发现的改进点"（性能优化、缓存策略、安全加固）
- [ ] 服务器能正常访问演示（https://flowcapital.cn）
