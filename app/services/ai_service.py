"""Small OpenAI-compatible JSON client. No credentials or model output are logged."""

import json
from typing import Any
from urllib.parse import urlparse

import httpx


class AIUnavailable(Exception):
    """The configured model could not return a usable JSON response."""


class AIService:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 6,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._model = model.strip()
        hostname = (urlparse(self._base_url).hostname or "").lower()
        self._is_deepseek = hostname == "deepseek.com" or hostname.endswith(".deepseek.com")
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def available(self) -> bool:
        return bool(self._api_key and self._model and self._base_url)

    async def complete_json(self, system_prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise AIUnavailable("AI 未配置")
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": 512,
        }
        if self._is_deepseek:
            # These extraction tasks need concise structured output, not DeepSeek's
            # high-effort default reasoning mode, which adds substantial latency.
            body["thinking"] = {"type": "disabled"}
        timeout = httpx.Timeout(self._timeout, connect=min(5, self._timeout))
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or len(content) > 50_000:
                raise ValueError("AI 输出格式无效")
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("AI 输出不是 JSON 对象")
            return parsed
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            raise AIUnavailable(type(error).__name__) from error
