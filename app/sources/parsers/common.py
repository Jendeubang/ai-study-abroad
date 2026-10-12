"""Strict HTML helpers shared by the fixed-source adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag


class SourceParseError(ValueError):
    """A page is outside the narrow structure the adapter has reviewed."""


@dataclass(frozen=True)
class ParsedFact:
    field_name: str
    value: Any
    scope: dict[str, str]
    evidence_excerpt: str
    source_url: str


@dataclass(frozen=True)
class ParsedSourceRecord:
    source_id: str
    programme_key: str
    university: str
    country: str
    city: str | None
    program_name: str
    degree: str
    discipline: str
    title: str
    facts: tuple[ParsedFact, ...]


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def checked_main(source: Any, html: str) -> tuple[Tag, str]:
    parsed = urlsplit(source.url)
    if parsed.scheme != "https" or parsed.hostname != source.host or parsed.username or parsed.password:
        raise SourceParseError("来源 URL 不符合登记的 HTTPS 主机")
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if not isinstance(main, Tag):
        raise SourceParseError("官网页面缺少 main 正文，结构变化需人工复核")
    titles = [normalize_text(item.get_text(" ", strip=True)) for item in main.find_all("h1")]
    if titles != [source.program_name]:
        raise SourceParseError("官网课程标题与来源目录不一致")
    return main, titles[0]


def sections(main: Tag) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for heading in main.find_all(["h2", "h3"]):
        label = normalize_text(heading.get_text(" ", strip=True))
        chunks: list[str] = []
        for node in heading.next_elements:
            if node is heading:
                continue
            if isinstance(node, Tag) and node.name in {"h2", "h3"}:
                break
            if isinstance(node, Tag) and node.name in {"p", "li", "tr", "dd"}:
                text = normalize_text(node.get_text(" ", strip=True))
                if text:
                    chunks.append(text)
        if label:
            result.append((label, " ".join(dict.fromkeys(chunks))))
    return result


def _matching(items: list[tuple[str, str]], names: tuple[str, ...]) -> list[tuple[str, str]]:
    normalized = {name.casefold() for name in names}
    return [item for item in items if item[0].casefold() in normalized and item[1]]


def _parse_candidate_facts(source: Any, page_sections: list[tuple[str, str]]) -> list[ParsedFact]:
    facts: list[ParsedFact] = []
    content = " ".join(text for _, text in page_sections)

    if "tuition" in source.allowed_fields:
        fee_sections = _matching(page_sections, (
            "Tuition", "Tuition fee", "Tuition fees", "Programme fee", "Program fee", "Fees", "Fees and funding",
        ))
        for heading, text in fee_sections:
            year = re.search(r"\b(20\d{2}/\d{2})\b", text)
            mode = re.search(r"\b(full[- ]time|part[- ]time)\b", text, re.IGNORECASE)
            amounts = list(re.finditer(
                r"(?:HK\$|HKD|GBP|SGD|AUD|CAD|CNY|£|\$)\s*([\d,]+(?:\.\d{1,2})?)",
                text, re.IGNORECASE,
            ))
            if not amounts:
                continue
            if not year or not mode or len(amounts) != 1:
                raise SourceParseError("学费缺少学年、学习方式或唯一明确金额，停止猜测")
            token_match = re.match(r"HK\$|HKD|GBP|SGD|AUD|CAD|CNY|£|\$", amounts[0].group(0), re.IGNORECASE)
            if token_match is None:
                continue
            token = token_match.group(0).upper()
            currency = {"HK$": "HKD", "£": "GBP", "$": "USD"}.get(token, token)
            amount = float(amounts[0].group(1).replace(",", ""))
            if not 1_000 <= amount <= 2_000_000:
                raise SourceParseError("学费金额超出预期范围")
            scope = {
                "academic_year": year.group(1),
                "study_mode": re.sub(r"[- ]+", "_", mode.group(1)).lower(),
            }
            category = re.search(r"\b(local|non-local|international|overseas) students?\b", text, re.IGNORECASE)
            if category:
                scope["student_category"] = category.group(1).casefold()
            facts.append(ParsedFact(
                "tuition", {"amount": amount, "currency": currency}, scope,
                f"{heading}: {text[:400]}", source.url,
            ))
            break

    if "intake" in source.allowed_fields:
        match = re.search(
            r"\b(20\d{2})\s+(Fall|Autumn|Spring|Winter)\b.{0,40}?\b(?:intake|entry|application round)\b",
            content, re.IGNORECASE,
        )
        if match:
            term = "Fall" if match.group(2).casefold() == "autumn" else match.group(2).title()
            value = f"{match.group(1)} {term}"
            facts.append(ParsedFact(
                "intake", {"value": value}, {"intake": value},
                match.group(0), source.url,
            ))

    score_specs = (
        ("ielts_min", ("IELTS", "IELTS requirement", "English language requirements", "English proficiency"), 9.0,
         r"(?:IELTS(?:\s*(?:\(Academic\)|Academic))?\s*(?:overall\s*)?(?:minimum\s*)?(?:band\s*)?(?:score\s*)?(?:of|:)?\s*|minimum\s+IELTS(?:\s*(?:\(Academic\)|Academic))?\s+(?:overall\s+)?(?:band\s+)?(?:score\s+)?(?:of|:)?\s*)(\d(?:\.\d)?)"),
        ("toefl_min", ("TOEFL", "TOEFL requirement", "English language requirements", "English proficiency"), 120.0,
         r"(?:TOEFL(?:\s+Internet(?:-based)?\s+Test)?\s*(?:iBT\s*)?(?:minimum\s*)?(?:score\s*)?(?:of|:)?\s*|minimum\s+TOEFL(?:\s+iBT)?\s+(?:score\s+)?(?:of|:)?\s*)(\d{2,3})"),
    )
    for field_name, heading_names, maximum, pattern in score_specs:
        if field_name not in source.allowed_fields:
            continue
        exam_sections = [
            item for item in page_sections
            if item[0].casefold() in {name.casefold() for name in heading_names}
            or re.search(rf"\b{field_name.removesuffix('_min')}\b", item[1], re.IGNORECASE)
        ]
        for heading, text in exam_sections:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            score = float(match.group(1))
            if not 4 <= score <= maximum:
                raise SourceParseError(f"{field_name} 分数不在有效范围")
            facts.append(ParsedFact(
                field_name, {"value": score, "scale": int(maximum)},
                {"applies_when": heading}, match.group(0), source.url,
            ))
            break

    if "academic_standard" in source.allowed_fields:
        entries = _matching(page_sections, (
            "Academic requirements", "Entry requirements", "Admission requirements",
            "Academic entry requirements", "Educational background", "Expected Academic Standard",
        ))
        if entries:
            heading, text = entries[0]
            excerpt = text[:400]
            facts.append(ParsedFact(
                "academic_standard", {"text": excerpt}, {"applies_when": heading},
                excerpt, source.url,
            ))

    if "duration" in source.allowed_fields:
        match = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(year|years|month|months)\s+(full[- ]time|part[- ]time)\b",
            content, re.IGNORECASE,
        )
        if match:
            value = f"{match.group(1)} {match.group(2).lower()}"
            facts.append(ParsedFact(
                "duration", {"value": value},
                {"study_mode": match.group(3).replace(" ", "_").lower()},
                match.group(0), source.url,
            ))

    if "application_rounds" in source.allowed_fields:
        for heading, text in page_sections:
            match = re.search(
                r"\b(?:application\s+)?round\s*(\d+)\b.{0,100}?\bopens?\s+(20\d{2}-\d{2}-\d{2})\b.{0,60}?\b(?:closes?|deadline)\s+(20\d{2}-\d{2}-\d{2})\b",
                text, re.IGNORECASE,
            )
            if not match:
                continue
            opens_on, deadline_on = match.group(2), match.group(3)
            try:
                date.fromisoformat(opens_on)
                date.fromisoformat(deadline_on)
            except ValueError as exc:
                raise SourceParseError("申请轮次日期格式无效") from exc
            if opens_on > deadline_on:
                raise SourceParseError("申请轮次开放时间晚于截止时间")
            round_label = f"Round {match.group(1)}"
            intake_match = re.search(
                r"\b(20\d{2})\s+(Fall|Autumn|Spring|Winter)\b.{0,40}?\b(?:intake|entry|application round)\b",
                text, re.IGNORECASE,
            )
            if intake_match is None:
                raise SourceParseError("申请轮次缺少明确入学季")
            term = "Fall" if intake_match.group(2).casefold() == "autumn" else intake_match.group(2).title()
            intake = f"{intake_match.group(1)} {term}"
            facts.append(ParsedFact(
                "application_rounds",
                {"intake": intake, "round_label": round_label, "opens_on": opens_on, "deadline_on": deadline_on},
                {"intake": intake, "round_label": round_label, "section": heading}, match.group(0), source.url,
            ))
    return facts


def parse_candidate_page(source: Any, html: str, *, required_sections: tuple[str, ...]) -> ParsedSourceRecord:
    main, title = checked_main(source, html)
    page_sections = sections(main)
    present = {label.casefold() for label, _ in page_sections}
    if not any(name.casefold() in present for name in required_sections):
        raise SourceParseError("招生栏目缺失或结构已变更，停止解析")
    facts = _parse_candidate_facts(source, page_sections)
    if len(facts) > 20 or any(
        not fact.evidence_excerpt or len(fact.evidence_excerpt) > 500
        or fact.field_name not in source.allowed_fields or fact.source_url != source.url
        for fact in facts
    ):
        raise SourceParseError("解析结果越过来源字段、URL 白名单或证据长度限制")
    return ParsedSourceRecord(
        source_id=source.source_id, programme_key=source.programme_key,
        university=source.university, country=source.country, city=source.city,
        program_name=source.program_name, degree=source.degree,
        discipline=source.discipline, title=title, facts=tuple(facts),
    )
