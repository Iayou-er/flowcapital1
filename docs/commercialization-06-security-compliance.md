# 安全加固与合规规范

## 现状问题

- 传输层无强制 HTTPS（Nginx 可配但未强制）
- 无安全响应头（CSP、HSTS、X-Content-Type-Options 等）
- CORS 生产策略不够严格
- 无爬虫合规声明（robots.txt、User-Agent 规范）
- 无隐私政策页面
- 无 SQL 注入深度防护审查

## 目标

- 传输加密强制 HTTPS（TLS 1.2+），证书自动续期
- 安全头配置（CORS、CSP、HSTS）
- 数据合规（隐私政策、数据删除、爬虫合规）
- OWASP Top 10 基础防护
- 敏感配置管理（环境变量隔离）

## 实施方案

### 1. HTTPS 强制 + 证书管理

Nginx 配置：

```nginx
server {
    listen 80;
    server_name flowcapital.cn;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name flowcapital.cn;

    ssl_certificate     /etc/nginx/ssl/fullchain.pem;
    ssl_certificate_key /etc/nginx/ssl/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # HSTS：第 1 周 max-age=86400（1 天），确认无问题后逐步提升
    # 第 2 周 → 604800（7 天），第 4 周 → 31536000（1 年）
    add_header Strict-Transport-Security "max-age=86400" always;
}
```

Let's Encrypt 证书自动续期（certbot）：

```bash
# 首次获取证书
certbot certonly --webroot -w /var/www/html -d flowcapital.cn

# 自动续期（systemd timer，certbot 安装后自带）
systemctl enable certbot.timer
systemctl start certbot.timer

# 验证自动续期
certbot renew --dry-run
```

Nginx SSL 证书路径（certbot 默认输出）：

```
ssl_certificate     /etc/letsencrypt/live/flowcapital.cn/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/flowcapital.cn/privkey.pem;
```

HSTS 升级时间表：

| 时间点 | max-age | 说明 |
|--------|---------|------|
| 部署后第 1 周 | 86400（1 天） | 验证 HTTPS 无功能问题 |
| 部署后第 2 周 | 604800（7 天） | 验证混合内容、CSP 无报错 |
| 部署后第 4 周 | 31536000（1 年） | 长期锁定，可提交 HSTS preload |

### 2. 安全响应头

```python
# backend/middleware/security_headers.py
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        # CSP 最小化：默认仅允许本站资源
        # 如有 CDN/第三方资源，按需在 script-src / style-src 中追加域名
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "font-src 'self'; "
            "connect-src 'self'"
        ),
    })
    return response
```

CSP 调优指南：

| 场景 | 调整 |
|------|------|
| 前端框架需要内联样式（Vue SFC / styled-components） | `style-src 'unsafe-inline'` 已包含 |
| 需要内联脚本 | 改用 nonce 方案：后端生成随机 nonce → 注入 CSP 头 + `<script nonce="...">`，避免全局 `'unsafe-inline'` |
| 引用外部 CDN 脚本 | `script-src 'self' https://cdn.example.com` |
| 前端直连外部 API | `connect-src 'self' https://api.example.com` |

生产部署前用浏览器 Console 检查 CSP 违规报告并逐条处理。

### 3. CORS 生产策略

```python
import os

# 开发环境
CORS_ORIGINS = ["http://localhost:3000"]

# 生产环境 — 显式白名单，禁止通配符
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "https://flowcapital.cn").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
    max_age=3600,
)
```

### 4. 敏感配置管理

所有密钥通过环境变量注入，禁止硬编码：

| 配置项 | 环境变量 | 说明 |
|--------|----------|------|
| API Key | `API_KEY` | 服务间调用认证 |
| LLM API Key | `LLM_API_KEY` | 大模型调用 |
| 数据库密码 | `DATABASE_URL` | 含在连接串中 |
| 告警 Webhook | `DINGTALK_WEBHOOK`、`WECOM_WEBHOOK` | 通知渠道 |

`.env` 文件不得提交到版本控制（已在 `.gitignore` 中）。

### 5. 爬虫合规

```python
# 爬虫 User-Agent 规范化
CRAWLER_USER_AGENT = (
    "FlowCapital/1.0 (Economic News Aggregator; "
    "https://flowcapital.cn/robots.txt; "
    "compliance@flowcapital.cn)"
)

# 每个源的爬取间隔 ≥ 5 秒
MIN_CRAWL_INTERVAL = 5  # seconds
```

`robots.txt`（部署到 `https://flowcapital.cn/robots.txt`）：

```text
User-agent: *
Allow: /
Disallow: /api/admin/
Crawl-delay: 5
```

### 6. 隐私政策清单

商用前必须准备：

| 文档 | 内容要求 |
|------|----------|
| 隐私政策 | 收集哪些数据、用途、存储期限、用户权利 |
| 用户协议 | 服务条款、免责声明、知识产权 |
| 数据删除指引 | 匿名留言数据删除方式、处理时限 |

匿名留言板相关说明：
- `anonymous_id` 基于浏览器 localStorage 生成，不关联个人身份信息
- 留言内容存储于服务端，用户可凭留言时间 + anonymous_id 请求删除
- 删除请求需由网站管理员手动处理，处理时限 ≤ 30 天

### 7. SQL 注入防护审查

当前防护措施：参数化查询 + Pydantic 校验。需确保无遗漏：

```python
# 禁止：字符串拼接 SQL
# query = f"SELECT * FROM news WHERE title LIKE '%{keyword}%'"  ← 高危

# 正确：参数化查询（SQLAlchemy text() 支持 :param 占位符，兼容 SQLite/PG）
query = "SELECT * FROM news WHERE title LIKE :keyword"
params = {"keyword": f"%{keyword}%"}

# 动态 ORDER BY 必须白名单
ALLOWED_SORT_COLUMNS = {'published_at', 'hotness_score', 'read_count'}
def safe_order_by(column: str) -> str:
    if column not in ALLOWED_SORT_COLUMNS:
        raise ValueError(f"Invalid sort column: {column}")
    return column

# 动态 LIMIT/OFFSET 必须转为整数并限制范围
def safe_limit(raw_value, max_limit: int = 100) -> int:
    return min(max(1, int(raw_value)), max_limit)
```

### 8. 留言板安全

```python
# 输入校验（Pydantic 模型 + nh3 清洗 HTML）
from pydantic import BaseModel, Field, field_validator
import nh3

class GuestBookEntry(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)
    anonymous_id: str = Field(..., min_length=8, max_length=64)

    @field_validator('content')
    @classmethod
    def sanitize_content(cls, v: str) -> str:
        # nh3 清洗 HTML 标签，保留文本内容
        # 注意：nh3.clean 对 "x < y" 类文本正确处理（< 后接空格不构成标签）
        return nh3.clean(v, tags=set(), attributes={}, strip=True)
```

`nh3` 是最小的 HTML 净化库（Rust 实现，`ammonia` 的 Python 绑定），比 `bleach` 更活跃维护且更快。

前端渲染留言内容时必须转义 HTML：

```vue
<!-- 使用 v-text 或 {{ }} 自动转义，禁止 v-html -->
<p>{{ entry.content }}</p>
```

### 9. 速率限制加固

现有 `backend/middleware/rate_limiter.py` 已提供基于 IP 的滑动窗口限流，生产部署确保：

| 端点 | 限制 | 说明 |
|------|------|------|
| `POST /api/guest-book/submit` | 5 req/min | 匿名留言，防刷 |
| `GET /api/news/*` | 60 req/min | 新闻查询 |
| `POST /api/graph-rag/*` | 20 req/min | LLM 消耗大 |

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/middleware/security_headers.py` | **新建**，安全响应头中间件 |
| `deploy/nginx.conf` | HTTPS 强制 + HSTS + SSL 配置 |
| `backend/api/main.py` | 生产 CORS 策略 + 安全头中间件 |
| `backend/api/routes/guest_book.py` | 留言输入 Pydantic 校验 + nh3 清洗 |
| `backend/crawler/news_crawler.py` | User-Agent 规范化 |
| `frontend/public/robots.txt` | **新建** |
| `frontend/src/views/PrivacyPolicy.vue` | **新建**，隐私政策页面 |
| `requirements.txt` | 新增 `nh3` |

## 验收标准

- [ ] HTTP 请求自动 301 到 HTTPS
- [ ] Let's Encrypt 证书自动续期已配置（`certbot.timer` 启用）
- [ ] HSTS 按时间表升级（1 天 → 7 天 → 1 年）
- [ ] 所有响应包含安全头（X-Content-Type-Options、X-Frame-Options、CSP）
- [ ] CORS 仅允许 `CORS_ORIGINS` 白名单域名
- [ ] 动态 ORDER BY 使用白名单校验
- [ ] robots.txt 可访问（`https://flowcapital.cn/robots.txt`）
- [ ] 隐私政策页面可从前端访问
- [ ] `.env` 不在 git 跟踪中
- [ ] 爬虫 User-Agent 含联系方式
- [ ] 留言板 content 经 nh3 清洗，前端使用模板语法转义（非 v-html）
- [ ] 留言板 POST 接口有限流保护
