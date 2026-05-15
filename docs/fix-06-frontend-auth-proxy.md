# 前端 API Key 移除后的认证修复规范

## 现状

安全审计发现 `REACT_APP_API_KEY` 嵌入 JS bundle 导致泄露，已从 `economic-news-webapp/.env` 和 `src/services/api.js` 中移除。

**后果**：前端所有需要 API Key 的写操作现在返回 401：
- 翻译文章（`/api/news/translate`, `/api/news/{id}/translate`）
- 提交留言（`/api/guest/message`）
- GraphRAG 查询（`/api/graph-rag/query`）
- 重建搜索索引（`/api/news/rebuild-index`，管理操作）

---

## 修复方案

### 方案 A：Nginx 服务端注入（推荐，零前端改动）

Nginx 在反向代理时注入 `X-API-Key` 请求头，客户端完全不知情。

**已配置**（`deploy/nginx.conf`）：

```nginx
location /api/ {
    # 服务端注入 API Key
    set $api_key "";
    include /etc/nginx/conf.d/api_key.conf;
    proxy_set_header X-API-Key $api_key;
    ...
}
```

**部署步骤**：

```bash
# 0. 首次部署前创建占位文件（否则 nginx 启动失败）
sudo touch /etc/nginx/conf.d/api_key.conf
sudo chmod 600 /etc/nginx/conf.d/api_key.conf

# 1. 写入生产 API Key
echo 'set $api_key "YOUR_PRODUCTION_API_KEY";' | sudo tee /etc/nginx/conf.d/api_key.conf > /dev/null

# 2. 设置权限（仅 root/nginx 可读）
sudo chmod 600 /etc/nginx/conf.d/api_key.conf
sudo chown root:root /etc/nginx/conf.d/api_key.conf

# 3. 测试并重载 Nginx
sudo nginx -t && sudo nginx -s reload
```

**安全优势**：
- API Key 不出现在任何客户端代码或浏览器 DevTools 中
- Key 存储在 root 权限文件，Web 进程无法读取
- Key 仅在 Nginx → uvicorn 的内部网络中传输

---

### 方案 B：区分读/写端点，只保护写操作（当前架构已支持）

当前 `ENABLE_AUTH=true` 时，读操作（`/api/news/*` GET, `/api/analysis/*` GET）不需要 Key。

排查哪些端点真正需要前端调用：

| 端点 | 方法 | 调用方 | 需要 Key |
|------|------|--------|----------|
| `/api/guest/message` | POST | 前端用户 | **是** |
| `/api/guest/assign-id` | GET | 前端用户 | 否（公开） |
| `/api/guest/login` | POST | 前端用户 | 否（公开+CSRF） |
| `/api/graph-rag/query` | POST | 前端用户 | **是** |
| `/api/news/translate` | POST | 前端用户 | **是** |
| `/api/news/{id}/translate` | GET | 前端用户 | **是** |
| `/api/news/rebuild-index` | POST | 管理员 | **是** |
| `/api/admin/status` | GET | 管理员 | **是** |
| `/api/graph-rag/build-graph` | POST | 管理员 | **是** |

**结论**：前端需要 Key 的端点有 5 个（留言、翻译、GraphRAG 查询）。

---

### 方案 C：会话代理（最安全，需前端改动）

创建一个后端代理端点，前端通过会话 Cookie 认证，由后端持有 API Key 调用内部服务。

```python
# 新增 backend/api/routes/proxy.py
@proxy_router.post("/proxy/translate")
async def proxy_translate(request: TranslateTextRequest, session: str = Depends(get_session)):
    """前端通过 Session 认证，后端注入 API Key 调用翻译服务"""
    # 验证 session 有效 → 调用 translator.auto_translate() → 返回结果
```

**优点**：前端完全无感知 Key 的存在
**缺点**：需要前端配合修改 API 调用路径

---

## 推荐实施路径

| 阶段 | 方案 | 说明 |
|------|------|------|
| 立即 | A（Nginx 注入） | 配置 `api_key.conf`，重载 Nginx，前端写操作立即恢复 |
| 短期 | B（审计端点） | 确认仅 5 个前端端点需要 Key，其余保持公开 |
| 长期 | C（会话代理） | 前端迁移到 Session 认证，彻底消除 Key 暴露风险 |

---

## 验证清单

- [ ] Nginx `api_key.conf` 文件存在且权限 600
- [ ] `curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost/api/graph-rag/query -H "Content-Type: application/json" -d '{"query":"test"}'` 返回 200（Key 由 Nginx 注入）
- [ ] `curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost/api/graph-rag/query -H "Content-Type: application/json" -d '{"query":"test"}' -H "X-API-Key: wrong"` 返回 200（Nginx 覆盖了客户端 Key）
- [ ] 前端翻译功能可用（`/api/news/translate`）
- [ ] 前端留言功能可用（`/api/guest/message`）
- [ ] 前端 GraphRAG 查询可用（`/api/graph-rag/query`）
- [ ] 浏览器 Network 面板中看不到 X-API-Key 响应头
- [ ] 浏览器 JS bundle 中搜索不到 API Key 字符串

---

## Docker 部署适配

如果使用 Docker Compose 部署，在 `docker-compose.yml` 的 nginx 服务中添加 volume 挂载：

```yaml
# docker-compose.yml
services:
  nginx:
    # ... 现有配置
    volumes:
      - ./deploy/nginx/nginx.conf:/etc/nginx/nginx.conf:ro   # 现有
      - ./deploy/nginx/api_key.conf:/etc/nginx/conf.d/api_key.conf:ro  # 新增
```

创建 `deploy/nginx/api_key.conf`（纳入 `.gitignore`）：

```nginx
set $api_key "YOUR_KEY_HERE";
```

将该文件加入 `.gitignore`（已在 `.gitignore` 的 `deploy/` 规则覆盖范围内）。
