"""
双写管理器 — 迁移期间同时写入 SQLite（主）和 PostgreSQL（备）
主库失败 → 中断请求；备库失败 → 仅告警，不影响主流程
"""
import logging

logger = logging.getLogger(__name__)


class DualWriteManager:
    """双写管理器"""

    def __init__(self, primary_session, secondary_session):
        self.primary = primary_session      # SQLite（主）
        self.secondary = secondary_session  # PostgreSQL（备）

    async def save_news_article(self, article_data: dict):
        try:
            await self.primary.save_news_article(article_data)
        except Exception:
            raise  # 主库失败，中断请求

        try:
            await self.secondary.save_news_article(article_data)
        except Exception as e:
            logger.warning(f"PG 双写失败（不影响主流程）: {e}")

    async def save_analysis_result(self, result_data: dict):
        try:
            await self.primary.save_analysis_result(result_data)
        except Exception:
            raise

        try:
            await self.secondary.save_analysis_result(result_data)
        except Exception as e:
            logger.warning(f"PG 双写失败（不影响主流程）: {e}")

    async def save_guest_message(self, anonymous_id: str, content: str) -> bool:
        try:
            result = await self.primary.save_guest_message(anonymous_id, content)
            if not result:
                return False
        except Exception:
            raise

        try:
            await self.secondary.save_guest_message(anonymous_id, content)
        except Exception as e:
            logger.warning(f"PG 双写失败（不影响主流程）: {e}")
        return True
