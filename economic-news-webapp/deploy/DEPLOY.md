# 经济新闻分析系统 — 阿里云 ECS 部署指南

## 前置准备

### 1. 购买阿里云 ECS 实例

| 配置项 | 推荐值 |
|---|---|
| 操作系统 | Ubuntu 22.04 LTS |
| CPU | 2 核 |
| 内存 | 4 GB |
| 系统盘 | 40 GB SSD |
| 带宽 | 按量付费 5 Mbps |

### 2. 配置安全组

在 ECS 控制台 → 安全组中放行以下端口：

| 端口 | 协议 | 用途 |
|---|---|---|
| 22 | TCP | SSH 远程登录 |
| 80 | TCP | HTTP 访问 |
| 443 | TCP | HTTPS 访问 |

### 3. 域名解析（可选）

在阿里云 DNS 控制台将域名 A 记录指向 ECS 公网 IP。

---

## 部署步骤

### 第一步：本地构建前端

```bash
cd economic-news-webapp
npm run build
```

构建产物在 `economic-news-webapp/build/` 目录下。

### 第二步：修改生产环境变量

编辑 `deploy/.env.production`：

```bash
# 将 your-domain.com 替换为你的域名或 ECS 公网 IP
CORS_ORIGINS=http://your-domain.com

# 填入你的 DashScope API Key
LLM_API_KEY=你的实际API密钥

# 生成强随机 API Key（可选）
API_KEY=$(openssl rand -hex 16)
```

### 第三步：上传文件到 ECS

```bash
# 设置变量（替换为你的 ECS 公网 IP）
ECS_IP=你的ECS公网IP

# 1. 上传前端构建产物
scp -r economic-news-webapp/build/* root@$ECS_IP:/var/www/economic-news/

# 2. 上传后端代码（排除 __pycache__ 和 node_modules）
rsync -avz --exclude '__pycache__' --exclude '*.pyc' --exclude 'data/' --exclude 'logs/' backend/ root@$ECS_IP:/opt/economic-news/backend/

# 3. 上传 graph_rag 模块
rsync -avz --exclude '__pycache__' graph_rag/ root@$ECS_IP:/opt/economic-news/graph_rag/

# 4. 上传后端依赖和配置
scp deploy/requirements.txt root@$ECS_IP:/opt/economic-news/requirements.txt
scp deploy/.env.production root@$ECS_IP:/opt/economic-news/.env
scp deploy/backend.service root@$ECS_IP:/opt/economic-news/backend.service
scp scheduler.py root@$ECS_IP:/opt/economic-news/scheduler.py
```

### 第四步：SSH 登录 ECS 执行部署

```bash
ssh root@$ECS_IP
```

#### 4.1 安装系统依赖

```bash
apt update
apt install -y nginx redis-server python3 python3-pip python3-venv
```

#### 4.2 启动 Redis

```bash
systemctl enable redis-server
systemctl start redis-server
redis-cli ping  # 应返回 PONG
```

#### 4.3 安装 Python 依赖

```bash
cd /opt/economic-news
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 4.4 安装后端 systemd 服务

```bash
# 确保 .env 文件在正确位置
cp /opt/economic-news/backend.service /etc/systemd/system/

# 修改 service 文件中的路径（已正确则跳过）
# 确保 WorkingDirectory=/opt/economic-news
# 确保 EnvironmentFile=/opt/economic-news/.env

systemctl daemon-reload
systemctl enable backend.service
systemctl start backend.service

# 检查状态
systemctl status backend.service
curl http://127.0.0.1:8001/api/health  # 应返回健康检查响应
```

#### 4.5 配置 Nginx

```bash
# 上传 nginx 配置（或直接在服务器上创建）
cat > /etc/nginx/sites-available/economic-news << 'EOF'
server {
    listen 80;
    server_name 你的域名或IP;  # 替换

    # 前端静态资源
    location / {
        root /var/www/economic-news;
        index index.html;
        try_files $uri $uri/ /index.html;
    }

    # 反向代理后端 API
    location /api/ {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # 静态资源缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2)$ {
        root /var/www/economic-news;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript image/svg+xml;
}
EOF

# 启用站点配置
ln -sf /etc/nginx/sites-available/economic-news /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
```

#### 4.6 开启 HTTPS（可选但推荐）

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d 你的域名
```

---

## 验证部署

### 检查各服务状态

```bash
# 后端 API 服务
systemctl status backend.service

# Nginx
systemctl status nginx

# Redis
systemctl status redis-server
```

### 测试 API

```bash
curl http://你的域名或IP/api/health
curl http://你的域名或IP/api/news/latest?limit=1
```

### 访问前端

浏览器打开 `http://你的域名或IP`，应能看到完整的前端页面。

---

## 日常运维

### 查看日志

```bash
# 后端服务日志
journalctl -u backend.service -f

# Nginx 访问日志
tail -f /var/log/nginx/access.log

# Nginx 错误日志
tail -f /var/log/nginx/error.log
```

### 重启服务

```bash
systemctl restart backend.service
systemctl restart nginx
```

### 更新代码

```bash
# 前端更新：本地构建后重新上传
npm run build
scp -r build/* root@ECS_IP:/var/www/economic-news/

# 后端更新：上传代码后重启服务
rsync -avz backend/ root@ECS_IP:/opt/economic-news/backend/
ssh root@ECS_IP "systemctl restart backend.service"
```

### 定时爬虫（可选）

```bash
# 在 ECS 上启动调度器
cd /opt/economic-news
source venv/bin/activate
nohup python3 scheduler.py > /var/log/scheduler.log 2>&1 &
```

---

## 常见问题

### 1. 后端服务启动失败

```bash
# 查看详细错误
journalctl -u backend.service --no-pager | tail -50

# 手动启动测试
cd /opt/economic-news
source venv/bin/activate
uvicorn backend.api.main:app --host 127.0.0.1 --port 8001
```

### 2. Nginx 502 Bad Gateway

说明后端 API 未正常运行：

```bash
systemctl status backend.service
curl http://127.0.0.1:8001/api/health
```

### 3. CORS 错误

检查 `.env` 中的 `CORS_ORIGINS` 是否包含前端域名。

### 4. 前端页面空白

```bash
# 检查 build 文件是否正确上传
ls -la /var/www/economic-news/index.html

# 检查 Nginx 配置
nginx -t
cat /etc/nginx/sites-available/economic-news
```

### 5. Redis 连接失败

```bash
redis-cli ping  # 应返回 PONG
# 如果失败，检查 Redis 是否启动
systemctl status redis-server
```
