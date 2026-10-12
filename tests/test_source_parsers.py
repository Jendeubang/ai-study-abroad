import importlib
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.sources.catalog import source_specs


def parse_source(source_id, html):
    parser_name = "app.sources.parsers"
    assert importlib.util.find_spec(parser_name) is not None, "shared deterministic parser is not implemented"
    module = importlib.import_module(parser_name)
    return module.parse_source_html(source_specs_by_id()[source_id], html)


def source_specs_by_id():
    return {source.source_id: source for source in source_specs()}


def page(title, *sections):
    body = "".join(f"<section><h2>{heading}</h2>{content}</section>" for heading, content in sections)
    return f"<html><main><h1>{title}</h1>{body}</main></html>"


def test_hku_application_page_extracts_only_explicit_intake_and_round():
    html = page("MSc in Computer Science", (
        "Application procedure",
        "<p>2027 Fall application round 1 opens 2026-10-01 and closes 2026-12-01.</p>",
    ))

    record = parse_source("hku-msccs-application", html)

    assert record.programme_key == "hku-msccs"
    assert [(fact.field_name, fact.value) for fact in record.facts] == [
        ("intake", {"value": "2027 Fall"}),
        ("application_rounds", {
            "round_label": "Round 1", "opens_on": "2026-10-01", "deadline_on": "2026-12-01",
        }),
    ]
    assert all(fact.source_url.startswith("https://master.cds.hku.hk/") for fact in record.facts)


def test_hku_details_page_keeps_year_mode_currency_and_ielts_scope():
    html = page("MSc in Computer Science",
        ("Tuition Fees", "<p>2027/28 full-time international students pay HKD 240,000.</p>"),
        ("English Language Requirements", "<p>Minimum IELTS Academic overall score of 6.5.</p>"),
    )

    record = parse_source("hku-msccs-details", html)
    facts = {fact.field_name: fact for fact in record.facts}

    assert facts["tuition"].value == {"amount": 240000.0, "currency": "HKD"}
    assert facts["tuition"].scope == {
        "academic_year": "2027/28", "study_mode": "full_time", "student_category": "international",
    }
    assert facts["ielts_min"].value == {"value": 6.5, "scale": 9}
    assert "6.5" in facts["ielts_min"].evidence_excerpt


def test_cuhk_parser_extracts_requirements_without_inventing_gpa():
    html = page("MSc in Computer Science", (
        "Entry Requirements",
        "<p>Applicants should hold a recognised bachelor's degree. Minimum IELTS overall score of 6.5.</p>",
    ))

    record = parse_source("cuhk-msc-cs-requirements", html)

    assert {fact.field_name for fact in record.facts} == {"academic_standard", "ielts_min"}
    assert not any(fact.field_name == "gpa_min" for fact in record.facts)


@pytest.mark.parametrize(
    ("source_id", "title", "fee_heading", "tuition", "duration"),
    [
        ("oxford-msc-advanced-cs", "MSc in Advanced Computer Science", "Fees and funding", "GBP 35,000", "1 year full-time"),
        ("ucl-computer-science-msc", "Computer Science MSc", "Tuition fees", "GBP 38,000", "1 year full-time"),
    ],
)
def test_uk_adapters_capture_explicit_study_facts(source_id, title, fee_heading, tuition, duration):
    html = page(title,
        ("Entry requirements", "<p>IELTS overall score 7.0 and TOEFL iBT 100.</p><p>Applicants require a 2:1 honours degree.</p>"),
        (fee_heading, f"<p>2027/28 full-time tuition is {tuition}.</p>"),
        ("Course overview", f"<p>{duration}</p>"),
    )

    record = parse_source(source_id, html)
    facts = {fact.field_name: fact for fact in record.facts}

    assert facts["tuition"].value["currency"] == "GBP"
    assert facts["tuition"].scope["academic_year"] == "2027/28"
    assert facts["duration"].value == {"value": "1 year"}
    assert facts["ielts_min"].value["value"] == 7.0
    assert facts["toefl_min"].value["value"] == 100.0


def test_missing_optional_fields_are_omitted_and_not_guessed():
    source = source_specs_by_id()["cuhk-msc-cs-requirements"]
    html = page(source.program_name, ("Entry Requirements", "<p>Applicants need a recognised bachelor's degree.</p>"))

    record = parse_source(source.source_id, html)

    assert [fact.field_name for fact in record.facts] == ["academic_standard"]


def test_malformed_score_and_changed_title_stop_parsing():
    source = source_specs_by_id()["cuhk-msc-cs-requirements"]
    malformed = page(source.program_name, (
        "Entry Requirements", "<p>Minimum IELTS overall score of 3.0.</p>",
    ))
    changed = page("Unrelated programme", ("Entry Requirements", "<p>IELTS 6.5</p>"))
    module = importlib.import_module("app.sources.parsers") if importlib.util.find_spec("app.sources.parsers") else None
    if module is None:
        pytest.fail("shared deterministic parser is not implemented")
    with pytest.raises(module.SourceParseError, match="分数"):
        module.parse_source_html(source, malformed)
    with pytest.raises(module.SourceParseError, match="标题"):
        module.parse_source_html(source, changed)


def test_unknown_parser_and_fields_outside_allowlist_are_never_emitted():
    module = importlib.import_module("app.sources.parsers") if importlib.util.find_spec("app.sources.parsers") else None
    if module is None:
        pytest.fail("shared deterministic parser is not implemented")
    source = source_specs_by_id()["oxford-msc-advanced-cs"]
    html = page(source.program_name,
        ("Entry requirements", "<p>IELTS overall score 7.0.</p>"),
        ("Fees and funding", "<p>2027/28 full-time tuition is GBP 35,000.</p>"),
    )
    restricted = replace(source, allowed_fields=("ielts_min",))
    record = module.parse_source_html(restricted, html)
    assert {fact.field_name for fact in record.facts} == {"ielts_min"}
    with pytest.raises(module.SourceParseError, match="未知|解析器"):
        module.parse_source_html(replace(source, parser_key="unknown"), html)


def test_legacy_collectors_derive_their_pages_from_the_shared_catalogue(monkeypatch):
    from app.sources import catalog

    original = source_specs()
    changed = tuple(
        replace(item, url="https://www.cs.cityu.edu.hk/changed/path")
        if item.source_id == "cityu-cs" else
        replace(item, url="https://www.postgraduate.study.cam.ac.uk/changed/cambridge")
        if item.source_id == "cambridge-acs" else item
        for item in original
    )
    monkeypatch.setattr(catalog, "source_specs", lambda: changed)
    root = Path(__file__).resolve().parent.parent

    def load_isolated(name, path):
        spec = importlib.util.spec_from_file_location(name, root / path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    try:
        additional = load_isolated("catalog_test_additional", "scripts/crawl_additional_programs.py")
        official = load_isolated("catalog_test_official", "scripts/crawl_official_requirements.py")
        assert additional.SOURCES["cityu-cs"].url == "https://www.cs.cityu.edu.hk/changed/path"
        assert official.SOURCES["cambridge-acs"].url == (
            "https://www.postgraduate.study.cam.ac.uk/changed/cambridge"
        )
    finally:
        sys.modules.pop("catalog_test_additional", None)
        sys.modules.pop("catalog_test_official", None)
