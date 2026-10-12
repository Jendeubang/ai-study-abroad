"""Review and import only explicitly approved additional university facts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Program, University
from scripts.crawl_additional_programs import SOURCES


class AdditionalReviewError(ValueError):
    """The collected batch or approval does not meet the import contract."""


RECORD_FIELDS = {
    "source_id", "university", "country", "city", "program_name", "degree",
    "discipline", "tuition", "currency", "intake", "ielts_min", "evidence",
    "source_url", "retrieved_at", "source_sha256",
}


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise AdditionalReviewError("采集时间无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AdditionalReviewError("采集时间无效") from exc
    if parsed.tzinfo is None:
        raise AdditionalReviewError("采集时间缺少时区")
    now = datetime.now(timezone.utc)
    if parsed > now + timedelta(minutes=5) or parsed < now - timedelta(days=180):
        raise AdditionalReviewError("采集快照过期或时间无效")
    return parsed.astimezone(timezone.utc)


def _records(batch: object) -> list[dict]:
    if not isinstance(batch, dict) or set(batch) != {
        "schema_version", "manual_review_required", "usage", "robots_urls", "records",
    }:
        raise AdditionalReviewError("采集文件格式无效")
    if (batch["schema_version"] != 1 or batch["manual_review_required"] is not True
            or batch["usage"] != "local_non_commercial_research"):
        raise AdditionalReviewError("采集文件的使用范围无效")
    records = batch["records"]
    if not isinstance(records, list) or not 1 <= len(records) <= len(SOURCES):
        raise AdditionalReviewError("采集记录数量无效")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
            raise AdditionalReviewError("采集记录字段无效")
        key = record["source_id"]
        source = SOURCES.get(key) if isinstance(key, str) else None
        if source is None or key in seen:
            raise AdditionalReviewError("来源不在已核准的课程名单内")
        seen.add(key)
        if (
            record["university"] != source.university
            or record["country"] != "香港" or record["city"] != "香港"
            or record["program_name"] != source.program_name
            or record["degree"] != "硕士"
            or record["discipline"] != source.discipline
            or record["source_url"] != source.url
        ):
            raise AdditionalReviewError("课程身份与预设官网来源不一致")
        evidence = record["evidence"]
        required_evidence = (
            {"program_name", "ielts_min"} if source.kind == "cityu"
            else {"program_name", "tuition", "intake"}
        )
        if (
            not isinstance(evidence, dict) or set(evidence) != required_evidence
            or any(not isinstance(quote, str) or not 8 <= len(quote) <= 300
                   for quote in evidence.values())
            or evidence["program_name"] != source.program_name
        ):
            raise AdditionalReviewError("官网证据字段无效")
        if source.kind == "cityu":
            score = record["ielts_min"]
            if (
                isinstance(score, bool) or not isinstance(score, (int, float))
                or not 4 <= score <= 9
                or record["tuition"] is not None or record["currency"] is not None
                or record["intake"] is not None
                or not re.search(rf"\b{score:g}\b", evidence["ielts_min"])
            ):
                raise AdditionalReviewError("港城大雅思要求或缺失字段无效")
        else:
            tuition = record["tuition"]
            fee_quote = evidence["tuition"]
            fee_match = (
                re.search(r"\bfull-time study is HK\$\s*([\d,]+)", fee_quote, re.IGNORECASE)
                or re.search(r"\bis HK\$\s*([\d,]+)\s*\(full-time study", fee_quote, re.IGNORECASE)
            )
            if (
                type(tuition) is not int or not 10_000 <= tuition <= 1_000_000
                or record["currency"] != "HKD"
                or record["intake"] != "2027 Fall"
                or record["ielts_min"] is not None
                or fee_match is None
                or int(fee_match.group(1).replace(",", "")) != tuition
                or "2027/28 Fall" not in evidence["intake"]
            ):
                raise AdditionalReviewError("港科大全日制学费或入学季无效")
        _time(record["retrieved_at"])
        if (
            not isinstance(record["source_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"])
        ):
            raise AdditionalReviewError("网页摘要无效")
    if batch["robots_urls"] != sorted({SOURCES[key].robots_url for key in seen}):
        raise AdditionalReviewError("robots.txt 来源声明不一致")
    return records


def _digest(record: dict) -> str:
    content = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def prepare_review(batch: dict) -> dict:
    records = _records(batch)
    return {
        "schema_version": 1,
        "usage": "local_non_commercial_research",
        "rights_confirmed": False,
        "rights_note": "",
        "records": [{
            "source_id": record["source_id"],
            "source_url": record["source_url"],
            "record_digest": _digest(record),
            "evidence": record["evidence"],
            "approved": False,
            "review_note": "",
        } for record in records],
    }


def _approved_records(batch: dict, review: object) -> list[dict]:
    records = _records(batch)
    if (
        not isinstance(review, dict)
        or set(review) != {
            "schema_version", "usage", "rights_confirmed", "rights_note", "records",
        }
        or review["schema_version"] != 1
        or review["usage"] != "local_non_commercial_research"
    ):
        raise AdditionalReviewError("核对清单格式或使用范围无效")
    if (
        review["rights_confirmed"] is not True
        or not isinstance(review["rights_note"], str)
        or len(review["rights_note"].strip()) < 8
        or len(review["rights_note"]) > 500
    ):
        raise AdditionalReviewError("请先人工确认拟批准课程所属官网的内容使用范围并填写说明")
    approvals = review["records"]
    if not isinstance(approvals, list) or len(approvals) != len(records):
        raise AdditionalReviewError("核对清单与采集记录数量不一致")
    by_id = {}
    for entry in approvals:
        if (
            not isinstance(entry, dict)
            or set(entry) != {
                "source_id", "source_url", "record_digest", "evidence",
                "approved", "review_note",
            }
            or not isinstance(entry["source_id"], str)
            or entry["source_id"] in by_id
        ):
            raise AdditionalReviewError("核对清单记录无效")
        by_id[entry["source_id"]] = entry
    selected = []
    for record in records:
        entry = by_id.get(record["source_id"])
        if (
            entry is None or entry["source_url"] != record["source_url"]
            or entry["record_digest"] != _digest(record)
            or entry["evidence"] != record["evidence"]
        ):
            raise AdditionalReviewError("采集快照或证据已变化，请重新核对")
        if type(entry["approved"]) is not bool:
            raise AdditionalReviewError("批准字段无效")
        note = entry["review_note"]
        if not isinstance(note, str) or len(note) > 500:
            raise AdditionalReviewError("核对说明无效")
        if entry["approved"]:
            if len(note.strip()) < 8:
                raise AdditionalReviewError("每条批准的课程都需要核对说明")
            selected.append(record)
    if not selected:
        raise AdditionalReviewError("没有批准任何课程")
    return selected


def validate_review(batch: dict, review: dict) -> int:
    return len(_approved_records(batch, review))


def import_reviewed(session: Session, batch: dict, review: dict) -> dict[str, int]:
    selected = _approved_records(batch, review)
    counts = {
        "imported": 0, "updated": 0, "unchanged": 0,
        "skipped": len(batch["records"]) - len(selected),
    }
    try:
        for record in selected:
            university = session.scalar(select(University).where(
                University.name == record["university"],
                University.country == record["country"],
            ))
            if university is None:
                university = University(name=record["university"], country="香港")
                session.add(university)
                session.flush()
            program = session.scalar(select(Program).where(Program.source_id == record["source_id"]))
            if program is not None and program.data_origin != "official_reviewed":
                raise AdditionalReviewError("同来源 ID 已被非官网数据使用，停止覆盖")
            if program is not None and program.source_sha256 == record["source_sha256"]:
                counts["unchanged"] += 1
                continue
            if program is None:
                program = Program(university_id=university.id)
                session.add(program)
                counts["imported"] += 1
            else:
                counts["updated"] += 1
            source = SOURCES[record["source_id"]]
            program.university_record = university
            program.university = university.name
            program.country = "香港"
            program.city = "香港"
            program.program_name = source.program_name
            program.degree = "硕士"
            program.discipline = source.discipline
            program.duration = None
            program.tuition = record["tuition"]
            program.currency = record["currency"]
            program.intake = record["intake"]
            program.deadline = None
            program.gpa_min = None
            program.ielts_min = record["ielts_min"]
            program.toefl_min = None
            program.gre_required = None
            program.website = source.url
            program.source_url = source.url
            program.description = (
                "官网招生要求摘要：雅思总分适用于需要提交英语成绩的申请人；"
                "学历、专业背景和其他条件请核对官网。"
                if source.kind == "cityu" else
                "官网课程摘要：所示金额为 2027/28 入学季全日制课程学费；"
                "语言、学历和申请轮次请核对官网。"
            )
            program.tags = None
            program.data_origin = "official_reviewed"
            program.source_id = source.key
            program.source_sha256 = record["source_sha256"]
            program.source_retrieved_at = _time(record["retrieved_at"])
            program.reviewed_at = datetime.now(timezone.utc)
            program.updated_at = program.source_retrieved_at
            program.academic_standard = None
            program.language_details_json = None
        session.commit()
    except Exception:
        session.rollback()
        raise
    return counts
