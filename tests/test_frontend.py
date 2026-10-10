from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from app.models import Program


def test_three_previews_render(client):
    for path in ("/", "/preview/research", "/preview/studio"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="search-form"' in response.text
        assert 'src="/static/app.js"' in response.text
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
