"""
SQLite → PostgreSQL 数据迁移脚本
将全部业务表数据从 SQLite 迁移到 PostgreSQL
"""
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def migrate_all(sqlite_session, pg_session, batch_size: int = 500):
    """
    全量迁移 4 张业务表
    :param sqlite_session: SQLAlchemy async session（SQLite）
    :param pg_session: SQLAlchemy async session（PostgreSQL）
    :param batch_size: 每批迁移行数
    """
    tables = [
        ("news_articles", "published_at"),
        ("analysis_results", "created_at"),
        ("guest_book", "created_at"),
        ("event_log", "created_at"),
    ]

    from sqlalchemy import text

    total_migrated = 0
    for table, order_col in tables:
        count_sql = text(f"SELECT COUNT(*) FROM {table}")
        result = await sqlite_session.execute(count_sql)
        total = result.scalar()
        logger.info(f"开始迁移 {table}: {total} 行")

        offset = 0
        migrated = 0
        while offset < total:
            select_sql = text(
                f"SELECT * FROM {table} ORDER BY {order_col} "
                f"LIMIT :limit OFFSET :offset"
            )
            result = await sqlite_session.execute(
                select_sql, {"limit": batch_size, "offset": offset}
            )
            rows = result.fetchall()

            if not rows:
                break

            columns = result.keys()
            for row in rows:
                data = dict(zip(columns, row))
                columns_list = ", ".join(data.keys())
                placeholders = ", ".join(f":{k}" for k in data.keys())
                insert_sql = text(
                    f"INSERT INTO {table} ({columns_list}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT DO NOTHING"
                )
                try:
                    await pg_session.execute(insert_sql, data)
                except Exception as e:
                    logger.warning(f"插入 {table} 行失败: {e}")

            await pg_session.commit()
            migrated += len(rows)
            offset += batch_size
            logger.info(f"  {table}: {migrated}/{total}")

        total_migrated += migrated

    logger.info(f"迁移完成: {total_migrated} 行")
    return total_migrated


async def verify_migration(sqlite_session, pg_session):
    """校验两库行数一致"""
    from sqlalchemy import text

    tables = ["news_articles", "analysis_results", "guest_book", "event_log"]
    ok = True
    for table in tables:
        sqlite_count = (await sqlite_session.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        )).scalar()
        pg_count = (await pg_session.execute(
            text(f"SELECT COUNT(*) FROM {table}")
        )).scalar()
        if sqlite_count != pg_count:
            logger.error(f"  {table}: SQLite={sqlite_count} PG={pg_count} 不一致!")
            ok = False
        else:
            logger.info(f"  {table}: {sqlite_count} 一致")
    return ok
