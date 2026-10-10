from fastapi.testclient import TestClient

from app.main import create_app
from app.services.ai_service import AIUnavailable


class FakeAI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    @property
    def available(self):
        return True

    async def complete_json(self, system_prompt, payload):
        self.calls.append((system_prompt, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ai_client(tmp_path, fake):
    app = create_app(database_url=f"sqlite:///{(tmp_path / 'ai.db').as_posix()}", ai_service=fake)
    return TestClient(app)


PARSED_UK_CS = {
    "countries": ["英国"], "degree": "硕士", "discipline": "计算机科学",
    "gpa": {"value": 3.6, "scale": 4.0},
    "language": {"ielts": 7.0, "toefl": None},
    "budget": {"amount": 400000, "currency": "CNY"},
    "intake": None, "preferences": [],
}


def test_parse_returns_validated_ai_json(tmp_path):
    fake = FakeAI(PARSED_UK_CS)
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/parse", json={"query": "英国计算机硕士，GPA 3.6/4，雅思7"})
    assert response.status_code == 200
    assert response.json()["data"]["parsed"]["countries"] == ["英国"]
    assert response.json()["data"]["ai_available"] is True
    assert len(fake.calls) == 1


def test_invalid_ai_parse_falls_back_to_rules(tmp_path):
    fake = FakeAI(AIUnavailable("invalid JSON"))
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/parse", json={"query": "英国/香港 CS 硕士 GPA 3.6/4 雅思7 预算40万"})
    data = response.json()["data"]
    assert response.status_code == 200
    assert data["ai_available"] is False
    assert data["parsed"]["countries"] == ["英国", "香港"]
    assert data["parsed"]["gpa"] == {"value": 3.6, "scale": 4.0}
    assert data["parsed"]["budget"] == {"amount": 400000.0, "currency": "CNY"}


def test_recommendation_uses_verified_database_facts(tmp_path):
    fake = FakeAI(
        PARSED_UK_CS,
        {"items": [
            {"program_id": 1, "facts": [{"field": "country", "value": "英国"}]},
            {"program_id": 2, "facts": [{"field": "discipline", "value": "计算机科学"}]},
        ]},
    )
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/recommend", json={"query": "英国 CS 硕士 GPA 3.6/4 雅思7", "page_size": 10})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["ai_available"] is True
    assert {item["id"] for item in data["items"]} == {1, 2}
    assert "英国" in data["items"][0]["recommendation"]
    assert data["items"][0]["citations"][0]["source_url"] == data["items"][0]["source_url"]
    assert all(item["recommendation"] for item in data["items"])


def test_recommend_combines_text_with_explicit_filters(tmp_path):
    fake = FakeAI(
        PARSED_UK_CS,
        {"items": [{"program_id": 3, "facts": [{"field": "country", "value": "香港"}]}]},
    )
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/recommend", json={
            "query": "英国 CS 硕士 GPA 3.6/4 雅思7",
            "filters": {"country": "香港", "ranking_max": 100, "sort": "ranking"},
        })
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]["items"]] == [3]
    assert response.json()["data"]["items"][0]["recommendation_source"] == "ai_verified"


def test_recommend_rejects_invalid_filter_values(tmp_path):
    fake = FakeAI()
    with ai_client(tmp_path, fake) as client:
        for filters in ({"sort": "random"}, {"ielts": 10}, {"country": ""}):
            response = client.post("/api/recommend", json={"query": "英国 CS 硕士", "filters": filters})
            assert response.status_code == 422
    assert not fake.calls


def test_hallucinated_program_id_downgrades_to_rule_results(tmp_path):
    fake = FakeAI(PARSED_UK_CS, {"items": [{"program_id": 999, "facts": [{"field": "country", "value": "英国"}]}]})
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/recommend", json={"query": "英国 CS 硕士"})
    data = response.json()["data"]
    assert response.status_code == 200
    assert data["ai_available"] is False
    assert data["notice"] == "AI 推荐暂不可用"
    assert data["items"]
    assert all(item["recommendation_source"] == "rules" for item in data["items"])


def test_ai_timeout_downgrades_to_rule_results(tmp_path):
    fake = FakeAI(AIUnavailable("timeout"))
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/recommend", json={"query": "英国 CS 硕士"})
    data = response.json()["data"]
    assert response.status_code == 200
    assert data["ai_available"] is False
    assert data["notice"] == "AI 推荐暂不可用"
    assert data["items"]


def test_recommend_rejects_bad_query(tmp_path):
    fake = FakeAI()
    with ai_client(tmp_path, fake) as client:
        for query in ("", "!!!", "x" * 1001):
            response = client.post("/api/recommend", json={"query": query})
            assert response.status_code == 422
    assert not fake.calls


def test_rule_parser_keeps_hkd_budget_currency(tmp_path):
    fake = FakeAI(AIUnavailable("offline"))
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/parse", json={"query": "香港 CS 硕士，预算40万港币"})
    assert response.status_code == 200
    assert response.json()["data"]["parsed"]["budget"] == {"amount": 400000.0, "currency": "HKD"}


def test_hallucinated_tuition_downgrades_to_rules(tmp_path):
    fake = FakeAI(
        PARSED_UK_CS,
        {"items": [{"program_id": 1, "facts": [{"field": "tuition", "value": 1}]}]},
    )
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/recommend", json={"query": "英国 CS 硕士"})
    assert response.status_code == 200
    assert response.json()["data"]["ai_available"] is False
    assert response.json()["data"]["items"][0]["recommendation_source"] == "rules"


def test_ai_routes_have_stricter_rate_limit(tmp_path):
    fake = FakeAI(*([PARSED_UK_CS] * 10))
    with ai_client(tmp_path, fake) as client:
        for _ in range(10):
            assert client.post("/api/parse", json={"query": "英国 CS 硕士"}).status_code == 200
        response = client.post("/api/parse", json={"query": "英国 CS 硕士"})
    assert response.status_code == 429
    assert response.json()["code"] == 429


def test_toefl_only_query_affects_rule_score(tmp_path):
    fake = FakeAI(AIUnavailable("offline"))
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/recommend", json={"query": "英国 CS 硕士 托福85"})
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert items
    assert any("托福低于" in note for item in items for note in item["match_notes"])
