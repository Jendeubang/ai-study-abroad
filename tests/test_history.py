from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import ChatLog, HistoryConsent, QueryLog


ACTION = {"X-History-Action": "1"}


def test_history_is_opt_in_and_search_without_consent_is_not_saved(client):
    result = client.get("/api/programs", params={"q": "computer science"})
    assert result.status_code == 200
    assert client.get("/api/history").json()["data"] == {"enabled": False, "items": []}
    with client.app.state.session_factory() as db:
        assert list(db.scalars(select(QueryLog))) == []
        assert list(db.scalars(select(HistoryConsent))) == []


def test_consent_saves_redacted_search_recommendation_and_chat(client):
    assert client.post("/api/history/consent").status_code == 403
    consent = client.post("/api/history/consent", headers=ACTION)
    assert consent.status_code == 200
    assert "httponly" in consent.headers["set-cookie"].lower()
    assert "samesite=strict" in consent.headers["set-cookie"].lower()
    client.get("/api/programs", params={"q": "CS 联系 me@example.com 13800138000"})
    client.post("/api/recommend", json={"query": "英国计算机硕士", "page_size": 2})
    client.post("/api/chat", json={"message": "我的邮箱 me@example.com，学费？", "program_ids": []})
    items = client.get("/api/history").json()["data"]["items"]
    assert [item["kind"] for item in items] == ["chat", "recommend", "search"]
    assert "me@example.com" not in str(items)
    assert "13800138000" not in str(items)
    assert "[邮箱]" in str(items)
    assert "[手机号]" in str(items)
    with client.app.state.session_factory() as db:
        searches = list(db.scalars(select(QueryLog)))
        chats = list(db.scalars(select(ChatLog)))
        assert len(searches) == 2 and len(chats) == 1
        assert all(row.ip_hash is None for row in searches)
        assert all(row.owner_hash and len(row.owner_hash) == 64 for row in searches)
        assert all("me@example.com" not in row.raw_query for row in searches)
        assert all("me@example.com" not in row.user_message for row in chats)


def test_history_is_isolated_and_owner_can_delete_one_or_all(client):
    client.post("/api/history/consent", headers=ACTION)
    owner_cookie = client.cookies.get("studycompass_history")
    client.get("/api/programs", params={"q": "英国 CS"})
    item_id = client.get("/api/history").json()["data"]["items"][0]["id"]
    client.cookies.clear()
    client.post("/api/history/consent", headers=ACTION)
    assert client.get("/api/history").json()["data"]["items"] == []
    assert client.delete(f"/api/history/search/{item_id}", headers=ACTION).status_code == 404
    client.cookies.set("studycompass_history", owner_cookie)
    assert client.delete(f"/api/history/search/{item_id}").status_code == 403
    assert client.delete(f"/api/history/search/{item_id}", headers=ACTION).status_code == 200
    assert client.get("/api/history").json()["data"]["items"] == []
    client.get("/api/programs", params={"q": "香港"})
    assert client.delete("/api/history", headers=ACTION).status_code == 200
    assert client.get("/api/history").json()["data"] == {"enabled": True, "items": []}


def test_disable_deletes_history_and_revokes_cookie(client):
    client.post("/api/history/consent", headers=ACTION)
    cookie = client.cookies.get("studycompass_history")
    client.get("/api/programs", params={"q": "人工智能"})
    response = client.delete("/api/history/consent", headers=ACTION)
    assert response.status_code == 200
    assert client.get("/api/history").json()["data"]["enabled"] is False
    client.cookies.set("studycompass_history", cookie)
    assert client.get("/api/history").json()["data"]["enabled"] is False
    client.get("/api/programs", params={"q": "数据科学"})
    with client.app.state.session_factory() as db:
        assert list(db.scalars(select(QueryLog))) == []


def test_expired_entries_are_removed_and_failed_requests_are_not_saved(client):
    client.post("/api/history/consent", headers=ACTION)
    client.get("/api/programs", params={"q": "英国 CS"})
    assert client.post("/api/recommend", json={"query": ""}).status_code == 422
    with client.app.state.session_factory() as db:
        entry = db.scalar(select(QueryLog))
        entry.created_at = datetime.now(timezone.utc) - timedelta(days=91)
        db.commit()
    assert client.get("/api/history").json()["data"]["items"] == []
    with client.app.state.session_factory() as db:
        assert list(db.scalars(select(QueryLog))) == []


def test_history_page_has_explicit_consent_and_safe_rendering(client):
    html = client.get("/").text
    assert 'id="history-opt-in"' in html
    assert 'id="history-list"' in html
    assert "默认不保存" in html
    script = client.get("/static/app.js").text
    assert "textContent" in script
    assert "innerHTML" not in script
