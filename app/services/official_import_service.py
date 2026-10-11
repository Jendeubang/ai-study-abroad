"""Validate a fixed official snapshot and import only explicitly reviewed records."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Program, University
from scripts.crawl_official_requirements import ROBOTS_URL, SOURCES, TERMS_URL


class ReviewError(ValueError):
    """The crawl or approval file cannot authorize an import."""


SOURCE_DETAILS = {
    "cambridge-acs": "计算机科学",
    "cambridge-scientific-computing": "科学计算",
}
RECORD_FIELDS = {
    "source_id", "university", "program_name", "degree", "academic_standard",
    "ielts", "toefl", "subject_requirement_needs_review", "source_url",
    "retrieved_at", "source_sha256",
}
SCORE_FIELDS = {"overall", "listening", "writing", "reading", "speaking"}


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReviewError("官网采集时间无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReviewError("官网采集时间无效") from exc
    if parsed.tzinfo is None:
        raise ReviewError("官网采集时间缺少时区")
    now = datetime.now(timezone.utc)
    if parsed > now + timedelta(minutes=5) or parsed < now - timedelta(days=180):
        raise ReviewError("官网采集快照过期或时间无效，请重新采集")
    return parsed.astimezone(timezone.utc)


def _scores(values: object, maximum: float, label: str) -> None:
    if not isinstance(values, dict) or set(values) != SCORE_FIELDS:
        raise ReviewError(f"{label}要求不完整")
    if any(
        isinstance(score, bool) or not isinstance(score, (int, float))
        or not 0 < score <= maximum
        for score in values.values()
    ):
        raise ReviewError(f"{label}分数无效")


def _checked_records(batch: object) -> list[dict]:
    if not isinstance(batch, dict) or set(batch) != {
        "schema_version", "manual_review_required", "usage", "robots_url", "terms_url", "records",
    }:
        raise ReviewError("官网采集文件格式无效")
    if (batch["schema_version"] != 1 or batch["manual_review_required"] is not True
            or batch["usage"] != "local_non_commercial_research"
            or batch["robots_url"] != ROBOTS_URL or batch["terms_url"] != TERMS_URL):
        raise ReviewError("官网采集文件的使用范围或来源声明无效")
    records = batch["records"]
    if not isinstance(records, list) or not 1 <= len(records) <= len(SOURCES):
        raise ReviewError("官网采集记录数量无效")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != RECORD_FIELDS:
            raise ReviewError("官网采集记录字段无效")
        key = record["source_id"]
        source = SOURCES.get(key) if isinstance(key, str) else None
        if source is None or key in seen:
            raise ReviewError("官网来源不在已核准的课程名单内")
        seen.add(key)
        if (record["source_url"] != source.url
                or record["program_name"] != source.program_name
                or record["university"] != "University of Cambridge"
                or record["degree"] != "MPhil"):
            raise ReviewError("官网来源和课程信息与预设页面不一致")
        if record["academic_standard"] not in {
            "First class Honours Degree", "High II.i Honours Degree",
        } or record["subject_requirement_needs_review"] is not True:
            raise ReviewError("学历要求或专业背景核对标记无效")
        _scores(record["ielts"], 9, "雅思")
        _scores(record["toefl"], 120, "托福")
        _time(record["retrieved_at"])
        if not isinstance(record["source_sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", record["source_sha256"],
        ):
            raise ReviewError("官网页面哈希无效")
    return records


def _digest(record: dict) -> str:
    content = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def prepare_review(batch: dict) -> dict:
    """Create a denied-by-default review file tied to the exact snapshot."""
    records = _checked_records(batch)
    return {
        "schema_version": 1,
        "rights_confirmed": False,
        "usage": "local_non_commercial_research",
        "terms_url": TERMS_URL,
        "records": [{
            "source_id": record["source_id"],
            "source_url": record["source_url"],
            "record_digest": _digest(record),
            "approved": False,
            "review_note": "",
        } for record in records],
    }


def _approved_records(batch: dict, review: object) -> list[dict]:
    records = _checked_records(batch)
    if not isinstance(review, dict) or set(review) != {
        "schema_version", "rights_confirmed", "usage", "terms_url", "records",
    } or review["schema_version"] != 1 or review["usage"] != "local_non_commercial_research" \
            or review["terms_url"] != TERMS_URL:
        raise ReviewError("核对清单格式或使用范围无效")
    if review["rights_confirmed"] is not True:
        raise ReviewError("请先确认官网条款和本地非商业使用范围")
    approvals = review["records"]
    if not isinstance(approvals, list) or len(approvals) != len(records):
        raise ReviewError("核对清单与采集快照不一致")
    by_id = {}
    for entry in approvals:
        if not isinstance(entry, dict) or set(entry) != {
            "source_id", "source_url", "record_digest", "approved", "review_note",
        } or not isinstance(entry["source_id"], str) or entry["source_id"] in by_id:
            raise ReviewError("核对清单记录无效")
        by_id[entry["source_id"]] = entry
    selected = []
    for record in records:
        entry = by_id.get(record["source_id"])
        if (entry is None or entry["source_url"] != record["source_url"]
                or entry["record_digest"] != _digest(record)):
            raise ReviewError("采集快照已变更，请重新生成并核对清单")
        if type(entry["approved"]) is not bool:
            raise ReviewError("核对清单批准字段无效")
        note = entry["review_note"]
        if not isinstance(note, str) or len(note) > 500:
            raise ReviewError("核对说明无效")
        if entry["approved"]:
            if len(note.strip()) < 8:
                raise ReviewError("每条批准的课程都需要核对说明")
            selected.append(record)
    if not selected:
        raise ReviewError("核对清单没有批准任何课程")
    return selected


def validate_review(batch: dict, review: dict) -> int:
    """Check approval and snapshot binding without touching the database."""
    return len(_approved_records(batch, review))


def import_reviewed_snapshot(session: Session, batch: dict, review: dict) -> dict[str, int]:
    """Upsert approved facts. A replay of the same page hash is a no-op."""
    selected = _approved_records(batch, review)
    counts = {"imported": 0, "updated": 0, "unchanged": 0, "skipped": len(batch["records"]) - len(selected)}
    try:
        university = session.scalar(select(University).where(
            University.name == "University of Cambridge", University.country == "英国",
        ))
        if university is None:
            university = University(name="University of Cambridge", country="英国", ranking=None)
            session.add(university)
            session.flush()
        for record in selected:
            program = session.scalar(select(Program).where(Program.source_id == record["source_id"]))
            if program is not None and program.data_origin != "official_reviewed":
                raise ReviewError("已有同来源的非官网项目，停止覆盖")
            if program is not None and program.source_sha256 == record["source_sha256"]:
                counts["unchanged"] += 1
                continue
            if program is None:
                program = Program(university_id=university.id)
                session.add(program)
                counts["imported"] += 1
            else:
                counts["updated"] += 1
            program.university = university.name
            program.university_record = university
            program.country = "英国"
            program.city = "剑桥"
            program.program_name = record["program_name"]
            program.degree = "硕士"
            program.discipline = SOURCE_DETAILS[record["source_id"]]
            program.duration = None
            program.tuition = None
            program.currency = None
            program.intake = None
            program.deadline = None
            program.gpa_min = None
            program.ielts_min = record["ielts"]["overall"]
            program.toefl_min = record["toefl"]["overall"]
            program.gre_required = None
            program.website = record["source_url"]
            program.source_url = record["source_url"]
            program.description = "官网招生要求摘要。专业背景及其他条件请打开官网核对。"
            program.tags = None
            program.data_origin = "official_reviewed"
            program.source_id = record["source_id"]
            program.source_sha256 = record["source_sha256"]
            program.source_retrieved_at = _time(record["retrieved_at"])
            program.reviewed_at = datetime.now(timezone.utc)
            program.updated_at = program.source_retrieved_at
            program.academic_standard = record["academic_standard"]
            program.language_details_json = json.dumps({
                "ielts": record["ielts"], "toefl": record["toefl"],
            }, ensure_ascii=False, sort_keys=True)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return counts
