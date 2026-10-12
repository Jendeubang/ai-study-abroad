from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Program, SourcePermission


def test_admin_source_registry_lists_exact_baseline_and_disabled_candidates(client):
    client.app.state.admin_token = "catalog-test-token"
    headers = {"X-Admin-Token": "catalog-test-token"}

    response = client.get("/api/admin/sources", headers=headers)

    assert response.status_code == 200
    sources = response.json()["data"]
    by_id = {source["source_id"]: source for source in sources}
    assert len(sources) == 13
    assert by_id["hku-msccs-application"]["url"] == (
        "https://master.cds.hku.hk/msccs/about/"
        "msc-in-computer-science-application-procedure-2027-28/"
    )
    assert by_id["hku-msccs-application"]["enabled"] is False
    assert by_id["oxford-msc-advanced-cs"]["enabled"] is False
    assert by_id["cambridge-acs"]["enabled"] is True


def test_permission_can_be_registered_before_a_program_exists(client):
    client.app.state.admin_token = "catalog-test-token"
    headers = {"X-Admin-Token": "catalog-test-token"}
    body = {
        "host": "msc.cse.cuhk.edu.hk",
        "terms_url": "https://www.cuhk.edu.hk/policy/copyright/",
        "use_scope": "local_non_commercial_research",
        "reviewer": "reviewer",
        "note": "已核实仅限本地研究使用范围。",
    }

    response = client.post("/api/admin/sources", headers=headers, json=body)

    assert response.status_code == 200
    with client.app.state.session_factory() as session:
        assert session.scalar(select(Program.id).where(Program.source_url.like("%msc.cse.cuhk.edu.hk%"))) is None
        permission = session.scalar(
            select(SourcePermission).where(SourcePermission.host == body["host"])
        )
        assert permission is not None
        assert permission.use_scope == "local_non_commercial_research"


def test_candidate_source_can_only_be_enabled_after_valid_permission(client):
    client.app.state.admin_token = "catalog-test-token"
    headers = {"X-Admin-Token": "catalog-test-token"}

    denied = client.patch(
        "/api/admin/sources/oxford-msc-advanced-cs",
        headers=headers, json={"enabled": True},
    )
    assert denied.status_code == 422

    permission = {
        "host": "msc.cse.cuhk.edu.hk",
        "terms_url": "https://www.cuhk.edu.hk/policy/copyright/",
        "use_scope": "local_non_commercial_research",
        "reviewer": "reviewer",
        "note": "已核实仅限本地研究使用范围。",
    }
    assert client.post("/api/admin/sources", headers=headers, json=permission).status_code == 200
    enabled = client.patch(
        "/api/admin/sources/cuhk-msc-cs-requirements",
        headers=headers, json={"enabled": True},
    )
    assert enabled.status_code == 200
    listed = client.get("/api/admin/sources", headers=headers).json()["data"]
    cuhk = next(item for item in listed if item["source_id"] == "cuhk-msc-cs-requirements")
    assert cuhk["enabled"] is True


def test_source_scope_and_expiry_are_explicit_in_admin_listing(client):
    client.app.state.admin_token = "catalog-test-token"
    headers = {"X-Admin-Token": "catalog-test-token"}
    body = {
        "host": "www.ox.ac.uk",
        "terms_url": "https://www.ox.ac.uk/about/organisation/legal",
        "use_scope": "local_non_commercial_research",
        "reviewer": "reviewer",
        "note": "已核实本地研究范围，不含公开展示。",
    }
    assert client.post("/api/admin/sources", headers=headers, json=body).status_code == 200

    with client.app.state.session_factory() as session:
        permission = session.scalar(
            select(SourcePermission).where(SourcePermission.host == body["host"])
        )
        permission.checked_at = datetime.now(timezone.utc) - timedelta(days=366)
        session.commit()

    response = client.get("/api/admin/sources", headers=headers)

    assert response.status_code == 200
    oxford = next(item for item in response.json()["data"] if item["host"] == body["host"])
    assert oxford["expired"] is True
    assert oxford["allowed_purposes"] == []


def test_local_research_permission_does_not_allow_public_display(client):
    client.app.state.admin_token = "catalog-test-token"
    headers = {"X-Admin-Token": "catalog-test-token"}
    body = {
        "host": "www.cs.cityu.edu.hk",
        "terms_url": "https://www.cityu.edu.hk/web/copyright",
        "use_scope": "local_non_commercial_research",
        "reviewer": "reviewer",
        "note": "已核实可用于本地研究，未授权公开展示。",
    }
    assert client.post("/api/admin/sources", headers=headers, json=body).status_code == 200

    response = client.get("/api/admin/sources", headers=headers)

    cityu = next(
        item for item in response.json()["data"]
        if item["host"] == body["host"] and item["enabled"]
    )
    assert cityu["allowed_purposes"] == ["local_non_commercial_research"]

