from tests.test_recommend import FakeAI, ai_client


def test_chat_answer_cites_exact_project_fact(tmp_path):
    fake = FakeAI({"unknown": False, "facts": [{"program_id": 1, "field": "tuition", "value": 36000}]})
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/chat", json={"message": "第一个项目学费是多少？", "program_ids": [1]})
    data = response.json()["data"]
    assert response.status_code == 200
    assert "36000 GBP" in data["answer"]
    assert data["citations"] == [{"program_id": 1, "source_url": "https://example.org/northbridge/cs"}]


def test_chat_unknown_without_evidence_returns_required_phrase(tmp_path):
    fake = FakeAI({"unknown": True, "facts": []})
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/chat", json={"message": "毕业生就业率是多少？", "program_ids": [1]})
    assert response.status_code == 200
    assert response.json()["data"]["answer"] == "根据当前检索结果无法确定"
    assert response.json()["data"]["citations"] == []


def test_chat_hallucinated_value_is_not_returned(tmp_path):
    fake = FakeAI({"unknown": False, "facts": [{"program_id": 1, "field": "tuition", "value": 1}]})
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/chat", json={"message": "学费是多少？", "program_ids": [1]})
    assert response.status_code == 200
    assert response.json()["data"]["answer"] == "根据当前检索结果无法确定"
    assert response.json()["data"]["ai_available"] is False


def test_chat_rejects_unselected_program_ids(tmp_path):
    fake = FakeAI()
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/chat", json={"message": "学费是多少？", "program_ids": [999]})
    assert response.status_code == 200
    assert response.json()["data"]["answer"] == "根据当前检索结果无法确定"
    assert not fake.calls


def test_chat_rejects_true_but_irrelevant_fact(tmp_path):
    fake = FakeAI({"unknown": False, "facts": [{"program_id": 1, "field": "tuition", "value": 36000}]})
    with ai_client(tmp_path, fake) as client:
        response = client.post("/api/chat", json={"message": "毕业生就业率是多少？", "program_ids": [1]})
    assert response.status_code == 200
    assert response.json()["data"]["answer"] == "根据当前检索结果无法确定"
