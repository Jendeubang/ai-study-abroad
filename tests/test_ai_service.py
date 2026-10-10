import asyncio

import httpx
import pytest

from app.services.ai_service import AIService, AIUnavailable


def test_ai_service_rejects_non_json_model_content():
    def handler(request):
        assert request.headers["authorization"] == "Bearer test-secret"
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    service = AIService(api_key="test-secret", base_url="https://example.org/v1", model="test-model", transport=httpx.MockTransport(handler))
    with pytest.raises(AIUnavailable):
        asyncio.run(service.complete_json("Return JSON", {"query": "hello"}))


def test_ai_service_rejects_rate_limit():
    service = AIService(
        api_key="test-secret", base_url="https://example.org/v1", model="test-model",
        transport=httpx.MockTransport(lambda request: httpx.Response(429, json={"error": "limited"})),
    )
    with pytest.raises(AIUnavailable):
        asyncio.run(service.complete_json("Return JSON", {"query": "hello"}))
