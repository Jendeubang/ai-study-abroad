import asyncio

import httpx
import pytest

from app.services.ai_service import AIService, AIUnavailable


def test_deepseek_json_prompts_include_json_keyword_and_output_example():
    from app.services.chat_service import CHAT_PROMPT
    from app.services.parser_service import PARSE_PROMPT
    from app.services.recommend_service import REASON_PROMPT

    for prompt in (PARSE_PROMPT, REASON_PROMPT, CHAT_PROMPT):
        assert "json" in prompt
        assert "{" in prompt and "}" in prompt


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


def test_deepseek_disables_thinking_for_fast_structured_output():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    service = AIService(
        api_key="test-secret", base_url="https://api.deepseek.com", model="deepseek-flash",
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(service.complete_json("Return JSON", {"query": "hello"}))

    body = requests[0].read()
    import json
    payload = json.loads(body)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 512


def test_non_deepseek_provider_does_not_receive_deepseek_parameters():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    service = AIService(
        api_key="test-secret", base_url="https://example.org/v1", model="test-model",
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(service.complete_json("Return JSON", {"query": "hello"}))

    import json
    payload = json.loads(requests[0].read())
    assert "thinking" not in payload
    assert payload["max_tokens"] == 512
