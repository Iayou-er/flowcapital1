# NewsCrawler 拆分规范

## 现状

`backend/crawler/news_crawler.py` — 1530 行，1 个类 `NewsCrawler`（1074 行），包含：
- 25 个数据源抓取方法（`_fetch_jinshi`, `_fetch_wallstreetcn`, `_fetch_wechat_sogou` ...）
- HTTP 适配器、重试、熔断
- Playwright 共享浏览器管理
- 正文提取管线（4 策略）
- WAF 绕过、RSS 解析、分类匹配
- 全局超时控制

**隐患**：加一个源就要改 1500 行文件；修改正文提取逻辑可能影响所有源；无法单独测试某个源。

---

## 目标架构

```
backend/crawler/
├── __init__.py
├── base.py                  # BaseFetcher 抽象基类 + 通用工具函数
├── utils.py                 # 共享工具：_normalize_pubdate, _walk, 正则常量等
├── fetchers/
│   ├── __init__.py
│   ├── rss.py               # RSS 类源（新华网/财新/经济日报）
│   ├── api.py               # API 类源（同花顺/财联社/百度/金十）
│   ├── web.py               # Playwright 网页源（东方财富/新浪/雪球/微信搜狗）
│   └── registry.py          # 数据源注册表（名称 → Fetcher 类映射）
├── pipeline.py              # 正文提取管线（4 策略）
├── extractors/
│   ├── __init__.py
│   ├── selector.py          # 选择器提取
│   ├── density.py           # 文本密度提取
│   ├── readability.py       # Readability 提取
│   └── fallback.py          # 回退提取
├── circuit.py               # 熔断器
├── browser.py               # Playwright 共享浏览器管理（sync API）
├── orchestrator.py          # NewsCrawler → 编排器（调度并行抓取、汇总、去重）
└── config.py                # 超时、并发数、分类映射等常量
```

> **注意**：SimHash 去重保留在 `backend/analyzer/dedup.py`，不移动。编排器和分析管线都需要引用它。

---

## 拆分步骤

### 第 1 步：提取熔断器 `circuit.py`

从 `news_crawler.py` 提取 `CircuitBreaker` 类：

```python
class CircuitBreaker:
    """熔断器：连续失败 N 次后暂停 M 秒"""
    def __init__(self, name: str, failure_threshold: int = 3, cooldown_seconds: int = 300):
        ...
    def record_success(self): ...
    def record_failure(self): ...
    def is_open(self) -> bool: ...
```

### 第 2 步：提取浏览器管理 `browser.py`

从 `NewsCrawler.__init__` 和相关方法提取 Playwright 管理。

**使用同步 Playwright API**（`sync_playwright()`），与现有代码一致：

```python
class PlaywrightBrowserManager:
    """单例，管理共享 Playwright 浏览器实例（同步 API）"""
    def get_page(self): ...
    def reset(self): ...
    def close(self): ...
```

> 切换 `async_playwright()` 是未来优化项，不在本次拆分范围。

### 第 3 步：定义抽象基类 `base.py`

```python
from abc import ABC, abstractmethod

class BaseFetcher(ABC):
    """数据源抓取基类（同步方法，由编排器通过 ThreadPoolExecutor 调度）"""
    name: str                     # 数据源名称
    category: str                 # 分类（fast/slow）
    timeout: int = 30

    def __init__(self, browser: PlaywrightBrowserManager = None,
                 session: requests.Session = None,
                 circuit_breaker: CircuitBreaker = None):
        """依赖注入：编排器负责传入共享资源"""
        self.browser = browser
        self.session = session or requests.Session()
        self.circuit = circuit_breaker or CircuitBreaker(name=self.name)

    @abstractmethod
    def fetch(self, **kwargs) -> List[Dict]:
        """抓取文章列表（同步），返回 [{article_id, title, url, ...}]"""
        ...

    def fetch_article(self, url: str) -> Optional[Dict]:
        """抓取单篇文章正文（可选覆盖，同步）"""
        ...
```

**关键设计决策**：
- 所有方法保持**同步**（与当前代码兼容），由 `orchestrator.py` 通过 `ThreadPoolExecutor` 调度并行执行
- 共享依赖（浏览器、HTTP session、熔断器）通过构造函数注入
- `base.py` 同时容纳共享工具函数：`_normalize_pubdate`、`_walk`、`SKIP_PATTERN`、`ARTICLE_CLASS_PATTERN` 等

### 第 4 步：迁移数据源到 `fetchers/`

每个 `_fetch_xxx` 方法 → 独立的 `XxxFetcher(BaseFetcher)` 类。

| 原方法 | 新类 | 文件 |
|--------|------|------|
| `_fetch_jinshi` | `JinshiFetcher` | `fetchers/api.py` |
| `_fetch_wallstreetcn` | `WallStreetCNFetcher` | `fetchers/api.py` |
| `_fetch_xueqiu` | `XueqiuFetcher` | `fetchers/web.py` |
| `_fetch_wechat_sogou` | `WeChatSogouFetcher` | `fetchers/web.py` |
| `_fetch_rss_sources` | `RssFetcher` | `fetchers/rss.py` |
| ... | ... | ... |

注册表 `registry.py`：

```python
FETCHER_REGISTRY: Dict[str, Type[BaseFetcher]] = {
    "jinshi": JinshiFetcher,
    "wallstreetcn": WallStreetCNFetcher,
    "xueqiu": XueqiuFetcher,
    ...
}

FAST_SOURCES = ["jinshi", "wallstreetcn", ...]  # API/RSS 源
SLOW_SOURCES = ["xueqiu", "wechat_sogou", ...]  # Playwright 源
```

### 第 5 步：提取正文提取管线 `pipeline.py`

```python
class ContentExtractionPipeline:
    """4 策略正文提取管线：选择器 → 文本密度 → Readability → 回退"""
    def __init__(self, cache_size: int = 512): ...
    def extract(self, html: str, url: str, source: str) -> Tuple[str, str]:
        """返回 (content, summary)"""
```

### 第 6 步：重构编排器 `orchestrator.py`

```python
class CrawlOrchestrator:
    """编排并行抓取：快源线程池 + 慢源 Playwright

    依赖关系：本模块依赖 refactor-01 的 Fetcher 类。
    如果 refactor-03（scheduler 扁平化）先执行，需保留旧的 NewsCrawler 导入路径。
    """
    def __init__(self, sources: List[str] = None):
        shared_browser = PlaywrightBrowserManager()
        shared_session = requests.Session()
        self.fetchers = []
        for name in (sources or ALL_SOURCES):
            cls = FETCHER_REGISTRY[name]
            cb = CircuitBreaker(name=name)
            self.fetchers.append(cls(
                browser=shared_browser if name in SLOW_SOURCES else None,
                session=shared_session if name in FAST_SOURCES else None,
                circuit_breaker=cb,
            ))

    def run(self) -> List[Dict]:
        """并行抓取所有源，汇总、去重、质量排序，180s 全局超时"""
        with ThreadPoolExecutor(max_workers=len(FAST_SOURCES)) as pool:
            fast_futures = {pool.submit(f.fetch): f for f in self.fast_fetchers}
            # 慢源按顺序在单独的 Playwright 线程中执行
            slow_futures = {pool.submit(f.fetch): f for f in self.slow_fetchers}
            # ...
        articles = self._dedup(all_results)
        return self._sort_by_quality(articles)
```

### 第 7 步：清理旧代码

- 移除 `NewsCrawler` 类
- 更新 `scripts/scheduler.py` 中的导入
- 更新 `backend/crawler/__init__.py` 的导出

---

## 测试策略

| 层级 | 内容 | 工具 |
|------|------|------|
| 单元 | 每个 Fetcher 的 URL 构建和响应解析 | pytest + mock |
| 单元 | 正文提取管线 4 策略各自 | pytest + fixture HTML |
| 集成 | 编排器完整流程（mock HTTP 响应） | pytest + responses |
| 端到端 | 实际抓取 1-2 个稳定源 | 手动 / CI 定时 |

---

## 验收标准

- [ ] 所有 25 个数据源独立为 Fetcher 类
- [ ] 添加新数据源只需新建一个文件 + 注册一行
- [ ] 正文提取管线可单独测试
- [ ] 编排器 180s 全局超时行为不变
- [ ] 现有调度器无修改即可使用（仅改 import）
- [ ] 每个 Fetcher 有至少 1 个单元测试
