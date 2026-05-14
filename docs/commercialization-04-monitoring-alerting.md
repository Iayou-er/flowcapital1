# 监控告警体系规范

## 现状问题

- 日志仅写文件（`logs/scheduler.log`），无结构化、无轮转
- `/api/health` 和 `/api/admin/status` 有基础指标但无时序存储
- 爬虫/分析任务失败仅靠日志，无主动告警
- 无 APM（应用性能监控），排查慢请求困难
- 无 SLA 指标追踪（可用性、错误率、P99 延迟）

## 目标

- Prometheus + Grafana 标准监控栈
- 结构化日志（JSON 格式）+ 日志轮转
- 关键指标：QPS、错误率、P95 延迟、爬虫成功率、LLM 调用量
- 多渠道告警（企业微信/钉钉/邮件）
- 不引入过多基础设施，复用已有 ECS

## 实施方案

### 1. 指标定义

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `flowcapital_http_requests_total` | Counter | method, path, status | HTTP 请求总数 |
| `flowcapital_http_request_duration_ms` | Histogram | method, path | 请求耗时分布 |
| `flowcapital_crawl_articles_total` | Counter | source | 爬取文章数 |
| `flowcapital_crawl_errors_total` | Counter | source, error_type | 爬取失败数 |
| `flowcapital_dedup_ratio` | Gauge | — | 去重率 |
| `flowcapital_llm_calls_total` | Counter | operation | LLM 调用次数 |
| `flowcapital_llm_cost_estimate` | Counter | model | LLM 费用估算 |
| `flowcapital_db_connections` | Gauge | state | 数据库连接数 |
| `flowcapital_search_index_lag` | Gauge | — | 搜索索引落后条数 |
| `flowcapital_hotness_articles_total` | Gauge | — | 有热度分的文章数 |

### 2. Prometheus 集成（API 服务）

uvicorn 多 worker 模式（当前 8 worker）下，Prometheus 指标需通过共享内存聚合。

关键点：多进程模式下，Counter / Histogram 等指标**不传 `registry` 参数**（使用默认 registry），`prometheus_client` 库自动将默认 registry 的指标序列化到共享内存。`MultiProcessCollector` 仅用于收集 GC、CPU 等进程级指标。

```python
# backend/monitoring/metrics.py
import os
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry
from prometheus_client import multiprocess, generate_latest

MULTIPROC_DIR = os.getenv("PROMETHEUS_MULTIPROC_DIR")

# 多进程模式：创建 MultiProcessCollector 用于进程级指标聚合
if MULTIPROC_DIR:
    os.makedirs(MULTIPROC_DIR, exist_ok=True)
    mp_registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(mp_registry)
    # 业务指标使用默认 registry（自动序列化到共享内存）
    use_registry = None
else:
    mp_registry = None
    use_registry = CollectorRegistry()

HTTP_REQUESTS = Counter(
    'flowcapital_http_requests_total',
    'Total HTTP requests',
    ['method', 'path', 'status_code'],
    registry=use_registry,
)

HTTP_DURATION = Histogram(
    'flowcapital_http_request_duration_ms',
    'HTTP request duration in ms',
    ['method', 'path'],
    buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000],
    registry=use_registry,
)
```

暴露 `/metrics` 端点：

```python
# backend/api/routes/metrics.py
from fastapi import APIRouter, Response
from backend.monitoring.metrics import MULTIPROC_DIR, use_registry
from prometheus_client import generate_latest

metrics_router = APIRouter(tags=["监控"])

@metrics_router.get("/metrics")
async def metrics():
    if MULTIPROC_DIR:
        data = generate_latest()       # 从共享内存聚合所有 worker
    else:
        data = generate_latest(use_registry)
    return Response(content=data, media_type="text/plain")
```

### 3. Prometheus 集成（Scheduler）

Scheduler 是单进程，**禁止设置 `PROMETHEUS_MULTIPROC_DIR`**，否则指标会序列化到共享内存文件而不会被 Pushgateway 推送。

Scheduler 使用独立的 registry 并通过 Pushgateway 暴露指标：

```python
# scripts/scheduler.py 顶部（确保在导入 metrics 模块前）
import os
# 关键：scheduler 不设 PROMETHEUS_MULTIPROC_DIR，避免指标进入共享内存
assert "PROMETHEUS_MULTIPROC_DIR" not in os.environ, \
    "scheduler 禁止设置 PROMETHEUS_MULTIPROC_DIR，请从 systemd 配置中移除"

from backend.monitoring.metrics import (
    use_registry, CRAWL_ARTICLES, CRAWL_ERRORS, LLM_CALLS,
)
from prometheus_client import Gauge, push_to_gateway

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "localhost:9091")

# 模块级别 Gauge（避免函数内重复创建导致 "Duplicate timeseries" 错误）
dedup_gauge = Gauge(
    'flowcapital_dedup_ratio', 'Deduplication ratio',
    registry=use_registry,
)
lag_gauge = Gauge(
    'flowcapital_search_index_lag', 'Search index lag',
    registry=use_registry,
)
hotness_gauge = Gauge(
    'flowcapital_hotness_articles_total', 'Articles with hotness score',
    registry=use_registry,
)
```

Scheduler 各函数中使用已创建的 Gauge 对象：

```python
# 在 crawl_and_analyze_news() 中
total_new = len(news_articles)
total_raw = len(raw_crawled)
if total_raw > 0:
    dedup_gauge.set(1 - total_new / total_raw)

# 在 check_search_index_consistency() 中
lag_gauge.set(db_count - index_count)

# 在 update_hotness_scores() 中
hotness_gauge.set(count_with_score)

# 每个周期结束时推送到 Pushgateway
def push_scheduler_metrics():
    try:
        push_to_gateway(
            PUSHGATEWAY_URL,
            job="flowcapital-scheduler",
            registry=use_registry,
        )
    except Exception as e:
        logger.warning(f"Pushgateway 推送失败: {e}")
```

### 4. Prometheus 配置

```yaml
# deploy/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  # API 服务（uvicorn workers）
  - job_name: "flowcapital-api"
    static_configs:
      - targets: ["localhost:8001"]
    metrics_path: "/metrics"

  # Scheduler 指标（通过 Pushgateway 中转）
  - job_name: "flowcapital-scheduler"
    honor_labels: true
    static_configs:
      - targets: ["localhost:9091"]
```

### 5. 结构化日志

8 个 uvicorn worker 并行写同一个日志文件会导致轮转冲突。解决方式：**控制台输出由 systemd-journald 统一收集；文件日志通过 `QueueHandler` + 单线程 `QueueListener` 串行写入**。

```python
# backend/logging/setup.py
import atexit
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from queue import Queue

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JSONFormatter())

    log_queue = Queue(-1)
    queue_handler = QueueHandler(log_queue)

    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, 'app.json.log'),
        maxBytes=100 * 1024 * 1024,
        backupCount=30,
        encoding='utf-8',
    )
    file_handler.setFormatter(JSONFormatter())

    # QueueListener 将队列中的日志串行写入文件和控制台
    listener = QueueListener(log_queue, console_handler, file_handler)
    listener.start()
    # 进程退出时停止监听器，刷新队列中剩余日志
    atexit.register(listener.stop)

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL))
    root.addHandler(queue_handler)
```

Scheduler 是单进程，可直接使用 `RotatingFileHandler`，无需 QueueHandler。

### 6. 告警规则

```yaml
# deploy/prometheus/alerts.yml
groups:
  - name: flowcapital
    rules:
      - alert: CrawlFailureRate
        expr: |
          rate(flowcapital_crawl_errors_total[15m]) > 0
          and
          rate(flowcapital_crawl_articles_total[15m]) > 0
          and
          rate(flowcapital_crawl_errors_total[15m]) / rate(flowcapital_crawl_articles_total[15m]) > 0.3
        for: 10m
        labels:
          severity: critical
        annotations:
          summary: "爬取失败率超过 30%"

      - alert: SearchIndexLag
        expr: flowcapital_search_index_lag > 100
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "搜索索引落后 {{ $value }} 条"

      - alert: LLMCostSpike
        expr: rate(flowcapital_llm_calls_total[1h]) > 200
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "LLM 调用量异常飙升"

      - alert: HighErrorRate
        expr: |
          rate(flowcapital_http_requests_total{status_code=~"5.."}[5m]) > 0
          and
          rate(flowcapital_http_requests_total{status_code=~"5.."}[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "5xx 错误率超过 5%"

      - alert: ServiceDown
        expr: up{job="flowcapital-api"} == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "API 服务不可用"
```

### 7. 告警通知渠道

```python
# backend/monitoring/alerting.py
import os
import logging
import requests

class AlertManager:
    def __init__(self):
        self.dingtalk_webhook = os.getenv("DINGTALK_WEBHOOK")
        self.wecom_webhook = os.getenv("WECOM_WEBHOOK")

    def send_dingtalk(self, title: str, content: str):
        if not self.dingtalk_webhook:
            return
        try:
            requests.post(
                self.dingtalk_webhook,
                json={
                    "msgtype": "markdown",
                    "markdown": {"title": title, "text": f"## {title}\n{content}"},
                },
                timeout=5,
            )
        except Exception as e:
            logging.getLogger(__name__).error(f"钉钉告警发送失败: {e}")

    def send_wecom(self, title: str, content: str):
        if not self.wecom_webhook:
            return
        try:
            requests.post(
                self.wecom_webhook,
                json={
                    "msgtype": "text",
                    "text": {"content": f"{title}\n{content}"},
                },
                timeout=5,
            )
        except Exception as e:
            logging.getLogger(__name__).error(f"企业微信告警发送失败: {e}")
```

### 8. Grafana Dashboard

关键面板：

```
┌─────────────────────────────────────────────────┐
│  QPS & P95 Latency (折线图, 5min 粒度)          │
├─────────────────────────────────────────────────┤
│  爬虫成功率 (Gauge, 按数据源)                    │
├──────────────────────┬──────────────────────────┤
│  去重率 (单值)        │  LLM 调用量/费用 (柱状图) │
├──────────────────────┴──────────────────────────┤
│  搜索索引一致率 (折线图)                         │
├─────────────────────────────────────────────────┤
│  5xx 错误率 (折线图, 告警线 5%)                  │
└─────────────────────────────────────────────────┘
```

### 9. 部署

docker-compose 新增服务（含 Pushgateway）：

```yaml
# docker-compose.monitoring.yml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./deploy/prometheus:/etc/prometheus
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'

  pushgateway:
    image: prom/pushgateway:latest
    ports:
      - "9091:9091"

  grafana:
    image: grafana/grafana:latest
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./deploy/grafana/dashboards:/etc/grafana/provisioning/dashboards
    ports:
      - "3000:3000"
```

systemd 环境变量配置：

- **`deploy/backend.service`**（API 服务，需要多进程共享内存）：

```ini
[Service]
Environment=PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc
ExecStartPre=/bin/sh -c 'mkdir -p /tmp/prometheus_multiproc'
ExecStart=/usr/local/bin/uvicorn ...
```

- **`deploy/scheduler.service`**（单进程，禁止设置 `PROMETHEUS_MULTIPROC_DIR`）：

```ini
[Service]
# 不设 PROMETHEUS_MULTIPROC_DIR —— scheduler 通过 Pushgateway 推送指标
Environment=PUSHGATEWAY_URL=localhost:9091
ExecStart=/usr/bin/python3 /opt/flowcapital/scripts/scheduler.py
```

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/monitoring/metrics.py` | **新建**，Prometheus 指标定义 |
| `backend/api/routes/metrics.py` | **新建**，`/metrics` 端点 |
| `backend/monitoring/alerting.py` | **新建**，钉钉/企业微信告警（含超时和异常处理） |
| `backend/logging/setup.py` | **新建**，JSON 结构化日志（QueueHandler + atexit） |
| `backend/api/main.py` | 注册 `/metrics` 端点；添加 HTTP 指标中间件 |
| `scripts/scheduler.py` | 指标埋点（模块级 Gauge）+ Pushgateway 推送；PROMETHEUS_MULTIPROC_DIR 断言 |
| `deploy/prometheus/prometheus.yml` | **新建**，Prometheus 抓取配置 + Pushgateway |
| `deploy/prometheus/alerts.yml` | **新建**，告警规则 |
| `deploy/grafana/dashboards/flowcapital.json` | **新建**，Grafana Dashboard |
| `docker-compose.monitoring.yml` | **新建**，Prometheus + Pushgateway + Grafana |
| `deploy/backend.service` | 新增 `PROMETHEUS_MULTIPROC_DIR` + `ExecStartPre` |
| `deploy/scheduler.service` | 新增 `PUSHGATEWAY_URL`（禁止设 MULTIPROC_DIR） |
| `requirements.txt` | 新增 `prometheus-client`、`requests` |

## 验收标准

- [ ] `/metrics` 端点输出所有自定义指标，8 worker 下数据聚合正确
- [ ] Prometheus 可 scrape `/metrics`，数据点无间断
- [ ] Scheduler 指标通过 Pushgateway 可见
- [ ] 爬虫失败率 > 30% 且持续 10 分钟 → 告警（无爬取活动时不误报）
- [ ] Grafana Dashboard 展示 QPS、P95、爬虫成功率
- [ ] 日志为 JSON 格式，单文件 ≤ 100MB，保留 30 天，多 worker 下无轮转冲突
- [ ] 进程退出时 QueueListener 刷新队列中剩余日志（atexit）
- [ ] 5xx 错误率 > 5% 持续 5 分钟 → 触发告警
- [ ] scheduler 未设置 `PROMETHEUS_MULTIPROC_DIR`（断言保护）
- [ ] 不引入 ELK/Loki 等重型日志系统
