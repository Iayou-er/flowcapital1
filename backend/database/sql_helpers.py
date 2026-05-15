"""SQL 兼容工具：IN 子句构建、行转字典、列名常量"""
from typing import List, Dict, Tuple, Any

from .engine import DATABASE_URL

_IS_PG = DATABASE_URL.startswith("postgresql")

NEWS_LIGHT_COLS = [
    "id", "article_id", "title", "summary", "url", "source", "category",
    "published_at", "author", "read_count", "comment_count", "tags", "created_at", "updated_at",
]
NEWS_ALL_COLS = [
    "id", "article_id", "title", "content", "summary", "url", "source",
    "category", "published_at", "author", "read_count", "comment_count",
    "tags", "hotness_score", "created_at", "updated_at",
]
ANALYSIS_COLS = [
    "id", "article_id", "model_used", "sentiment_score",
    "sentiment_label", "keywords", "summary", "analysis_type", "result", "created_at",
]
EVENT_COLS = ["id", "event_type", "article_id", "client_id", "payload", "created_at"]
GUEST_COLS = ["id", "anonymous_id", "content", "created_at"]


def build_in_clause(param_name: str, values: List[Any], negate: bool = False) -> Tuple[str, Dict[str, Any]]:
    """IN 子句兼容：PG 用 = ANY(:p)，SQLite 用 IN (:p_0, :p_1, ...)"""
    if not values:
        return ("FALSE", {})
    if _IS_PG:
        op = "!= ALL" if negate else "= ANY"
        return (f"{op}(:{param_name})", {param_name: values})
    params = {f"{param_name}_{i}": v for i, v in enumerate(values)}
    placeholders = ", ".join(f":{k}" for k in params)
    prefix = "NOT " if negate else ""
    return (f"{prefix}IN ({placeholders})", params)


def row_to_dict(row, cols: List[str]) -> dict:
    d = dict(zip(cols, row))
    if d.get("tags") and isinstance(d["tags"], str):
        d["tags"] = d["tags"].split(",")
    if d.get("keywords") and isinstance(d["keywords"], str):
        d["keywords"] = d["keywords"].split(",")
    return d
