"""
云服务商LLM客户端
支持多种云服务商API集成
"""
import requests
import json
import time
import hmac
import hashlib
import base64
from typing import Dict, List, Any
import logging

logger = logging.getLogger(__name__)


class CloudLLMClient:
    """
    云服务商LLM客户端
    支持多种云服务商API
    """

    def __init__(self, api_type: str = "openai", api_key: str = None,
                 endpoint: str = None, model: str = None):
        """
        初始化客户端

        Args:
            api_type: 服务商类型 (aliyun/baidu/openai)
            api_key: API密钥
            endpoint: API端点URL
            model: 模型名称
        """
        self.api_type = api_type
        self.api_key = api_key
        self.model = model or "qwen-plus"

        # 默认端点
        if endpoint:
            self.endpoint = endpoint
        elif api_type == "aliyun":
            self.endpoint = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        elif api_type == "baidu":
            self.endpoint = "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/completions"
        elif api_type == "openai":
            self.endpoint = "https://api.openai.com/v1/chat/completions"
        else:
            raise ValueError(f"Unsupported API type: {api_type}")

        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })

    def generate_response(self, prompt: str, system: str = None, **kwargs) -> str:
        """生成响应（返回纯文本）"""
        try:
            if self.api_type == "aliyun":
                return self._aliyun_generate(prompt, system, **kwargs)
            elif self.api_type == "baidu":
                return self._baidu_generate(prompt, system, **kwargs)
            elif self.api_type == "openai":
                return self._openai_generate(prompt, system, **kwargs)
            else:
                raise ValueError(f"Unsupported API type: {self.api_type}")
        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            raise

    def generate_structured(self, prompt: str, system: str = None, **kwargs) -> dict:
        """生成 OpenAI 兼容格式的响应（供 graph_builder/rag_engine 使用）"""
        text = self.generate_response(prompt, system, **kwargs)
        return {"choices": [{"message": {"content": text}}]}

    # ==================== 阿里云 DashScope ====================

    def _aliyun_generate(self, prompt: str, system: str = None, **kwargs) -> str:
        """阿里云 DashScope（OpenAI 兼容格式）"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = {
            'model': self.model,
            'messages': messages,
            'max_tokens': kwargs.get('max_tokens', 2048),
            'temperature': kwargs.get('temperature', 0.3),
        }

        response = self.session.post(
            self.endpoint,
            headers=headers,
            json=data,
            timeout=kwargs.get('timeout', 30)
        )
        response.raise_for_status()
        result = response.json()
        return result.get('choices', [{}])[0].get('message', {}).get('content', '')

    # ==================== 百度千帆 ====================

    def _baidu_generate(self, prompt: str, system: str = None, **kwargs) -> str:
        """百度千帆API"""
        # 百度需要先获取 access_token
        access_token = self._get_baidu_token()
        url = f"https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{self.model}?access_token={access_token}"

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = {
            'messages': messages,
            'temperature': kwargs.get('temperature', 0.3),
        }

        response = self.session.post(url, json=data, timeout=kwargs.get('timeout', 30))
        response.raise_for_status()
        result = response.json()
        return result.get('result', '')

    def _get_baidu_token(self) -> str:
        """获取百度千帆 access_token"""
        parts = self.api_key.split('|')
        if len(parts) != 2:
            raise ValueError("百度API密钥格式错误，应为 'API_KEY|SECRET_KEY'")

        api_key, secret_key = parts[0], parts[1]
        # 使用 POST body 而非 URL 查询参数，防止密钥被代理/负载均衡日志记录
        response = requests.post(
            "https://aip.baidubce.com/oauth/2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": api_key,
                "client_secret": secret_key,
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get('access_token', '')

    # ==================== OpenAI 兼容 ====================

    def _openai_generate(self, prompt: str, system: str = None, **kwargs) -> str:
        """OpenAI 兼容 API"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        data = {
            'model': self.model,
            'messages': messages,
            'temperature': kwargs.get('temperature', 0.3),
            'max_tokens': kwargs.get('max_tokens', 2048)
        }

        response = self.session.post(
            self.endpoint,
            headers=headers,
            json=data,
            timeout=kwargs.get('timeout', 30)
        )
        response.raise_for_status()
        result = response.json()
        return result.get('choices', [{}])[0].get('message', {}).get('content', '')
