import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models import Program, ProgramCandidate, SyncRun


def _candidate(session, *, status="pending"):
    digest = hashlib.sha256(b"snapshot").hexdigest()
    candidate = ProgramCandidate(
        programme_key="test-cs", source_ids_json='["hku-msccs-application"]',
        source_snapshots_json=json.dumps({"hku-msccs-application": digest}),
        metadata_json=json.dumps({
            "university": "测试大学", "country": "香港", "city": "香港",
            "program_name": "计算机科学硕士", "degree": "硕士", "discipline": "计算机科学",
        }, ensure_ascii=False),
        facts_json=json.dumps([{
            "field_name": "ielts_min", "value": {"value": 6.5},
            "scope": {"intake": "2027 Fall"},
            "source_id": "hku-msccs-application",
            "source_url": "https://master.cds.hku.hk/msccs/about/msc-in-computer-science-application-procedure-2027-28/",
            "source_sha256": digest, "evidence_excerpt": "雅思总分要求不低于 6.5。",
        }], ensure_ascii=False),
        snapshot_sha256=digest, fingerprint=hashlib.sha256(b"candidate").hexdigest(),
        retrieved_at=datetime.now(timezone.utc), status=status,
    )
    session.add(candidate)
    session.commit()
    session.refresh(candidate)
    return candidate


def test_sync_runs_endpoint_lists_latest_run_and_source_statuses(client):
    client.app.state.admin_token = "sync-admin-token"
    with client.app.state.session_factory() as session:
        session.add(SyncRun(
            run_id="00000000-0000-0000-0000-000000000001", trigger="manual",
            started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc),
            eligible_count=2, checked_count=2, changed_count=1, failed_count=1,
            skipped_count=0, pending_count=1,
            source_statuses_json='[{"source_id":"hku-msccs-application","status":"changed","pending":1},{"source_id":"ucl-advanced-cs","status":"failed","message":"页面结构变化"}]',
        ))
        session.commit()

    response = client.get("/api/admin/sync-runs", headers={"X-Admin-Token": "sync-admin-token"})

    assert response.status_code == 200
    run = response.json()["data"][0]
    assert run["failed_count"] == 1
    assert run["pending_count"] == 1
    assert run["source_statuses"][1]["status"] == "failed"


def test_new_program_candidates_can_be_listed_and_rejected(client):
    client.app.state.admin_token = "sync-admin-token"
    with client.app.state.session_factory() as session:
        original_program_count = session.scalar(select(func.count(Program.id)))
        candidate = _candidate(session)
        candidate_id = candidate.id

    headers = {"X-Admin-Token": "sync-admin-token"}
    listed = client.get("/api/admin/program-candidates", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"][0]["program_name"] == "计算机科学硕士"
    assert listed.json()["data"][0]["facts"][0]["evidence_excerpt"] == "雅思总分要求不低于 6.5。"

    rejected = client.post(
        f"/api/admin/program-candidates/{candidate_id}/reject",
        headers=headers,
        json={"reviewer": "审核员", "note": "官网页面与项目数据已完成核验。"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["data"]["status"] == "rejected"
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count(Program.id))) == original_program_count
        assert session.get(ProgramCandidate, candidate_id).review_note == "官网页面与项目数据已完成核验。"


def test_new_program_approval_requires_permission_for_requested_purpose(client):
    client.app.state.admin_token = "sync-admin-token"
    with client.app.state.session_factory() as session:
        original_program_count = session.scalar(select(func.count(Program.id)))
        candidate = _candidate(session)
        candidate_id = candidate.id

    response = client.post(
        f"/api/admin/program-candidates/{candidate_id}/approve?purpose=public_display",
        headers={"X-Admin-Token": "sync-admin-token"},
        json={"reviewer": "审核员", "note": "证据准确，但需要核验公开展示权限。"},
    )

    assert response.status_code == 422
    with client.app.state.session_factory() as session:
        assert session.scalar(select(func.count(Program.id))) == original_program_count
        assert session.get(ProgramCandidate, candidate_id).status == "pending"


def test_sync_admin_endpoints_are_token_protected(client):
    assert client.get("/api/admin/sync-runs").status_code == 403
    assert client.get("/api/admin/program-candidates").status_code == 403

