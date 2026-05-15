"""AnalysisRepository — analysis_results 表 CRUD"""
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from sqlalchemy import text

from .base import AsyncRepository
from ..sql_helpers import ANALYSIS_COLS, build_in_clause, row_to_dict
from ..engine import AsyncSessionLocal
from ..retry import retry_on_lock

logger = logging.getLogger(__name__)


class AnalysisRepository(AsyncRepository):
    table_name = "analysis_results"
    columns = ANALYSIS_COLS

    @retry_on_lock(max_retries=3, delay=0.1)
    async def save(self, analysis_result: dict) -> bool:
        try:
            async with AsyncSessionLocal() as session:
                keywords = analysis_result.get("keywords", [])
                keywords_str = (
                    ",".join(keywords) if isinstance(keywords, list) else (keywords or "")
                )
                params = {
                    "aid": analysis_result["article_id"],
                    "model": analysis_result.get("model_used", ""),
                    "score": analysis_result.get("sentiment_score", 0.0),
                    "label": analysis_result.get("sentiment_label", ""),
                    "keywords": keywords_str,
                    "summary": analysis_result.get("summary", ""),
                    "atype": analysis_result.get("analysis_type", "sentiment"),
                    "result": analysis_result.get("result", ""),
                    "created_at": datetime.now().isoformat(),
                }
                await session.execute(
                    text(
                        "INSERT INTO analysis_results (article_id, model_used, sentiment_score, "
                        "sentiment_label, keywords, summary, analysis_type, result, created_at) "
                        "VALUES (:aid, :model, :score, :label, :keywords, :summary, :atype, :result, :created_at) "
                        "ON CONFLICT (article_id) DO UPDATE SET "
                        "model_used = EXCLUDED.model_used, sentiment_score = EXCLUDED.sentiment_score, "
                        "sentiment_label = EXCLUDED.sentiment_label, keywords = EXCLUDED.keywords, "
                        "summary = EXCLUDED.summary, analysis_type = EXCLUDED.analysis_type, "
                        "result = EXCLUDED.result, created_at = EXCLUDED.created_at"
                    ),
                    params,
                )
                await session.commit()
                logger.debug("保存分析结果: %s", analysis_result["article_id"])
                return True
        except Exception as e:
            logger.error("保存分析结果失败: %s", e)
            return False

    async def get_by_article_id(self, article_id: str) -> Optional[Dict]:
        try:
            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(ANALYSIS_COLS)} FROM analysis_results "
                            "WHERE article_id = :aid LIMIT 1"
                        ),
                        {"aid": article_id},
                    )
                ).fetchone()
                if row is None:
                    return None
                return row_to_dict(row, ANALYSIS_COLS)
        except Exception as e:
            logger.error("获取分析结果失败: %s", e)
            return None

    async def get_by_article_ids(self, article_ids: List[str]) -> Dict[str, Dict]:
        if not article_ids:
            return {}
        try:
            async with AsyncSessionLocal() as session:
                in_sql, in_params = build_in_clause("aids", article_ids)
                rows = (
                    await session.execute(
                        text(
                            f"SELECT {', '.join(ANALYSIS_COLS)} FROM analysis_results "
                            f"WHERE article_id {in_sql}"
                        ),
                        in_params,
                    )
                ).fetchall()
                result = {}
                for row in rows:
                    d = row_to_dict(row, ANALYSIS_COLS)
                    result[d["article_id"]] = d
                return result
        except Exception as e:
            logger.error("批量获取分析结果失败: %s", e)
            return {}

    async def get_sentiment_aggregate(self) -> Tuple[float, int, int, int]:
        try:
            async with AsyncSessionLocal() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT "
                            "AVG(sentiment_score), "
                            "SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN sentiment_label = 'neutral' THEN 1 ELSE 0 END) "
                            "FROM analysis_results"
                        )
                    )
                ).fetchone()
                if row and row[0] is not None:
                    return round(row[0], 4), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
                return 0.0, 0, 0, 0
        except Exception:
            return 0.0, 0, 0, 0
