"""Prometheus Pushgateway 指标推送"""
import logging
import os

from prometheus_client import push_to_gateway

logger = logging.getLogger(__name__)

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "http://localhost:9091")


def push_metrics(registry):
    """推送指标到 Pushgateway"""
    try:
        push_to_gateway(PUSHGATEWAY_URL, job="flowcapital-scheduler", registry=registry)
    except Exception as e:
        logger.warning("Pushgateway 推送失败: %s", e)
