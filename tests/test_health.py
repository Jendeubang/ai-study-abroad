def test_health_has_standard_envelope_and_request_id(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"]["status"] == "ok"
    assert body["request_id"] == response.headers["x-request-id"]


def test_ordinary_api_rate_limit_uses_standard_error(client):
    for _ in range(60):
        response = client.get("/api/health")
        assert response.status_code == 200
    response = client.get("/api/health")
    assert response.status_code == 429
    assert response.json()["code"] == 429
    assert response.json()["request_id"] == response.headers["x-request-id"]


def test_slow_request_times_out_with_standard_error(tmp_path):
    import asyncio

    from fastapi.testclient import TestClient

    from app.main import create_app

    application = create_app(
        database_url=f"sqlite:///{(tmp_path / 'timeout.db').as_posix()}",
        request_timeout_seconds=0.01,
    )

    @application.get("/api/slow-test")
    async def slow_test():
        await asyncio.sleep(0.05)
        return {"unexpected": True}

    with TestClient(application) as test_client:
        response = test_client.get("/api/slow-test")
    assert response.status_code == 504
    assert response.json()["code"] == 504
    assert response.json()["request_id"] == response.headers["x-request-id"]
