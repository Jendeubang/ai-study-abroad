from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.crawl_official_requirements import ROBOTS_URL, SOURCES, TERMS_URL


ROOT = Path(__file__).resolve().parent.parent


def snapshot():
    source = SOURCES["cambridge-acs"]
    return {
        "schema_version": 1,
        "manual_review_required": True,
        "usage": "local_non_commercial_research",
        "robots_url": ROBOTS_URL,
        "terms_url": TERMS_URL,
        "records": [{
            "source_id": source.key,
            "university": "University of Cambridge",
            "program_name": source.program_name,
            "degree": "MPhil",
            "academic_standard": "First class Honours Degree",
            "ielts": {"overall": 7.5, "listening": 7.0, "writing": 7.0, "reading": 7.0, "speaking": 7.0},
            "toefl": {"overall": 107, "listening": 25, "writing": 25, "reading": 25, "speaking": 25},
            "subject_requirement_needs_review": True,
            "source_url": source.url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": "a" * 64,
        }],
    }


def approved_review(batch):
    from app.services.official_import_service import prepare_review

    review = prepare_review(batch)
    review["rights_confirmed"] = True
    review["records"][0]["approved"] = True
    review["records"][0]["review_note"] = "已逐项核对官网学历和语言要求；仅用于本地非商业研究"
    return review


def test_review_starts_unapproved_and_import_requires_explicit_review(client):
    from app.services.official_import_service import ReviewError, import_reviewed_snapshot, prepare_review

    batch = snapshot()
    review = prepare_review(batch)
    assert review["rights_confirmed"] is False
    assert review["records"][0]["approved"] is False
    with client.app.state.session_factory() as session:
        with pytest.raises(ReviewError, match="使用范围|确认"):
            import_reviewed_snapshot(session, batch, review)
    assert client.get("/api/programs?country=英国&source_type=official").json()["data"]["total"] == 0


def test_reviewed_import_is_searchable_labeled_and_idempotent(client):
    from app.services.official_import_service import import_reviewed_snapshot

    batch = snapshot()
    review = approved_review(batch)
    with client.app.state.session_factory() as session:
        first = import_reviewed_snapshot(session, batch, review)
        second = import_reviewed_snapshot(session, batch, review)
    assert first["imported"] == 1
    assert second["unchanged"] == 1

    response = client.get("/api/programs?q=Advanced%20Computer%20Science&source_type=official")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    item = response.json()["data"]["items"][0]
    assert item["data_origin"] == "official_reviewed"
    assert item["source_url"] == batch["records"][0]["source_url"]
    assert item["academic_standard"] == "First class Honours Degree"
    assert item["gpa_min"] is None
    assert item["tuition"] is None
    assert item["source_retrieved_at"]
    assert item["reviewed_at"]
    assert client.get("/api/programs?source_type=demo").json()["data"]["total"] > 0
    first_page = client.get("/api/programs?page_size=8").json()["data"]["items"]
    assert first_page[0]["data_origin"] == "official_reviewed"

    detail = client.get(f"/programs/{item['id']}").text
    assert "官网已核对" in detail
    assert "官网采集时间" in detail
    assert "人工核对时间" in detail
    assert "First class Honours Degree" in detail
    assert "申请截止" in detail
    assert "模拟排名" not in detail
    assert "此页全部院校" not in detail
    assert 'rel="noopener noreferrer"' in detail
    assert "官网已核对" in client.get("/").text


def test_changed_snapshot_cannot_use_old_review(client):
    from app.services.official_import_service import ReviewError, import_reviewed_snapshot

    batch = snapshot()
    review = approved_review(batch)
    changed = deepcopy(batch)
    changed["records"][0]["ielts"]["overall"] = 6.0
    with client.app.state.session_factory() as session:
        with pytest.raises(ReviewError, match="快照|变更"):
            import_reviewed_snapshot(session, changed, review)
    assert client.get("/api/programs?source_type=official").json()["data"]["total"] == 0


def test_unknown_source_and_missing_note_cannot_be_imported(client):
    from app.services.official_import_service import ReviewError, import_reviewed_snapshot, prepare_review

    batch = snapshot()
    changed = deepcopy(batch)
    changed["records"][0]["source_url"] = "https://example.org/attacker"
    with pytest.raises(ReviewError, match="来源"):
        prepare_review(changed)

    review = approved_review(batch)
    review["records"][0]["review_note"] = ""
    with client.app.state.session_factory() as session:
        with pytest.raises(ReviewError, match="核对说明"):
            import_reviewed_snapshot(session, batch, review)


def test_cli_requires_review_file_before_writing_database(tmp_path):
    from app.main import create_app
    from fastapi.testclient import TestClient

    crawl_path = tmp_path / "crawl.json"
    review_path = tmp_path / "review.json"
    database = tmp_path / "app.db"
    crawl_path.write_text(json.dumps(snapshot(), ensure_ascii=False), encoding="utf-8")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    command = [sys.executable, "-m", "scripts.review_and_import"]
    common = ["--input", str(crawl_path), "--review", str(review_path)]

    prepared = subprocess.run(command + ["prepare"] + common, cwd=ROOT, env=env, capture_output=True, text=True)
    assert prepared.returncode == 0, prepared.stderr
    rejected = subprocess.run(command + ["import"] + common, cwd=ROOT, env=env, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert not database.exists()

    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["rights_confirmed"] = True
    review["records"][0]["approved"] = True
    review["records"][0]["review_note"] = "已核对官网学历和语言成绩"
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    imported = subprocess.run(command + ["import"] + common, cwd=ROOT, env=env, capture_output=True, text=True)
    assert imported.returncode == 0, imported.stderr
    app = create_app(database_url=f"sqlite:///{database.as_posix()}")
    with TestClient(app) as client:
        assert client.get("/api/programs?source_type=official").json()["data"]["total"] == 1
