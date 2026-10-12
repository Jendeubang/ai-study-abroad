def test_empty_search_returns_seeded_programs(client):
    response = client.get("/api/programs")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] >= 6
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert all(item["source_url"].startswith("https://example.org/") for item in data["items"])


def test_homepage_uses_cache_busted_app_script(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "/static/app.js?v=20261012-1" in response.text


def test_fts_search_and_country_filter(client):
    response = client.get("/api/programs", params={"q": "computer science", "country": "英国"})

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert items
    assert all(item["country"] == "英国" for item in items)
    assert all("computer" in (item["program_name"] + item["description"]).lower() for item in items)


def test_pagination_is_stable(client):
    first = client.get("/api/programs", params={"page": 1, "page_size": 2}).json()["data"]
    second = client.get("/api/programs", params={"page": 2, "page_size": 2}).json()["data"]

    assert len(first["items"]) == len(second["items"]) == 2
    assert first["total"] == second["total"]
    assert {item["id"] for item in first["items"]}.isdisjoint(
        {item["id"] for item in second["items"]}
    )


def test_deadline_sort_is_ascending(client):
    items = client.get("/api/programs", params={"sort": "deadline"}).json()["data"]["items"]
    deadlines = [item["deadline"] for item in items if item["deadline"]]
    assert deadlines == sorted(deadlines)


def test_sort_and_page_size_are_whitelisted(client):
    for params in ({"sort": "id desc; drop table programs"}, {"page_size": 101}):
        response = client.get("/api/programs", params=params)
        assert response.status_code == 422
        assert response.json()["code"] != 0


def test_excessive_and_symbol_only_query_are_rejected(client):
    for query in ("x" * 501, "!!!<>$()"):
        response = client.get("/api/programs", params={"q": query})
        assert response.status_code == 422


def test_sql_metacharacters_do_not_change_result_set(client):
    response = client.get("/api/programs", params={"q": "computer' OR 1=1 --"})
    assert response.status_code == 200
    assert response.json()["data"]["total"] < client.get("/api/programs").json()["data"]["total"]


def test_project_detail_and_missing_project(client):
    listing = client.get("/api/programs").json()["data"]["items"][0]
    detail = client.get(f"/api/programs/{listing['id']}")
    missing = client.get("/api/programs/999999")

    assert detail.status_code == 200
    assert detail.json()["data"]["id"] == listing["id"]
    assert detail.json()["data"]["source_url"] == listing["source_url"]
    assert missing.status_code == 404
    assert missing.json()["request_id"]


def test_budget_different_currency_is_marked_uncertain(client):
    response = client.get("/api/programs", params={"country": "英国", "budget": 400000})
    assert response.status_code == 200
    assert response.json()["data"]["items"]
    assert any(
        "币种不同" in warning
        for item in response.json()["data"]["items"]
        for warning in item["match_notes"]
    )


def test_html_query_is_returned_only_as_json_data(client):
    response = client.get("/api/programs", params={"q": "<script>alert(1)</script>"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "<script>" not in response.text
