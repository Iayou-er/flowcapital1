"""
Prometheus 监控指标定义
支持多进程模式（uvicorn workers）和单进程模式（scheduler）
"""
import os
from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry
from prometheus_client import multiprocess


MULTIPROC_DIR = os.getenv("PROMETHEUS_MULTIPROC_DIR")

if MULTIPROC_DIR:
    os.makedirs(MULTIPROC_DIR, exist_ok=True)
    mp_registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(mp_registry)
    use_registry = None  # 多进程：指标使用默认 registry，自动序列化到共享内存
else:
    mp_registry = None
    use_registry = CollectorRegistry()  # 单进程：使用自定义 registry

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

CRAWL_ARTICLES = Counter(
    'flowcapital_crawl_articles_total',
    'Total crawled articles',
    ['source'],
    registry=use_registry,
)

CRAWL_ERRORS = Counter(
    'flowcapital_crawl_errors_total',
    'Crawl errors',
    ['source', 'error_type'],
    registry=use_registry,
)

LLM_CALLS = Counter(
    'flowcapital_llm_calls_total',
    'LLM API calls',
    ['operation', 'model'],
    registry=use_registry,
)

DEDUP_RATIO = Gauge(
    'flowcapital_dedup_ratio',
    'Deduplication ratio',
    registry=use_registry,
)

SEARCH_INDEX_LAG = Gauge(
    'flowcapital_search_index_lag',
    'Search index lag behind DB',
    registry=use_registry,
)

HOTNESS_ARTICLES_TOTAL = Gauge(
    'flowcapital_hotness_articles_total',
    'Articles with hotness score > 0',
    registry=use_registry,
)

DB_CONNECTIONS = Gauge(
    'flowcapital_db_connections',
    'Database connection state',
    ['state'],
    registry=use_registry,
)
