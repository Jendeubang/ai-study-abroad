from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Program
from app.services.additional_import_service import (
    AdditionalReviewError, import_reviewed, prepare_review,
)
from scripts.crawl_additional_programs import (
    CrawlError, SOURCES, crawl, parse_cityu_html, parse_hkust_html,
)


CITYU_HTML = """
<main>
  <h1>Master of Science in Computer Science</h1>
  <h2>Admissions</h2>
  <p>Entrance Requirements</p>
  <p>Applicants need a recognised bachelor's degree in a computing discipline.</p>
  <p>an overall band score of 6.0 in International English Language Testing System (IELTS)</p>
  <p>Non-local Applicants</p>
</main>
"""

HKUST_HTML = """
<main>
  <h1>MSc in Information Technology</h1>
  <h2>Program Fee</h2>
  <p>The nominal program fee for 2027/28 Fall intake for full-time study is HK$320,000
  and part-time study is HK$260,000 respectively.</p>
  <h2>Application</h2>
  <h3>For 2027/28 Fall intake (September 2027)</h3>
</main>
"""


def test_cityu_parser_keeps_only_current_ielts_scale():
    source = SOURCES["cityu-cs"]
    facts = parse_cityu_html(CITYU_HTML, source)
    assert facts["ielts_min"] == 6.0
    assert facts["tuition"] is None
    assert "IELTS" in facts["evidence"]["ielts_min"]
    with pytest.raises(CrawlError, match="雅思"):
        parse_cityu_html(CITYU_HTML.replace("band score of 6.0", "band score of 3.0"), source)


def test_hkust_parser_scopes_fee_to_2027_full_time():
    source = SOURCES["hkust-it"]
    facts = parse_hkust_html(HKUST_HTML, source)
    assert facts["tuition"] == 320000
    assert facts["currency"] == "HKD"
    assert facts["intake"] == "2027 Fall"
    assert facts["ielts_min"] is None
    with pytest.raises(CrawlError, match="学费学年"):
        parse_hkust_html(HKUST_HTML.replace("2027/28 Fall intake for full-time", "2026/27 Fall intake for full-time"), source)
    with pytest.raises(CrawlError, match="学费学年|全日制学费"):
        parse_hkust_html(HKUST_HTML.replace("full-time study is HK$320,000", "part-time study is HK$320,000"), source)


def test_changed_title_is_rejected():
    with pytest.raises(CrawlError, match="标题"):
        parse_cityu_html(CITYU_HTML.replace("Master of Science in Computer Science", "Other Course"), SOURCES["cityu-cs"])


def test_robots_disallow_stops_before_course_request():
    visited = []

    def respond(request):
        visited.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /academic-programmes/\n", headers={"content-type": "text/plain"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(CrawlError, match="不允许"):
            crawl(client, [SOURCES["cityu-cs"]], pause=lambda _: None)
    assert visited == ["/robots.txt"]


def test_review_defaults_to_denied_and_import_is_idempotent():
    source = SOURCES["cityu-cs"]
    facts = parse_cityu_html(CITYU_HTML, source)
    batch = {
        "schema_version": 1,
        "manual_review_required": True,
        "usage": "local_non_commercial_research",
        "robots_urls": [source.robots_url],
        "records": [{
            "source_id": source.key, "university": source.university,
            "country": "香港", "city": "香港", "program_name": source.program_name,
            "degree": "硕士", "discipline": source.discipline,
            **facts,
            "source_url": source.url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": "a" * 64,
        }],
    }
    review = prepare_review(batch)
    assert review["rights_confirmed"] is False
    assert review["records"][0]["approved"] is False
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            with pytest.raises(AdditionalReviewError, match="人工确认"):
                import_reviewed(session, batch, review)
            assert session.scalar(select(Program)) is None
            approved = deepcopy(review)
            approved["rights_confirmed"] = True
            approved["rights_note"] = "已人工检查学校官网内容使用范围，仅在本地研究。"
            approved["records"][0]["approved"] = True
            approved["records"][0]["review_note"] = "已对照官网核对项目名称与雅思总分。"
            changed = deepcopy(batch)
            changed["records"][0]["ielts_min"] = 7.0
            with pytest.raises(AdditionalReviewError, match="港城大雅思"):
                import_reviewed(session, changed, approved)
            assert import_reviewed(session, batch, approved)["imported"] == 1
            assert import_reviewed(session, batch, approved)["unchanged"] == 1
            program = session.scalar(select(Program))
            assert program.data_origin == "official_reviewed"
            assert program.ielts_min == 6.0
            assert program.toefl_min is None
            assert program.source_url == source.url
    finally:
        engine.dispose()


def test_official_detail_labels_partial_hkust_and_cityu_facts():
    from app.main import templates

    common = dict(
        university="Official University", country="香港", city="香港", degree="硕士",
        discipline="计算机科学", data_origin="official_reviewed", deadline=None,
        duration=None, source_url="https://example.edu/course",
        website="https://example.edu/course", source_retrieved_at=datetime.now(timezone.utc),
        reviewed_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        academic_standard=None, toefl_min=None, gpa_min=None, language_details=None,
    )
    template = templates.get_template("program_detail.html")
    hkust = SimpleNamespace(
        **common, source_id="hkust-it", program_name="MSc in Information Technology",
        tuition=320000, currency="HKD", intake="2027 Fall", ielts_min=None,
        description="官网课程摘要。",
    )
    hkust_html = template.render(program=hkust, style="editorial", deadline_expired=False, data_stale=False)
    assert "2027/28 全日制学费" in hkust_html
    assert "320,000 HKD" in hkust_html
    assert "语言要求</span><strong>未采集" in hkust_html
    cityu = SimpleNamespace(
        **common, source_id="cityu-cs", program_name="Master of Science in Computer Science",
        tuition=None, currency=None, intake=None, ielts_min=6.0,
        description="官网招生要求摘要。",
    )
    cityu_html = template.render(program=cityu, style="editorial", deadline_expired=False, data_stale=False)
    assert "雅思分数适用于需要提交英语成绩的申请人" in cityu_html
    assert "托福分数</span><strong>未采集" in cityu_html
