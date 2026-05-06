# 经济新闻分析系统

基于 React 前端 + FastAPI 后端的经济新闻爬取、分析和知识图谱检索系统。

## 功能特性

- 实时新闻展示与分类浏览
- 新闻关键词搜索
- 新闻详情查看（含情感分析与关键词标签）
- 数据统计分析（分类分布、新闻趋势、热门关键词）
- GraphRAG 知识图谱问答
- 响应式布局，支持多端访问

## 技术栈

| 层 | 技术 |
|---|------|
| 前端 | React 18, Ant Design 5, Axios, Recharts, React Router 6 |
| 后端 | Python 3, FastAPI, SQLite |
| 分析 | 情感分析, 知识图谱 (GraphRAG) |

## 项目结构

```
├── backend/
│   ├── api/
│   │   ├── main.py              # FastAPI 入口，CORS 配置，路由注册
│   │   └── routes/
│   │       ├── news.py          # 新闻 CRUD 与搜索
│   │       ├── analysis.py      # 新闻统计分析
│   │       └── graph_rag.py     # GraphRAG 知识图谱问答
│   ├── crawler/                 # 新闻爬虫
│   ├── analyzer/                # 文本分析与情感分析
│   └── database/
│       ├── db_manager.py        # SQLite 数据库管理
│       └── models.py            # 数据模型
├── economic-news-webapp/
│   ├── src/
│   │   ├── components/          # 可复用组件 (Header, NewsCard, SearchBar...)
│   │   ├── pages/               # 页面 (Home, NewsDetail, Category, Analysis...)
│   │   ├── services/api.js      # Axios 实例与统一响应处理
│   │   ├── utils/helpers.js     # 工具函数
│   │   └── styles/              # 全局样式
│   └── .env.example             # 环境变量模板
└── run_project.py               # 项目启动脚本
```

## 快速开始

### 环境要求

- Node.js 14+ / npm 6+
- Python 3.8+
- SQLite 3（内置）

### 1. 启动后端 API

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 启动 FastAPI 服务（默认端口 8001）
uvicorn backend.api.main:app --reload --port 8001
```

### 2. 启动前端

```bash
cd economic-news-webapp

# 安装依赖
npm install

# 可选：配置环境变量
cp .env.example .env.local
# 编辑 .env.local 中的 REACT_APP_API_URL

# 启动开发服务器
npm start
```

前端默认运行在 `http://localhost:3000`。

### 3. 启动爬虫（可选）

```bash
python run_project.py
# 选择 "1" 启动调度器，定时爬取经济新闻
```

## 环境变量

### 前端 (`.env.local`)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REACT_APP_API_URL` | `http://localhost:8001` | 后端 API 地址 |

### 后端

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API_KEY` | *(空)* | LLM API 密钥（GraphRAG 需要） |
| `LLM_API_ENDPOINT` | `https://dashscope.aliyuncs.com/api/v1` | LLM API 端点 |
| `LLM_API_TYPE` | `aliyun` | LLM 服务商类型 |

> GraphRAG 功能在未配置 `LLM_API_KEY` 时以受限模式运行，不影响新闻和统计接口。

## API 接口

### 新闻 (`/api/news`)

| 方法 | 路径 | 说明 | 参数 |
|------|------|------|------|
| GET | `/api/news/latest` | 获取最新新闻 | `limit` (默认 20) |
| GET | `/api/news/search` | 搜索新闻 | `keyword` (必填), `limit` |
| GET | `/api/news/category/{category}` | 按分类获取 | `category`, `limit` |
| GET | `/api/news/{article_id}` | 获取新闻详情 | `article_id` |

### 分析 (`/api/analysis`)

| 方法 | 路径 | 说明 | 参数 |
|------|------|------|------|
| GET | `/api/analysis` | 新闻统计分析 | `time_range`, `start_date`, `end_date` |

### GraphRAG (`/api/graph-rag`)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/graph-rag/query` | 图谱问答 |
| POST | `/api/graph-rag/build-graph` | 构建知识图谱 |
| GET | `/api/graph-rag/info` | 图谱信息 |
| POST | `/api/graph-rag/query-test` | 接口测试 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |

### 响应格式

所有接口统一返回：

```json
{
  "code": 0,
  "data": { ... },
  "message": "可选错误信息"
}
```

- `code: 0` 表示成功，非 0 表示业务错误
- `data` 为实际响应数据
- `message` 在错误时提供人类可读描述

## 前后端连接说明

- 前端通过 `src/services/api.js` 中的 Axios 实例与后端通信
- 响应拦截器自动解包 `{code, data}` 格式，组件直接使用 `data`
- 开发环境通过 CORS 配置访问后端（`main.py` 中 `allow_origins` 配置）
- 生产环境建议使用 Nginx 反向代理，避免跨域问题
