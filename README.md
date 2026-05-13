# 经济新闻分析系统

自动化经济新闻采集、存储、分析和展示平台。25 个数据源并行爬取，含情感分析、全文搜索、知识图谱、多语言翻译和 Web 可视化看板。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Python 3.8+, FastAPI, Uvicorn |
| 数据库 | SQLite (WAL 模式 + 读写分离), Redis (自动降级) |
| 爬虫 | Requests + BeautifulSoup + lxml + Playwright, ThreadPoolExecutor 并行 |
| 分析 | SnowNLP (情感), jieba + TF-IDF (关键词), TextRank (摘要), SimHash 桶索引 (去重) |
| 搜索 | Whoosh + jieba 中文分词 |
| 图谱 | NetworkX + GraphRAG (LLM) |
| 前端 | React, Ant Design 5, Recharts, Service Worker |
| 部署 | Docker Compose / Nginx + systemd |

## 快速开始

### 环境要求

- Python 3.8+, Node.js 14+, npm 6+
- Redis（可选，不安装时自动使用内存缓存）

### 安装

```bash
# 后端
pip install -r scripts/requirements.txt
playwright install chromium

# 前端
cd economic-news-webapp && npm install
```

### 配置

```bash
cp .env.example .env
# 编辑 .env，填入 API 密钥等配置
```

### 启动

```bash
# 一键启动（API 服务 + 定时调度器）
python scripts/run_project.py

# 或分别启动
python scripts/scheduler.py                          # 定时爬取
uvicorn backend.api.main:app --host 0.0.0.0 --port 8001  # API 服务
cd economic-news-webapp && npm start                 # 前端 (localhost:3000)
```

### Docker 部署

```bash
docker-compose up -d
```

## API 概览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/news/latest?page=1&limit=20` | GET | 最新新闻（排除自媒体源） |
| `/api/news/search?q=关键词` | GET | 全文搜索 |
| `/api/news/category/{name}` | GET | 按分类浏览 |
| `/api/news/media` | GET | 自媒体板块（7 源独立展示） |
| `/api/news/media/search?q=` | GET | 自媒体搜索 |
| `/api/news/{id}` | GET | 新闻详情 |
| `/api/news/{id}/translate` | POST | 翻译新闻 |
| `/api/news/translate` | POST | 通用文本翻译 |
| `/api/news/duplicates` | GET | 重复新闻检测 |
| `/api/news/rebuild-index` | POST | 重建搜索索引（需认证） |
| `/api/analysis` | GET | 分析概览 |
| `/api/analysis/breakdown` | GET | 情感细分 |
| `/api/analysis/entities` | GET | 实体识别 |
| `/api/analysis/hot-topics` | GET | 热点话题 |
| `/api/analysis/clusters` | GET | 文章聚类 |
| `/api/graph-rag/build-graph` | POST | 构建知识图谱（需认证） |
| `/api/graph-rag/query` | POST | 图谱查询 |
| `/api/guest/assign-id` | GET | 分配匿名用户ID |
| `/api/guest/messages` | GET/POST | 留言板 |
| `/api/admin/status` | GET | 系统状态 |

## 数据源

**快速源 (14)**：金十数据、华尔街见闻、同花顺、财联社、雪球、东方财富公告、36氪、新华网RSS、财新RSS、经济日报RSS、虎嗅RSS、少数派RSS、观察者网RSS、AKShare

**慢速源 (10)**：东方财富、新浪财经、搜狗微信 (Playwright)、钛媒体、财新网、界面新闻、网易财经、第一财经、同花顺网页、百度财经

## 项目结构

```
├── backend/
│   ├── crawler/          # 爬虫（25 源并行, HTTP 重试+断路器）
│   ├── database/         # SQLite + Redis 缓存
│   ├── analyzer/         # 情感分析/关键词/摘要/去重/搜索/翻译
│   ├── api/              # FastAPI 路由（新闻/分析/图谱/留言）
│   └── middleware/        # 频率限制
├── graph_rag/            # GraphRAG 知识图谱
├── scripts/              # 调度器 + 启动脚本
├── economic-news-webapp/ # React 前端
├── deploy/               # Nginx + systemd 部署配置
├── docker-compose.yml    # Docker 一键部署
└── .env.example          # 环境变量模板
```

## 生产部署

详见 `deploy/` 目录和 `docker-compose.yml`。

1. `cp .env.example .env` 并修改配置（`ENVIRONMENT=production`, `ENABLE_AUTH=true`, 强随机 `API_KEY`）
2. 构建前端：`cd economic-news-webapp && npm run build`
3. 部署 API + Nginx：参考 `deploy/nginx.conf` 和 `deploy/backend.service`

## 热更新

服务器上执行一条命令即可更新到最新版本：

```bash
cd /opt/flowcapital && ./update.sh
```

**首次配置：**

```bash
cat > /opt/flowcapital/update.sh << 'EOF'
#!/bin/bash
set -e
cd /opt/flowcapital
git pull
source venv/bin/activate
pip install -r scripts/requirements.txt -q
cd economic-news-webapp
npm run build
cp -r build/* /var/www/flowcapital/
cd /opt/flowcapital
systemctl restart flowcapital-api flowcapital-scheduler
echo "更新完成"
EOF

chmod +x /opt/flowcapital/update.sh
```

## 许可证

仅供学习和研究使用，请遵守相关法律法规和网站使用条款。
