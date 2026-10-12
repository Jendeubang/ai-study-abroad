import importlib
import importlib.util
from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import ApplicationRound, Base, EvidenceFact, Program, ReviewCandidate, SourceCheck, University
from app.sources.catalog import seed_source_catalog, set_source_enabled
from app.services.data_review_service import set_source_permission


CITYU_HTML = """<main><h1>Master of Science in Computer Science</h1><h2>Admissions</h2>
<p>Entrance Requirements</p><p>Applicants need a recognised bachelor's degree.</p>
<p>an overall band score of 6.5 in International English Language Testing System (IELTS)</p>
<p>Non-local Applicants</p></main>"""


def sync_api():
    module_name = "scripts.sync_program_sources"
    assert importlib.util.find_spec(module_name) is not None, "unified source sync runner is not implemented"
    return importlib.import_module(module_name)


def models():
    assert hasattr(__import__("app.models", fromlist=["SyncRun"]), "SyncRun"), "sync history schema is not implemented"
    from app.models import ProgramCandidate, SyncRun
    return ProgramCandidate, SyncRun


def database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    seed_source_catalog(session)
    session.commit()
    return engine, session


def register_permission(session, source):
    set_source_permission(
        session, host=source.host,
        terms_url=f"https://{source.host}/terms",
        use_scope="local_non_commercial_research", reviewer="tester",
        note="已核查官网条款，仅限本地研究用途。",
    )
    set_source_enabled(session, source.source_id, enabled=True)


def respond_with(html_by_host, fail_hosts=()):
    visited = []

    def respond(request):
        visited.append(str(request.url))
        if request.url.host in fail_hosts:
            return httpx.Response(403, text="blocked", request=request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n", headers={"content-type": "text/plain"}, request=request)
        html = html_by_host.get(request.url.path, html_by_host.get(request.url.host, ""))
        return httpx.Response(200, text=html, headers={"content-type": "text/html"}, request=request)

    return httpx.MockTransport(respond), visited


def test_unified_source_sync_runner_exists():
    assert sync_api().sync_sources


def test_legacy_sync_cli_delegates_to_unified_runner(monkeypatch):
    legacy = importlib.import_module("scripts.sync_additional_programs")
    unified = sync_api()
    forwarded = []
    monkeypatch.setattr("sys.argv", ["sync_additional_programs", "--course", "cityu-cs"])
    monkeypatch.setattr(unified, "main", lambda args: forwarded.extend(args or []) or 0)

    result = legacy.main()

    assert result == 0
    assert forwarded == ["--source", "cityu-cs"]


def test_initial_program_is_staged_for_review_without_creating_program():
    runner = sync_api()
    ProgramCandidate, SyncRun = models()
    engine, session = database()
    try:
        source = session.get(__import__("app.models", fromlist=["SourceDefinition"]).SourceDefinition,
                             "cuhk-msc-cs-requirements")
        register_permission(session, source)
        html = """<main><h1>MSc in Computer Science</h1><h2>Entry Requirements</h2>
        <p>Applicants should hold a recognised bachelor's degree. Minimum IELTS overall score of 6.5.</p>
        </main>"""
        transport, _ = respond_with({source.host: html})
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            summary = runner.sync_sources(session, client, [source], now=datetime.now(timezone.utc), pause=lambda _: None)

        candidate = session.scalar(select(ProgramCandidate))
        check = session.scalar(select(SourceCheck))
        run = session.scalar(select(SyncRun))
        assert summary.pending == 1
        assert candidate.status == "pending"
        assert candidate.programme_key == "cuhk-msc-cs"
        assert "ielts_min" in {fact["field_name"] for fact in candidate.facts}
        assert session.scalar(select(Program)) is None
        assert check.sync_run_id == run.id
        assert check.status == "changed"
        review_service = importlib.import_module("app.services.data_review_service")
        assert hasattr(review_service, "approve_program_candidate"), "initial programme review is not implemented"
        approved = review_service.approve_program_candidate(
            session, candidate.id, reviewer="reviewer",
            note="已逐项核对官网项目名称和语言要求。",
        )
        program = session.scalar(select(Program).where(Program.source_id == source.source_id))
        facts = list(session.scalars(select(EvidenceFact).where(EvidenceFact.program_id == program.id)))
        assert approved.status == "approved"
        assert program.data_origin == "official_reviewed"
        assert len(facts) == len(candidate.facts)
        assert all(fact.source_url == source.url and fact.source_sha256 for fact in facts)
        assert session.scalar(select(SourceCheck).where(SourceCheck.program_id == program.id)) is not None
    finally:
        session.close()
        engine.dispose()


def existing_cityu(session, source):
    university = University(name=source.university, country=source.country)
    session.add(university)
    session.flush()
    program = Program(
        university_id=university.id, university=source.university, country=source.country,
        city=source.city, program_name=source.program_name, degree=source.degree,
        discipline=source.discipline, source_url=source.url, website=source.url,
        data_origin="official_reviewed", source_id=source.source_id,
        source_sha256="a" * 64, ielts_min=6.0,
    )
    session.add(program)
    session.flush()
    session.add(EvidenceFact(
        program_id=program.id, field_name="ielts_min",
        value_json='{"scale":9,"value":6.0}',
        scope_json='{"applies_when":"english_proficiency_proof_required"}',
        evidence_excerpt="IELTS overall band score 6.0", source_url=source.url,
        source_sha256="a" * 64, retrieved_at=datetime.now(timezone.utc),
        reviewed_at=datetime.now(timezone.utc), reviewer="tester", active=True,
    ))
    session.commit()
    return program


def test_changed_field_is_staged_and_published_value_is_unchanged():
    runner = sync_api()
    models()
    engine, session = database()
    try:
        source = session.get(__import__("app.models", fromlist=["SourceDefinition"]).SourceDefinition, "cityu-cs")
        register_permission(session, source)
        program = existing_cityu(session, source)
        transport, _ = respond_with({source.host: CITYU_HTML})
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            summary = runner.sync_sources(session, client, [source], now=datetime.now(timezone.utc), pause=lambda _: None)
        candidate = session.scalar(select(ReviewCandidate).where(ReviewCandidate.kind == "fact"))
        assert summary.changed == 1 and summary.pending == 1
        assert candidate.old_value == {"scale": 9, "value": 6.0}
        assert candidate.new_value == {"scale": 9, "value": 6.5}
        assert program.ielts_min == 6.0
    finally:
        session.close()
        engine.dispose()


def test_multiple_pages_for_one_program_merge_into_one_review_candidate():
    runner = sync_api()
    ProgramCandidate, _ = models()
    engine, session = database()
    try:
        SourceDefinition = __import__("app.models", fromlist=["SourceDefinition"]).SourceDefinition
        application = session.get(SourceDefinition, "hku-msccs-application")
        details = session.get(SourceDefinition, "hku-msccs-details")
        register_permission(session, application)
        set_source_enabled(session, details.source_id, enabled=True)
        pages = {
            "/msccs/about/msc-in-computer-science-application-procedure-2027-28/": """<main>
            <h1>MSc in Computer Science</h1><h2>Application procedure</h2>
            <p>2027 Fall application round 1 opens 2026-10-01 and closes 2026-12-01.</p></main>""",
            "/msccs/admission/application-details/": """<main>
            <h1>MSc in Computer Science</h1><h2>Tuition Fees</h2>
            <p>2027/28 full-time international students pay HKD 240,000.</p>
            <h2>English Language Requirements</h2><p>Minimum IELTS Academic overall score of 6.5.</p></main>""",
        }
        transport, _ = respond_with(pages)
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            summary = runner.sync_sources(
                session, client, [application, details],
                now=datetime.now(timezone.utc), pause=lambda _: None,
            )
        candidate = session.scalar(select(ProgramCandidate))
        assert summary.changed == 2
        assert summary.pending == 1
        assert set(candidate.source_ids) == {application.source_id, details.source_id}
        assert {fact["field_name"] for fact in candidate.facts} == {
            "intake", "application_rounds", "tuition", "ielts_min",
        }
    finally:
        session.close()
        engine.dispose()


def test_approved_initial_program_keeps_application_round_as_separate_row():
    runner = sync_api()
    ProgramCandidate, _ = models()
    engine, session = database()
    try:
        SourceDefinition = __import__("app.models", fromlist=["SourceDefinition"]).SourceDefinition
        source = session.get(SourceDefinition, "hku-msccs-application")
        register_permission(session, source)
        html = """<main><h1>MSc in Computer Science</h1><h2>Application procedure</h2>
        <p>2027 Fall application round 1 opens 2026-10-01 and closes 2026-12-01.</p></main>"""
        transport, _ = respond_with({source.host: html})
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            runner.sync_sources(session, client, [source], now=datetime.now(timezone.utc), pause=lambda _: None)
        candidate = session.scalar(select(ProgramCandidate))
        service = importlib.import_module("app.services.data_review_service")
        service.approve_program_candidate(
            session, candidate.id, reviewer="reviewer",
            note="已对照官网申请轮次页面核对开放和截止日期。",
        )
        round_record = session.scalar(select(ApplicationRound))
        assert round_record.intake == "2027 Fall"
        assert round_record.round_label == "Round 1"
        assert round_record.opens_on.isoformat() == "2026-10-01"
        assert round_record.deadline_on.isoformat() == "2026-12-01"
        assert round_record.source_url == source.url
    finally:
        session.close()
        engine.dispose()


def test_same_snapshot_is_unchanged_and_page_only_change_waits_for_review():
    runner = sync_api()
    models()
    engine, session = database()
    try:
        source = session.get(__import__("app.models", fromlist=["SourceDefinition"]).SourceDefinition, "cityu-cs")
        register_permission(session, source)
        program = existing_cityu(session, source)
        program.ielts_min = 6.5
        evidence = session.scalar(select(EvidenceFact))
        evidence.value_json = '{"scale":9,"value":6.5}'
        session.commit()
        transport, _ = respond_with({source.host: CITYU_HTML})
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            first = runner.sync_sources(session, client, [source], now=datetime.now(timezone.utc), pause=lambda _: None)
        assert first.changed == 1  # the page digest differs from the reviewed baseline
        page_candidate = session.scalar(select(ReviewCandidate).where(ReviewCandidate.kind == "page"))
        assert page_candidate is not None
        session.delete(page_candidate)
        program.source_sha256 = first.source_digests[source.source_id]
        session.commit()
        transport, _ = respond_with({source.host: CITYU_HTML})
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            second = runner.sync_sources(session, client, [source], now=datetime.now(timezone.utc), pause=lambda _: None)
        assert second.unchanged == 1
        assert session.scalar(select(ReviewCandidate)) is None

        changed_html = CITYU_HTML.replace("<main>", "<main>\n<!-- harmless page-only formatting -->")
        transport, _ = respond_with({source.host: changed_html})
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            third = runner.sync_sources(session, client, [source], now=datetime.now(timezone.utc), pause=lambda _: None)
        assert third.changed == 1
        assert session.scalar(select(ReviewCandidate).where(ReviewCandidate.kind == "page")) is not None
        assert program.ielts_min == 6.5
    finally:
        session.close()
        engine.dispose()


def test_permission_skip_makes_no_http_calls_and_failure_does_not_stop_next_source():
    runner = sync_api()
    ProgramCandidate, _ = models()
    engine, session = database()
    try:
        SourceDefinition = __import__("app.models", fromlist=["SourceDefinition"]).SourceDefinition
        cuhk = session.get(SourceDefinition, "cuhk-msc-cs-requirements")
        transport = httpx.MockTransport(lambda request: pytest.fail("permission-denied source sent an HTTP request"))
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            skipped = runner.sync_sources(session, client, [cuhk], now=datetime.now(timezone.utc), pause=lambda _: None)
        assert skipped.skipped == 1
        cityu = session.get(SourceDefinition, "cityu-cs")
        register_permission(session, cuhk)
        register_permission(session, cityu)
        transport, visited = respond_with({cuhk.host: "", cityu.host: CITYU_HTML}, fail_hosts={cuhk.host})
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            summary = runner.sync_sources(session, client, [cuhk, cityu], now=datetime.now(timezone.utc), pause=lambda _: None)
        assert summary.failed == 1
        assert summary.checked == 2
        assert any("www.cs.cityu.edu.hk/robots.txt" in url for url in visited)
        assert session.scalar(select(ProgramCandidate)) is not None
    finally:
        session.close()
        engine.dispose()
