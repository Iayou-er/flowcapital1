"""
数据库管理器
用于管理新闻数据的存储和检索
采用 SQLite 单连接长连接模式，避免频繁创建/关闭连接
"""

import os
import sqlite3
import logging
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from .models import NewsArticle, AnalysisResult

logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'logs')
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(logs_dir, 'database.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 列名常量
NEWS_COLUMNS = ['id', 'article_id', 'title', 'content', 'summary', 'url', 'source',
                'category', 'published_at', 'author', 'read_count', 'comment_count',
                'tags', 'created_at', 'updated_at']

# 列表接口用轻量列（不含 content，减少 90%+ 数据传输）
NEWS_COLUMNS_LIGHT = 'id, article_id, title, summary, url, source, category, ' \
                     'published_at, author, read_count, comment_count, tags, created_at, updated_at'


def _row_to_dict(row: tuple) -> dict:
    """将数据库行转换为字典"""
    return {
        col: (row[i].split(',') if col == 'tags' and row[i] else row[i])
        for i, col in enumerate(NEWS_COLUMNS)
    }


class DatabaseManager:
    """
    数据库管理器 - 单例模式 + 长连接
    使用 SQLite 单连接模式，避免每次查询都创建/关闭连接
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, db_path: str = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, db_path: str = None):
        # 仅首次初始化时执行
        if hasattr(self, '_initialized'):
            return
        self._initialized = True

        # 确保数据库路径相对于项目根目录（backend 的父目录）
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if db_path is None:
            db_path = os.path.join(project_root, 'data', 'sqlite.db')
        elif not os.path.isabs(db_path):
            db_path = os.path.join(project_root, db_path)

        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        # 读写分离：WAL 模式下读写不互斥
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._ro_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._ro_conn.row_factory = sqlite3.Row
        self._wr_lock = threading.Lock()
        self._ro_lock = threading.Lock()

        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.execute('PRAGMA cache_size=-8000')
        self._conn.execute('PRAGMA mmap_size=268435456')
        self._conn.execute('PRAGMA foreign_keys=ON')

        self.init_db()
        logger.info("数据库读写连接已建立（WAL 模式，读写分离）")

    def _reconnect_conn(self, conn):
        """重连单个连接"""
        conn.close()
        new_conn = sqlite3.connect(self.db_path, check_same_thread=False)
        new_conn.row_factory = sqlite3.Row
        new_conn.execute('PRAGMA journal_mode=WAL')
        new_conn.execute('PRAGMA synchronous=NORMAL')
        new_conn.execute('PRAGMA cache_size=-8000')
        new_conn.execute('PRAGMA mmap_size=268435456')
        new_conn.execute('PRAGMA foreign_keys=ON')
        return new_conn

    def _reconnect(self):
        """断线重连（兼容旧调用）"""
        try:
            self._conn = self._reconnect_conn(self._conn)
            self._ro_conn = self._reconnect_conn(self._ro_conn)
            logger.info("SQLite 连接已重连")
        except Exception as e:
            logger.error(f"重连失败: {e}")
            raise

    def _execute_write(self, sql: str, params: tuple = None, commit: bool = False) -> Optional[sqlite3.Cursor]:
        """执行写 SQL（INSERT/UPDATE/DELETE/DDL）"""
        with self._wr_lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute(sql, params or ())
                if commit:
                    self._conn.commit()
                return cursor
            except sqlite3.DatabaseError as e:
                err_msg = str(e).lower()
                if 'no such table' not in err_msg and any(
                    kw in err_msg for kw in ['database is locked', 'unable to open database',
                                              'disk i/o error', 'connection is closed']
                ):
                    logger.warning(f"写连接异常，尝试重连: {e}")
                    try:
                        self._conn = self._reconnect_conn(self._conn)
                        cursor = self._conn.cursor()
                        cursor.execute(sql, params or ())
                        if commit:
                            self._conn.commit()
                        return cursor
                    except Exception as reconnect_err:
                        logger.warning(f"写连接重连失败: {reconnect_err}")
                self._conn.rollback()
                logger.error(f"写 SQL 失败: {e}\n  SQL: {sql}")
                raise

    def _execute_read(self, sql: str, params: tuple = None) -> Optional[sqlite3.Cursor]:
        """执行读 SQL（SELECT），不阻塞写入"""
        with self._ro_lock:
            try:
                cursor = self._ro_conn.cursor()
                cursor.execute(sql, params or ())
                return cursor
            except sqlite3.DatabaseError as e:
                err_msg = str(e).lower()
                if any(kw in err_msg for kw in ['connection is closed', 'unable to open database']):
                    logger.warning(f"读连接异常，尝试重连: {e}")
                    try:
                        self._ro_conn = self._reconnect_conn(self._ro_conn)
                        cursor = self._ro_conn.cursor()
                        cursor.execute(sql, params or ())
                        return cursor
                    except Exception as reconnect_err:
                        logger.warning(f"读连接重连失败: {reconnect_err}")
                raise

    def _execute(self, sql: str, params: tuple = None, commit: bool = False) -> Optional[sqlite3.Cursor]:
        """兼容旧接口"""
        return self._execute_write(sql, params, commit)

    def _query(self, sql: str, params: tuple = None) -> List[Dict]:
        """查询并返回字典列表（读连接，不阻塞写入）"""
        cursor = self._execute_read(sql, params)
        if cursor is None:
            return []
        results = []
        for row in cursor.fetchall():
            d = dict(row)
            for key in d:
                val = row[key]
                d[key] = val.split(',') if key == 'tags' and val else val
            results.append(d)
        return results

    def _query_one(self, sql: str, params: tuple = None) -> Optional[tuple]:
        """查询单行（读连接）"""
        cursor = self._execute_read(sql, params)
        if cursor is None:
            return None
        return cursor.fetchone()

    def init_db(self):
        """初始化数据库表结构"""
        self._execute('''
            CREATE TABLE IF NOT EXISTS news_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT UNIQUE,
                title TEXT NOT NULL,
                content TEXT,
                summary TEXT,
                url TEXT,
                source TEXT,
                category TEXT,
                published_at TEXT,
                author TEXT,
                read_count INTEGER DEFAULT 0,
                comment_count INTEGER DEFAULT 0,
                tags TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        ''', commit=True)

        self._execute('''
            CREATE TABLE IF NOT EXISTS analysis_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT,
                model_used TEXT,
                sentiment_score REAL DEFAULT 0.0,
                sentiment_label TEXT,
                keywords TEXT,
                summary TEXT,
                analysis_type TEXT,
                result TEXT,
                created_at TEXT,
                FOREIGN KEY (article_id) REFERENCES news_articles (article_id)
            )
        ''', commit=True)

        # 迁移：为已有数据库添加 summary 列
        cols = [row[1] for row in self._execute("PRAGMA table_info(analysis_results)").fetchall()]
        if 'summary' not in cols:
            self._execute('ALTER TABLE analysis_results ADD COLUMN summary TEXT', commit=True)

        # URL 唯一索引（跨批次去重主防线，仅非空值）
        self._execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_news_url ON news_articles(url) WHERE url IS NOT NULL AND url != ''", commit=True)

        # 单列索引
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_article_id ON news_articles(article_id)', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_source ON news_articles(source)', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_category ON news_articles(category)', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_title ON news_articles(title)', commit=True)

        # 复合索引
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_category_time ON news_articles(category, published_at DESC)', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_published_at ON news_articles(published_at DESC)', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_search ON news_articles(title, summary)', commit=True)
        # 覆盖索引加速列表查询（不含 content，匹配 NEWS_COLUMNS_LIGHT）
        self._execute('CREATE INDEX IF NOT EXISTS idx_news_list ON news_articles(published_at DESC, source, category, title, summary)', commit=True)

        # 分析结果索引
        self._execute('CREATE INDEX IF NOT EXISTS idx_analysis_article_id ON analysis_results(article_id)', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_analysis_created_at ON analysis_results(created_at DESC)', commit=True)

        # 留言板
        self._execute('''
            CREATE TABLE IF NOT EXISTS guest_book (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                anonymous_id TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        ''', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_guestbook_time ON guest_book(created_at DESC)', commit=True)

        # 用户行为埋点事件
        self._execute('''
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                article_id TEXT,
                client_id TEXT,
                payload TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_event_type_time ON event_log(event_type, created_at)', commit=True)
        self._execute('CREATE INDEX IF NOT EXISTS idx_event_article ON event_log(article_id)', commit=True)

        # 迁移：为已有数据库添加 hotness_score 列
        news_cols = [row[1] for row in self._execute("PRAGMA table_info(news_articles)").fetchall()]
        if 'hotness_score' not in news_cols:
            self._execute('ALTER TABLE news_articles ADD COLUMN hotness_score REAL DEFAULT 0.0', commit=True)
            self._execute('CREATE INDEX IF NOT EXISTS idx_news_hotness ON news_articles(hotness_score DESC)', commit=True)

        logger.info("数据库表结构及索引初始化完成")

    def _invalidate_news_cache(self):
        """清除新闻列表相关 Redis 缓存"""
        try:
            # 懒加载，避免循环导入
            from .redis_client import redis_client
            from .cache import CacheManager
            CacheManager.invalidate_news_list_sync()
        except Exception as e:
            logger.warning(f"清除新闻缓存失败: {e}")

    def save_news_article(self, article: NewsArticle) -> bool:
        """保存单条新闻"""
        try:
            # 检查是否存在
            existing = self._query_one(
                'SELECT content, title, summary FROM news_articles WHERE article_id = ?',
                (article.article_id,)
            )
            if existing and existing[0] == article.content and existing[1] == article.title and existing[2] == article.summary:
                logger.info(f"新闻已存在且内容一致，跳过: {article.title}")
                return True

            self._execute('''
                INSERT OR REPLACE INTO news_articles
                (article_id, title, content, summary, url, source, category,
                 published_at, author, read_count, comment_count, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                article.article_id, article.title, article.content, article.summary,
                article.url, article.source, article.category, article.published_at,
                article.author, article.read_count, article.comment_count,
                ','.join(article.tags) if article.tags else '',
                article.created_at, datetime.now().isoformat()
            ), commit=True)

            logger.info(f"{'更新' if existing else '保存'}新闻: {article.title}")
            self._invalidate_news_cache()
            return True
        except Exception as e:
            logger.error(f"保存新闻失败: {e}")
            return False

    def save_news_articles(self, articles: List[NewsArticle]) -> int:
        """批量保存新闻"""
        if not articles:
            return 0
        try:
            # 单次 IN 查询查出所有已存在的记录，避免 N+1
            article_ids = [a.article_id for a in articles]
            placeholders = ','.join(['?'] * len(article_ids))
            existing_rows = self._query(
                f'SELECT article_id, content, title, summary FROM news_articles WHERE article_id IN ({placeholders})',
                tuple(article_ids)
            )
            existing_map = {r['article_id']: r for r in existing_rows}

            batch_insert = []
            success_count = 0

            for article in articles:
                existing = existing_map.get(article.article_id)
                if existing and existing['content'] == article.content and existing['title'] == article.title and existing['summary'] == article.summary:
                    success_count += 1
                    continue
                batch_insert.append((
                    article.article_id, article.title, article.content, article.summary,
                    article.url, article.source, article.category, article.published_at,
                    article.author, article.read_count, article.comment_count,
                    ','.join(article.tags) if article.tags else '',
                    article.created_at, datetime.now().isoformat()
                ))
                success_count += 1

            if batch_insert:
                with self._wr_lock:
                    self._conn.executemany('''
                        INSERT OR REPLACE INTO news_articles
                        (article_id, title, content, summary, url, source, category,
                         published_at, author, read_count, comment_count, tags, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', batch_insert)
                    self._conn.commit()

            logger.info(f"批量保存完成，成功: {success_count}/{len(articles)}，实际写入: {len(batch_insert)}")
            if batch_insert:
                self._invalidate_news_cache()
            return success_count
        except Exception as e:
            logger.error(f"批量保存失败: {e}")
            return 0

    def get_latest_news(self, limit: int = 100, offset: int = 0,
                        exclude_sources: List[str] = None) -> Tuple[List[Dict], int]:
        """获取最新新闻（列表用轻量列，不含 content），热度+新鲜度混合排序"""
        now = datetime.now()
        day_ago = (now - timedelta(days=1)).isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()
        order_sql = '''
            ORDER BY
                CASE
                    WHEN published_at >= ? THEN 1.0
                    WHEN published_at >= ? THEN 0.5
                    ELSE 0.1
                END * 0.5
                + COALESCE(hotness_score, 0) * 0.3
                + MIN(COALESCE(LENGTH(tags) - LENGTH(REPLACE(tags, ',', '')) + CASE WHEN tags != '' THEN 1 ELSE 0 END, 0), 10) * 0.02
                DESC
        '''
        try:
            if exclude_sources:
                placeholders = ','.join(['?'] * len(exclude_sources))
                total_row = self._query_one(
                    f'SELECT COUNT(*) FROM news_articles WHERE source NOT IN ({placeholders})',
                    tuple(exclude_sources)
                )
                total = total_row[0] if total_row else 0
                news = self._query(
                    f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles WHERE source NOT IN ({placeholders}) '
                    f'{order_sql} LIMIT ? OFFSET ?',
                    (day_ago, week_ago) + tuple(exclude_sources) + (limit, offset)
                )
            else:
                total_row = self._query_one("SELECT COUNT(*) FROM news_articles")
                total = total_row[0] if total_row else 0
                news = self._query(
                    f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles '
                    f'{order_sql} LIMIT ? OFFSET ?',
                    (day_ago, week_ago, limit, offset)
                )
            return news, total
        except Exception as e:
            logger.error(f"获取最新新闻失败: {e}")
            return [], 0

    def get_news_by_sources(self, sources: List[str], limit: int = 100, offset: int = 0) -> Tuple[List[Dict], int]:
        """按来源列表获取新闻（用于自媒体板块），返回 (新闻列表, 总数)"""
        if not sources:
            return [], 0
        try:
            placeholders = ','.join(['?'] * len(sources))
            total_row = self._query_one(
                f'SELECT COUNT(*) FROM news_articles WHERE source IN ({placeholders})',
                tuple(sources)
            )
            total = total_row[0] if total_row else 0

            news = self._query(
                f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles WHERE source IN ({placeholders}) '
                f'ORDER BY published_at DESC LIMIT ? OFFSET ?',
                tuple(sources) + (limit, offset)
            )
            return news, total
        except Exception as e:
            logger.error(f"获取自媒体新闻失败: {e}")
            return [], 0

    def search_media(self, sources: List[str], keyword: str,
                      limit: int = 20, offset: int = 0) -> Tuple[List[Dict], int]:
        """搜索自媒体内容（仅限指定 source 列表 + SQL LIKE）"""
        if not sources or not keyword:
            return [], 0
        escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f'%{escaped}%'
        placeholders = ','.join(['?'] * len(sources))
        try:
            total_row = self._query_one(
                f'SELECT COUNT(*) FROM news_articles '
                f'WHERE source IN ({placeholders}) '
                f"AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')",
                tuple(sources) + (pattern, pattern, pattern)
            )
            total = total_row[0] if total_row else 0
            results = self._query(
                f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles '
                f'WHERE source IN ({placeholders}) '
                f"AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\') "
                f'ORDER BY published_at DESC LIMIT ? OFFSET ?',
                tuple(sources) + (pattern, pattern, pattern, limit, offset)
            )
            return results, total
        except Exception as e:
            logger.error(f"搜索自媒体失败: {e}")
            return [], 0

    def get_news_by_category(self, category: str, limit: int = 100, offset: int = 0,
                              exclude_sources: List[str] = None) -> Tuple[List[Dict], int]:
        """按分类获取新闻，返回 (新闻列表, 总数)"""
        try:
            if exclude_sources:
                placeholders = ','.join(['?'] * len(exclude_sources))
                total_row = self._query_one(
                    f'SELECT COUNT(*) FROM news_articles WHERE category = ? AND source NOT IN ({placeholders})',
                    (category,) + tuple(exclude_sources)
                )
                total = total_row[0] if total_row else 0
                news = self._query(
                    f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles WHERE category = ? AND source NOT IN ({placeholders}) '
                    f'ORDER BY published_at DESC LIMIT ? OFFSET ?',
                    (category,) + tuple(exclude_sources) + (limit, offset)
                )
            else:
                total_row = self._query_one("SELECT COUNT(*) FROM news_articles WHERE category = ?", (category,))
                total = total_row[0] if total_row else 0
                news = self._query(
                    f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles WHERE category = ? '
                    f'ORDER BY published_at DESC LIMIT ? OFFSET ?',
                    (category, limit, offset)
                )
            return news, total
        except Exception as e:
            logger.error(f"获取分类新闻失败: {e}")
            return [], 0

    def get_news_by_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """按日期范围获取新闻"""
        return self._query(
            f'SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles WHERE published_at BETWEEN ? AND ? '
            f'ORDER BY published_at DESC',
            (start_date, end_date)
        )

    def search_news(self, keyword: str, limit: int = 20, offset: int = 0) -> Tuple[List[Dict], int]:
        """按关键词搜索新闻，返回 (结果列表, 总数)"""
        # 转义 LIKE 通配符，防止用户输入 % _ 被当作通配符利用
        escaped = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f"%{escaped}%"
        try:
            total_row = self._query_one("""
                SELECT COUNT(*) FROM news_articles
                WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\'
            """, (pattern, pattern, pattern))
            total = total_row[0] if total_row else 0

            results = self._query(f'''
                SELECT {NEWS_COLUMNS_LIGHT} FROM news_articles
                WHERE title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\'
                ORDER BY published_at DESC LIMIT ? OFFSET ?
            ''', (pattern, pattern, pattern, limit, offset))
            return results, total
        except Exception as e:
            logger.error(f"搜索新闻失败: {e}")
            return [], 0

    def get_news_by_article_id(self, article_id: str) -> Optional[Dict]:
        """根据 article_id 获取单条新闻"""
        results = self._query('SELECT * FROM news_articles WHERE article_id = ?', (article_id,))
        return results[0] if results else None

    def get_news_by_article_ids(self, article_ids: List[str]) -> List[Dict]:
        """批量根据 article_id 获取新闻，保持传入顺序"""
        if not article_ids:
            return []
        placeholders = ','.join(['?'] * len(article_ids))
        results = self._query(
            f'SELECT * FROM news_articles WHERE article_id IN ({placeholders})',
            tuple(article_ids)
        )
        news_map = {r['article_id']: r for r in results}
        return [news_map[aid] for aid in article_ids if aid in news_map]

    def get_recent_article_ids(self, limit: int = 500) -> List[str]:
        """获取最近入库的 article_id 列表（用于去重比对，不含 content）"""
        rows = self._query(
            'SELECT article_id FROM news_articles ORDER BY id DESC LIMIT ?',
            (limit,)
        )
        return [r['article_id'] for r in rows]

    def get_news_count(self) -> int:
        """获取新闻总数"""
        try:
            row = self._query_one('SELECT COUNT(*) FROM news_articles')
            return row[0] if row else 0
        except Exception as e:
            logger.error(f"获取新闻总数失败: {e}")
            return 0

    def get_all_categories(self) -> List[Dict]:
        """获取所有分类及其新闻数量（读锁，不阻塞写入）"""
        try:
            return self._query('''
                SELECT category, COUNT(*) as count FROM news_articles
                WHERE category IS NOT NULL AND category != ''
                GROUP BY category ORDER BY count DESC
            ''')
        except Exception as e:
            logger.error(f"获取分类列表失败: {e}")
            return []

    def save_analysis_result(self, analysis_result: AnalysisResult) -> bool:
        """保存分析结果"""
        try:
            self._execute('''
                INSERT OR REPLACE INTO analysis_results
                (article_id, model_used, sentiment_score, sentiment_label, keywords,
                 summary, analysis_type, result, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                analysis_result.article_id, analysis_result.model_used,
                analysis_result.sentiment_score, analysis_result.sentiment_label,
                ','.join(analysis_result.keywords) if analysis_result.keywords else '',
                analysis_result.summary or '',
                analysis_result.analysis_type, analysis_result.result,
                datetime.now().isoformat()
            ), commit=True)
            logger.info(f"保存分析结果: {analysis_result.article_id}")
            return True
        except Exception as e:
            logger.error(f"保存分析结果失败: {e}")
            return False

    def get_analysis_by_article_id(self, article_id: str) -> Optional[Dict]:
        """根据 article_id 查询是否已有分析结果"""
        results = self._query(
            'SELECT * FROM analysis_results WHERE article_id = ? LIMIT 1',
            (article_id,)
        )
        return results[0] if results else None

    # ────────────────── 留言板 ──────────────────────

    def save_guest_message(self, anonymous_id: str, content: str) -> bool:
        """保存留言"""
        try:
            self._execute('''
                INSERT INTO guest_book (anonymous_id, content, created_at)
                VALUES (?, ?, ?)
            ''', (anonymous_id, content, datetime.now().isoformat()), commit=True)
            return True
        except Exception as e:
            logger.error(f"保存留言失败: {e}")
            return False

    def get_guest_messages(self, page: int = 1, limit: int = 20) -> Tuple[List[Dict], int]:
        """分页获取留言"""
        try:
            total_row = self._query_one("SELECT COUNT(*) FROM guest_book")
            total = total_row[0] if total_row else 0
            offset = (page - 1) * limit
            messages = self._query('''
                SELECT * FROM guest_book ORDER BY created_at DESC LIMIT ? OFFSET ?
            ''', (limit, offset))
            return messages, total
        except Exception as e:
            logger.error(f"获取留言失败: {e}")
            return [], 0

    def get_anonymous_id_count(self) -> int:
        """获取当前匿名ID的最大序号"""
        try:
            row = self._query_one("SELECT COUNT(*) FROM guest_book")
            return row[0] if row else 0
        except Exception:
            return 0

    def get_sentiment_aggregate(self) -> Tuple[float, int, int, int]:
        """从已有分析结果聚合情感数据 — 不做 SnowNLP 计算"""
        try:
            row = self._query_one("""
                SELECT
                    AVG(sentiment_score),
                    SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN sentiment_label = 'neutral' THEN 1 ELSE 0 END)
                FROM analysis_results
            """)
            if row and row[0] is not None:
                return round(row[0], 4), int(row[1] or 0), int(row[2] or 0), int(row[3] or 0)
            # 无分析结果时返回中性默认值
            return 0.0, 0, 0, 0
        except Exception:
            return 0.0, 0, 0, 0

    # ── 用户行为埋点 ──

    def insert_event(self, event_type: str, article_id: str = None,
                     payload: str = None, client_id: str = None) -> bool:
        """插入埋点事件（payload 已在调用方序列化为 JSON 字符串）"""
        try:
            self._execute(
                'INSERT INTO event_log (event_type, article_id, payload, client_id, created_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (event_type, article_id, payload, client_id, datetime.now().isoformat()),
                commit=True
            )
            return True
        except Exception as e:
            logger.warning(f"写入事件失败: {e}")
            return False

    def get_recent_events(self, hours: int = 24) -> List[Dict]:
        """获取近期埋点事件"""
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        return self._query(
            'SELECT * FROM event_log WHERE created_at >= ? ORDER BY created_at DESC',
            (since,)
        )

    # ── 热度分 ──

    def update_hotness_scores(self, scores: Dict[str, float]):
        """
        覆盖写入文章热度分（非累加）。
        每次计算时用当前窗口的聚合分直接替换旧值，天然实现衰减。
        """
        if not scores:
            return
        with self._wr_lock:
            cursor = self._conn.cursor()
            for aid, score in scores.items():
                cursor.execute(
                    'UPDATE news_articles SET hotness_score = ? WHERE article_id = ?',
                    (round(score, 4), aid)
                )
            self._conn.commit()
        self._invalidate_news_cache()

    def reset_all_hotness_scores(self):
        """将所有文章热度分归零（在重算前调用，单次 UPDATE 高效）"""
        self._execute('UPDATE news_articles SET hotness_score = 0 WHERE hotness_score != 0', commit=True)

    def close(self):
        """关闭数据库连接（应用退出时调用）"""
        for name, conn in [('写', '_conn'), ('读', '_ro_conn')]:
            if hasattr(self, conn) and getattr(self, conn):
                try:
                    getattr(self, conn).close()
                except Exception as close_err:
                    logger.debug(f"关闭{name}连接异常: {close_err}")
        logger.info("数据库连接已关闭")

    def __del__(self):
        for conn in ['_conn', '_ro_conn']:
            try:
                if hasattr(self, conn) and getattr(self, conn):
                    getattr(self, conn).close()
            except Exception:
                pass  # __del__ 中忽略所有异常，确保不阻塞 GC


# 全局单例
db = DatabaseManager()
