from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from app.models import ApplicationRound, EvidenceFact, Program


def test_three_previews_render(client):
    for path in ("/", "/preview/research", "/preview/studio"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="search-form"' in response.text
        assert 'src="/static/app.js?v=' in response.text
        assert 'href="/static/tailwind.css"' in response.text
        assert "模拟数据" in response.text


def test_home_escapes_query_and_unknown_preview_returns_404(client):
    payload = '<script>alert("x")</script>'
    response = client.get(f"/?q={quote(payload)}")
    assert response.status_code == 200
    assert payload not in response.text
    assert "&lt;script&gt;" in response.text
    assert client.get("/static/tailwind.css").status_code == 200
    assert client.get("/preview/unknown").status_code == 404


def test_editorial_removes_decoration_but_keeps_privacy_and_source_context(client):
    home = client.get("/").text
    assert "PROJECT DATABASE" not in home
    assert "第三阶段 · 前端方案" not in home
    assert "StudyCompass AI" in home
    assert 'aria-label="StudyCompass AI 首页"' in home
    assert "隐私：" in home
    assert "不保证录取" in home
    assert "来源" in home
    assert "PROJECT DATABASE" in client.get("/preview/research").text


def test_home_defaults_to_reviewed_sources_when_available(client):
    assert '<option value="all" selected>' in client.get("/").text
    with client.app.state.session_factory() as session:
        program = session.get(Program, 1)
        program.data_origin = "official_reviewed"
        session.commit()
    assert '<option value="official" selected>' in client.get("/").text


def test_program_detail_renders_source_and_disclaimer(client):
    response = client.get("/programs/1")
    assert response.status_code == 200
    assert "Northbridge University" in response.text
    assert "StudyCompass AI" in response.text
    assert 'rel="noopener noreferrer"' in response.text
    assert "不保证录取" in response.text
    assert client.get("/programs/99999").status_code == 404


def test_program_detail_flags_expired_or_stale_information(client):
    with client.app.state.session_factory() as session:
        program = session.get(Program, 1)
        program.deadline = date.today() - timedelta(days=1)
        program.updated_at = datetime.now(timezone.utc) - timedelta(days=181)
        session.commit()
    response = client.get("/programs/1")
    assert response.status_code == 200
    assert "截止日期已过" in response.text
    assert "超过 180 天未更新" in response.text


def test_detail_shows_field_scope_rounds_and_review_alert(client):
    now = datetime.now(timezone.utc)
    with client.app.state.session_factory() as session:
        program = session.get(Program, 1)
        program.data_origin = "official_reviewed"
        program.source_id = "test-official"
        program.source_url = "https://example.edu/course"
        program.reviewed_at = now - timedelta(days=91)
        session.add(EvidenceFact(
            program_id=program.id, field_name="tuition",
            value_json='{"amount":320000,"currency":"HKD"}',
            scope_json='{"academic_year":"2027/28","study_mode":"full_time"}',
            evidence_excerpt="官网说明全日制学费为 HK$320,000。",
            source_url=program.source_url, source_sha256="a" * 64,
            retrieved_at=now, reviewed_at=now - timedelta(days=91),
            reviewer="tester", active=True,
        ))
        session.add(ApplicationRound(
            program_id=program.id, intake="2027 Fall", round_label="第一轮",
            opens_on=None, deadline_on=date(2027, 1, 15),
            evidence_excerpt="官网说明第一轮截止日期为 2027-01-15。",
            source_url=program.source_url, source_sha256="a" * 64,
            retrieved_at=now, reviewed_at=now, reviewer="tester", active=True,
        ))
        session.commit()
    response = client.get("/programs/1")
    assert response.status_code == 200
    assert "核对超过 90 天" in response.text
    assert "2027/28" in response.text
    assert "全日制" in response.text
    assert "第一轮" in response.text
    assert "官网说明全日制学费" in response.text
