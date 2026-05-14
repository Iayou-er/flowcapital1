"""
JSON 结构化日志配置
多进程安全：QueueHandler + QueueListener 串行写入文件
"""
import atexit
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from queue import Queue

LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'logs'
)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)

    log_queue = Queue(-1)
    queue_handler = QueueHandler(log_queue)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JSONFormatter())

    file_handler = RotatingFileHandler(
        os.path.join(LOG_DIR, 'app.json.log'),
        maxBytes=100 * 1024 * 1024,  # 100MB
        backupCount=30,
        encoding='utf-8',
    )
    file_handler.setFormatter(JSONFormatter())

    listener = QueueListener(log_queue, console_handler, file_handler)
    listener.start()
    atexit.register(listener.stop)

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL))
    root.addHandler(queue_handler)
