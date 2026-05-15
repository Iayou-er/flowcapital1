"""数据源抓取基类（同步方法，由编排器通过 ThreadPoolExecutor 调度）"""
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class BaseFetcher(ABC):
    name: str = ""
    category: str = "fast"
    timeout: int = 30

    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()

    @abstractmethod
    def fetch(self, limit: int = 15) -> List[Dict]:
        """抓取文章列表，返回 [{article_id, title, url, ...}]"""
        ...

    def fetch_article(self, url: str) -> Optional[Dict]:
        """抓取单篇文章正文（可选覆盖）"""
        return None
