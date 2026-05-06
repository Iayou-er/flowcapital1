#!/usr/bin/env python3
"""清理数据库中的重复新闻（相同 URL 多条记录）"""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'sqlite.db')

if not os.path.exists(db_path):
    print("数据库不存在")
    exit()

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 查看重复情况
cursor.execute('''
    SELECT url, COUNT(*) as cnt FROM news_articles
    WHERE url != '' GROUP BY url HAVING cnt > 1
    ORDER BY cnt DESC LIMIT 10
''')
duplicates = cursor.fetchall()

if not duplicates:
    print("没有重复新闻")
    conn.close()
    exit()

print(f"发现 {len(duplicates)} 组重复新闻:")
for url, cnt in duplicates:
    print(f"  [{cnt} 条] {url[:80]}...")

# 去重：保留每组中 id 最小的一条
cursor.execute('''
    DELETE FROM news_articles
    WHERE id NOT IN (
        SELECT MIN(id) FROM news_articles GROUP BY COALESCE(NULLIF(url,''), title)
    )
''')
deleted = conn.total_changes
conn.commit()

print(f"已删除 {deleted} 条重复记录")

# 查看总数
cursor.execute('SELECT COUNT(*) FROM news_articles')
print(f"剩余新闻总数: {cursor.fetchone()[0]}")

conn.close()
