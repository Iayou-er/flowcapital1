"""
多通道告警通知（钉钉、企业微信）
"""
import os
import logging
import requests


class AlertManager:
    def __init__(self):
        self.dingtalk_webhook = os.getenv("DINGTALK_WEBHOOK")
        self.wecom_webhook = os.getenv("WECOM_WEBHOOK")

    def send_dingtalk(self, title: str, content: str):
        if not self.dingtalk_webhook:
            return
        try:
            requests.post(
                self.dingtalk_webhook,
                json={
                    "msgtype": "markdown",
                    "markdown": {"title": title, "text": f"## {title}\n{content}"},
                },
                timeout=5,
            )
        except Exception as e:
            logging.getLogger(__name__).error(f"钉钉告警发送失败: {e}")

    def send_wecom(self, title: str, content: str):
        if not self.wecom_webhook:
            return
        try:
            requests.post(
                self.wecom_webhook,
                json={
                    "msgtype": "text",
                    "text": {"content": f"{title}\n{content}"},
                },
                timeout=5,
            )
        except Exception as e:
            logging.getLogger(__name__).error(f"企业微信告警发送失败: {e}")

    def send(self, title: str, content: str):
        """同时推送到所有已配置渠道"""
        self.send_dingtalk(title, content)
        self.send_wecom(title, content)


alert_manager = AlertManager()
