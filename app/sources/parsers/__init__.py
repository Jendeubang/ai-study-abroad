"""Deterministic adapters for the exact pages in the source catalogue."""

from __future__ import annotations

from typing import Any

from app.sources.parsers.common import ParsedFact, ParsedSourceRecord, SourceParseError
from app.sources.parsers import cuhk, hku, oxford, ucl


def _legacy_record(source: Any, html: str) -> ParsedSourceRecord:
    if source.parser_key in {"cityu", "hkust"}:
        from scripts.crawl_additional_programs import CrawlError, SOURCES, parse_cityu_html, parse_hkust_html
        legacy = SOURCES.get(source.source_id)
        if legacy is None:
            raise SourceParseError("未登记的港校来源")
        try:
            if source.parser_key == "cityu":
                parsed = parse_cityu_html(html, legacy)
                facts = (ParsedFact(
                    "ielts_min", {"value": parsed["ielts_min"], "scale": 9},
                    {"applies_when": "english_proficiency_proof_required"},
                    parsed["evidence"]["ielts_min"], source.url,
                ),)
            else:
                parsed = parse_hkust_html(html, legacy)
                facts = (
                    ParsedFact("tuition", {"amount": parsed["tuition"], "currency": parsed["currency"]},
                               {"academic_year": "2027/28", "intake": "2027 Fall", "study_mode": "full_time"},
                               parsed["evidence"]["tuition"], source.url),
                    ParsedFact("intake", {"value": parsed["intake"]}, {"academic_year": "2027/28"},
                               parsed["evidence"]["intake"], source.url),
                )
        except CrawlError as exc:
            raise SourceParseError(str(exc)) from exc
    elif source.parser_key == "cambridge":
        from scripts.crawl_official_requirements import CrawlError, SOURCES, parse_cambridge_html
        legacy = SOURCES.get(source.source_id)
        if legacy is None:
            raise SourceParseError("未登记的剑桥来源")
        try:
            parsed = parse_cambridge_html(html, legacy)
        except CrawlError as exc:
            raise SourceParseError(str(exc)) from exc
        facts = (
            ParsedFact("academic_standard", {"text": parsed["academic_standard"]},
                       {"applies_when": "Expected Academic Standard"},
                       parsed["academic_standard"], source.url),
            ParsedFact("language_details", {"ielts": parsed["ielts"], "toefl": parsed["toefl"]},
                       {"applies_when": "Language Requirement"},
                       f"IELTS overall {parsed['ielts']['overall']}; TOEFL overall {parsed['toefl']['overall']}", source.url),
        )
    else:
        raise SourceParseError("未知来源解析器，停止采集")
    return ParsedSourceRecord(
        source_id=source.source_id, programme_key=source.programme_key,
        university=source.university, country=source.country, city=source.city,
        program_name=source.program_name, degree=source.degree,
        discipline=source.discipline, title=source.program_name, facts=facts,
    )


def parse_source_html(source: Any, html: str) -> ParsedSourceRecord:
    parser_key = getattr(source, "parser_key", None)
    if parser_key in {"cityu", "hkust", "cambridge"}:
        record = _legacy_record(source, html)
    elif parser_key == "hku":
        record = hku.parse(source, html)
    elif parser_key == "cuhk":
        record = cuhk.parse(source, html)
    elif parser_key == "oxford":
        record = oxford.parse(source, html)
    elif parser_key == "ucl":
        record = ucl.parse(source, html)
    else:
        raise SourceParseError("未知来源解析器，停止采集")
    if record.source_id != source.source_id or any(
        item.field_name not in source.allowed_fields or item.source_url != source.url
        for item in record.facts
    ):
        raise SourceParseError("解析结果越过来源字段或 URL 白名单")
    return record


__all__ = ["ParsedFact", "ParsedSourceRecord", "SourceParseError", "parse_source_html"]
