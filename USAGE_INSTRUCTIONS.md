# 使用说明：CMD中正确使用经济新闻分析系统

## 系统启动流程

### 1. 环境准备
确保已安装Python 3.8+和Node.js 14+，并已安装所有依赖包：

```cmd
# 进入项目根目录
cd C:\Users\jiahui58.liu\.claude

# 安装后端依赖
pip install -r requirements.txt

# 创建必需的目录
mkdir data logs
```

### 2. 启动系统服务

#### 方法一：分步启动（推荐用于开发调试）
在CMD中同时打开多个窗口：

**窗口1：启动定时任务**
```cmd
cd C:\Users\jiahui58.liu\.claude
python scheduler.py
```

**窗口2：启动API服务**
```cmd
cd C:\Users\jiahui58.liu\.claude
python -m backend.api.main
```

**窗口3：启动前端应用**

```cmd
cd C:\Users\jiahui58.liu\.claude\economic-news-webapp
npm start
```

#### 方法二：单命令启动所有服务（需要后台运行）
```cmd
# 启动后台进程（可选）
start python scheduler.py
start python backend/api/main.py

# 启动前端
cd C:\Users\jiahui58.liu\.claude\economic-news-webapp
npm start
```

## 在CMD中操作爬虫和API

### 1. 查看当前爬虫功能状态
```cmd
# 获取最新的新闻（会自动获取当天所有新闻）
curl "http://localhost:8000/api/news/latest?limit=10"

# 获取分类新闻（默认也会获取当天新闻）
curl "http://localhost:8000/api/news/category/economy?limit=10"
```

### 2. 手动触发新闻爬取
使用以下命令可强制爬取新闻：
```cmd
# 爬取30条当天新闻
curl -X POST "http://localhost:8000/api/news/crawl?limit=30"

# 爬取50条当天新闻
curl -X POST "http://localhost:8000/api/news/crawl?limit=50"
```

### 3. 获取分析结果
```cmd
# 获取最新分析结果
curl "http://localhost:8000/api/analysis/news"

# 获取特定时间范围的分析结果
curl "http://localhost:8000/api/analysis/news?start_date=2023-01-01&end_date=2023-12-31"
```

### 4. 图谱查询（如果已启用）
```cmd
# 构建知识图谱
curl -X POST "http://localhost:8000/api/graph-rag/build-graph?limit=50"

# 知识图谱查询
curl -X POST "http://localhost:8000/api/graph-rag/query" -H "Content-Type: application/json" -d "{\"query\":\"中国经济发展\",\"k\":10}"
```

## CMD常用命令总结

### 系统管理命令
```cmd
# 查看运行中的Python进程
tasklist | findstr python

# 查看端口占用情况
netstat -ano | findstr 8000

# 查看日志
type logs\scheduler.log
type logs\api.log

# 结束进程（替换PID为实际进程ID）
taskkill /PID 1234 /F
```

### 常用API调用示例
```cmd
# 获取最新新闻（默认当天新闻）
curl "http://localhost:8000/api/news/latest?limit=20"

# 获取特定时间范围新闻（非默认：如需定制）
curl "http://localhost:8000/api/news/latest?limit=20&start_date=2023-01-01&end_date=2023-12-31"

# 爬取当天所有新闻
curl -X POST "http://localhost:8000/api/news/crawl?limit=50"

# 获取分析结果
curl "http://localhost:8000/api/analysis/news"

# 知识图谱构建
curl -X POST "http://localhost:8000/api/graph-rag/build-graph?limit=100"
```

## 注意事项

1. **默认行为**：爬虫模块已优化，默认自动获取当天的新闻，无需额外配置
2. **定时任务**：系统配置了每30分钟自动爬取最新的经济新闻，坚持每天获取
3. **数据访问**：前端默认运行在 http://localhost:3000，API服务运行在 http://localhost:8000
4. **重启服务**：如需重启爬虫服务，可重新运行 `python scheduler.py` 命令
5. **数据库维护**：数据存储在 `data/sqlite.db` 中，请定期备份

## 故障排除

如果遇到问题，可以尝试：
```cmd
# 重新安装依赖
pip install --upgrade --force-reinstall -r requirements.txt

# 清除数据库并重新开始
del data\sqlite.db

# 检查端口使用情况
netstat -ano | findstr 8000
```