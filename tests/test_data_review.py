from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import pytest
import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, ApplicationRound, EvidenceFact, Program, ReviewCandidate, SourceCheck
from app.services.additional_import_service import import_reviewed, prepare_review
from app.services.data_review_service import (
    ReviewWorkflowError, active_evidence, active_rounds, approve_candidate,
    backfill_additional_review, record_source_failure, review_alerts,
    set_source_permission, stage_additional_batch, stage_round_candidate,
)
from scripts.crawl_additional_programs import SOURCES, parse_cityu_html
from tests.test_additional_sources import CITYU_HTML


def approved_batch():
    source = SOURCES["cityu-cs"]
    record = {
        "source_id": source.key, "university": source.university,
        "country": "香港", "city": "香港", "program_name": source.program_name,
        "degree": "硕士", "discipline": source.discipline,
        **parse_cityu_html(CITYU_HTML, source),
        "source_url": source.url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source_sha256": "a" * 64,
    }
    batch = {
        "schema_version": 1, "manual_review_required": True,
        "usage": "local_non_commercial_research",
        "robots_urls": [source.robots_url], "records": [record],
    }
    review = prepare_review(batch)
    review["rights_confirmed"] = True
    review["rights_note"] = "用户已核对官网内容使用范围，仅用于本地研究。"
    review["records"][0]["approved"] = True
    review["records"][0]["review_note"] = "已逐项核对项目和雅思要求。"
    return batch, review


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        batch, review = approved_batch()
        import_reviewed(session, batch, review)
        yield session, batch, review
    engine.dispose()


def test_backfill_requires_matching_approved_snapshot_and_keeps_scope(db):
    session, batch, review = db
    assert backfill_additional_review(session, batch, review) == 1
    program = session.scalar(select(Program).where(Program.source_id == "cityu-cs"))
    facts = active_evidence(session, program.id)
    assert len(facts) == 1
    assert facts[0].field_name == "ielts_min"
    assert facts[0].value == {"value": 6.0}
    assert facts[0].scope == {"applies_when": "english_proficiency_proof_required"}
    assert "IELTS" in facts[0].evidence_excerpt
    assert session.scalar(select(SourceCheck)).status == "unchanged"
    assert backfill_additional_review(session, batch, review) == 0
    changed = deepcopy(batch)
    changed["records"][0]["source_sha256"] = "b" * 64
    with pytest.raises(ReviewWorkflowError, match="快照"):
        backfill_additional_review(session, changed, review)


def test_changed_fact_waits_for_rights_and_approval_before_publishing(db):
    session, batch, review = db
    backfill_additional_review(session, batch, review)
    changed = deepcopy(batch)
    changed["records"][0]["ielts_min"] = 6.5
    changed["records"][0]["evidence"]["ielts_min"] = (
        "an overall band score of 6.5 in International English Language Testing System (IELTS)"
    )
    changed["records"][0]["source_sha256"] = "b" * 64
    result = stage_additional_batch(session, changed)
    assert result["pending"] == 1
    assert stage_additional_batch(session, changed)["pending"] == 0
    candidate = session.scalar(select(ReviewCandidate))
    program = session.get(Program, candidate.program_id)
    assert program.ielts_min == 6.0
    assert candidate.old_value == {"value": 6.0}
    with pytest.raises(ReviewWorkflowError, match="使用范围"):
        approve_candidate(session, candidate.id, reviewer="tester", note="对照官网核对雅思分数。")
    set_source_permission(
        session, host="www.cs.cityu.edu.hk",
        terms_url="https://www.cityu.edu.hk/web/copyright",
        use_scope="local_non_commercial_research", reviewer="tester",
        note="已核对本地研究的使用范围。",
    )
    approve_candidate(session, candidate.id, reviewer="tester", note="对照官网核对雅思分数。")
    assert program.ielts_min == 6.5
    assert [fact.value for fact in active_evidence(session, program.id)] == [{"value": 6.5}]
    assert candidate.status == "approved"


def test_rounds_stay_separate_and_do_not_set_generic_deadline(db):
    session, batch, review = db
    program = session.scalar(select(Program).where(Program.source_id == "cityu-cs"))
    set_source_permission(
        session, host="www.cs.cityu.edu.hk",
        terms_url="https://www.cityu.edu.hk/web/copyright",
        use_scope="local_non_commercial_research", reviewer="tester",
        note="已核对本地研究的使用范围。",
    )
    for name, deadline in (("第一轮", date(2027, 1, 15)), ("第二轮", date(2027, 3, 15))):
        candidate = stage_round_candidate(
            session, program_id=program.id, intake="2027 Fall", round_label=name,
            opens_on=None, deadline_on=deadline, source_url=program.source_url,
            evidence_excerpt=f"官网招生页显示{name}截止日期为 {deadline.isoformat()}。",
            source_sha256="c" * 64, retrieved_at=datetime.now(timezone.utc),
        )
        approve_candidate(session, candidate.id, reviewer="tester", note="已对照官网核对申请轮次。")
    rounds = active_rounds(session, program.id)
    assert [(item.round_label, item.deadline_on) for item in rounds] == [
        ("第一轮", date(2027, 1, 15)), ("第二轮", date(2027, 3, 15)),
    ]
    assert program.deadline is None


def test_failed_check_and_old_review_raise_review_alert(db):
    session, batch, review = db
    program = session.scalar(select(Program).where(Program.source_id == "cityu-cs"))
    program.reviewed_at = datetime.now(timezone.utc) - timedelta(days=91)
    session.commit()
    assert "核对超过 90 天" in review_alerts(session, program)
    record_source_failure(session, "cityu-cs", "CrawlError")
    assert "最近一次采集失败" in review_alerts(session, program)
    assert session.scalar(select(SourceCheck)).status == "failed"


def test_public_permission_must_be_explicit(db):
    session, _, _ = db
    policy = set_source_permission(
        session, host="www.cs.cityu.edu.hk",
        terms_url="https://www.cityu.edu.hk/web/copyright",
        use_scope="local_non_commercial_research", reviewer="tester",
        note="已核对本地研究的使用范围。",
    )
    assert policy.use_scope == "local_non_commercial_research"
    assert policy.use_scope != "public_display"


def test_unverified_source_terms_can_be_recorded_without_enabling_crawl(db):
    from scripts.sync_additional_programs import sync_one

    session, _, _ = db
    source = SOURCES["cityu-cs"]
    policy = set_source_permission(
        session, host=source.host,
        terms_url="https://www.cityu.edu.hk/web/copyright",
        use_scope="unverified", reviewer="tester",
        note="自动采集和公开展示许可尚未从条款中确认。",
    )
    assert policy.use_scope == "unverified"
    with pytest.raises(ReviewWorkflowError, match="使用范围"):
        with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("未核实来源不得请求官网"))) as client:
            sync_one(session, client, source, pause=lambda _: None)


def test_admin_can_save_unverified_scope_and_it_stays_blocked(client):
    from scripts.sync_additional_programs import sync_one

    client.app.state.admin_token = "test-secret-value"
    batch, review = approved_batch()
    with client.app.state.session_factory() as session:
        import_reviewed(session, batch, review)
        program = session.scalar(select(Program).where(Program.source_id == "cityu-cs"))
        source = SOURCES["cityu-cs"]
    headers = {"X-Admin-Token": "test-secret-value"}
    payload = {
        "host": source.host,
        "terms_url": "https://www.cityu.edu.hk/web/copyright",
        "use_scope": "unverified",
        "reviewer": "tester",
        "note": "已查看官网版权说明，自动采集许可尚未确认。",
    }
    saved = client.post("/api/admin/sources", headers=headers, json=payload)
    assert saved.status_code == 200
    listing = client.get("/api/admin/sources", headers=headers).json()["data"]
    assert next(item for item in listing if item["host"] == source.host)["use_scope"] == "unverified"
    with client.app.state.session_factory() as session:
        with pytest.raises(ReviewWorkflowError, match="使用范围"):
            with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("未核实来源不得请求官网"))) as client_http:
                sync_one(session, client_http, source, pause=lambda _: None)


def test_sync_records_page_change_and_fetch_failure_without_publishing(db):
    from scripts.sync_additional_programs import sync_one

    session, batch, review = db
    backfill_additional_review(session, batch, review)
    source = SOURCES["cityu-cs"]
    with pytest.raises(ReviewWorkflowError, match="使用范围"):
        with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("不应请求官网"))) as client:
            sync_one(session, client, source, pause=lambda _: None)
    set_source_permission(
        session, host=source.host,
        terms_url="https://www.cityu.edu.hk/web/copyright",
        use_scope="local_non_commercial_research", reviewer="tester",
        note="已核对本地研究的使用范围。",
    )

    def response(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"})
        return httpx.Response(200, text=CITYU_HTML, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        result = sync_one(session, client, source, pause=lambda _: None)
    assert result["pending"] == 1
    assert session.scalar(select(ReviewCandidate)).kind == "page"
    assert session.scalar(select(Program).where(Program.source_id == source.key)).source_sha256 == "a" * 64

    def blocked(request):
        return httpx.Response(500, text="unavailable", headers={"content-type": "text/plain"})

    with httpx.Client(transport=httpx.MockTransport(blocked)) as client:
        result = sync_one(session, client, source, pause=lambda _: None)
    assert result["status"] == "failed"
    assert review_alerts(session, session.scalar(select(Program).where(Program.source_id == source.key)))


def test_admin_review_requires_token_rights_and_explicit_decision(client):
    client.app.state.admin_token = "test-secret-value"
    batch, review = approved_batch()
    with client.app.state.session_factory() as session:
        import_reviewed(session, batch, review)
        backfill_additional_review(session, batch, review)
        changed = deepcopy(batch)
        changed["records"][0]["ielts_min"] = 6.5
        changed["records"][0]["evidence"]["ielts_min"] = (
            "an overall band score of 6.5 in International English Language Testing System (IELTS)"
        )
        changed["records"][0]["source_sha256"] = "b" * 64
        stage_additional_batch(session, changed)
        candidate_id = session.scalar(select(ReviewCandidate.id))
    assert client.get("/api/admin/reviews").status_code == 403
    assert client.get("/api/admin/reviews", headers={"X-Admin-Token": "wrong"}).status_code == 403
    headers = {"X-Admin-Token": "test-secret-value"}
    listed = client.get("/api/admin/reviews", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["old_value"] == {"value": 6.0}
    body = {"reviewer": "tester", "note": "已对照官网核对雅思分数。"}
    assert client.post(f"/api/admin/reviews/{candidate_id}/approve", headers=headers, json=body).status_code == 422
    policy = {
        "host": "www.cs.cityu.edu.hk", "terms_url": "https://www.cityu.edu.hk/web/copyright",
        "use_scope": "local_non_commercial_research", "reviewer": "tester",
        "note": "已核对本地非商业研究使用范围。",
    }
    assert client.post("/api/admin/sources", headers=headers, json=policy).status_code == 200
    approved = client.post(f"/api/admin/reviews/{candidate_id}/approve", headers=headers, json=body)
    assert approved.status_code == 200
    assert approved.json()["data"]["status"] == "approved"
    assert client.post(f"/api/admin/reviews/{candidate_id}/approve", headers=headers, json=body).status_code == 422
    result = client.get("/api/programs?source_type=official").json()["data"]["items"][0]
    assert result["ielts_min"] == 6.5
    assert client.get("/admin/reviews").status_code == 200
    assert "test-secret-value" not in client.get("/admin/reviews").text


def test_admin_round_requires_recent_successful_source_check(client):
    client.app.state.admin_token = "test-secret-value"
    headers = {"X-Admin-Token": "test-secret-value"}
    batch, review = approved_batch()
    with client.app.state.session_factory() as session:
        import_reviewed(session, batch, review)
        program = session.scalar(select(Program).where(Program.source_id == "cityu-cs"))
        program_id = program.id
    body = {
        "program_id": program_id, "intake": "2027 Fall", "round_label": "第一轮",
        "opens_on": None, "deadline_on": "2027-01-15",
        "evidence_excerpt": "官网说明第一轮截止日期为 2027-01-15。",
    }
    assert client.post("/api/admin/rounds/candidates", headers=headers, json=body).status_code == 422
    with client.app.state.session_factory() as session:
        backfill_additional_review(session, batch, review)
    response = client.post("/api/admin/rounds/candidates", headers=headers, json=body)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "pending"
