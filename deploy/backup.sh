#!/bin/bash
# FlowCapital 数据库备份脚本
# 用法: ./backup.sh
# cron: 0 3 * * * /opt/flowcapital/deploy/backup.sh

set -e

PROJECT_ROOT="/opt/flowcapital"
DB_PATH="$PROJECT_ROOT/data/sqlite.db"
BACKUP_DIR="$PROJECT_ROOT/backup"
LOG_FILE="$PROJECT_ROOT/logs/backup.log"
RETENTION_DAYS=7

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a "$LOG_FILE"; }

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_PATH" ]; then
    log "ERROR: 数据库文件不存在: $DB_PATH"
    exit 1
fi

# 生成备份文件名
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_FILE="$BACKUP_DIR/sqlite_$TIMESTAMP.db.gz"

# 先执行 WAL checkpoint（确保所有数据写入主文件）
sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);"

# 备份 + 压缩
sqlite3 "$DB_PATH" ".dump" | gzip > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    log "备份成功: $BACKUP_FILE ($BACKUP_SIZE)"
else
    log "ERROR: 备份失败"
    exit 1
fi

# 清理过期备份
DELETED=$(find "$BACKUP_DIR" -name "sqlite_*.db.gz" -mtime +$RETENTION_DAYS -delete -print | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "清理了 $DELETED 个过期备份"
fi

# 显示备份统计
TOTAL_BACKUPS=$(find "$BACKUP_DIR" -name "sqlite_*.db.gz" | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)
log "当前备份: $TOTAL_BACKUPS 个, 总大小: $TOTAL_SIZE"
