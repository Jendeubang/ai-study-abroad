"""Stage official facts for human review; publish only approved evidence."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ApplicationRound, EvidenceFact, Program, ReviewCandidate, ReviewDecision, SourceDefinition,
    SourceCheck, SourcePermission,
)
from app.sources.catalog import seed_source_catalog
from app.services.additional_import_service import AdditionalReviewError, _approved_records, _records, _time
from scripts.crawl_additional_programs import SOURCES


class ReviewWorkflowError(ValueError):
    """A candidate cannot enter or leave the review queue safely."""


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _program_for_source(session: Session, source_id: str) -> Program:
    program = session.scalar(select(Program).where(Program.source_id == source_id))
    source = SOURCES.get(source_id)
    if program is None or source is None or program.data_origin != "official_reviewed" or program.source_url != source.url:
        raise ReviewWorkflowError("只能核对已导入的预设官网项目")
    return program


def _facts(record: dict) -> list[tuple[str, dict, dict, str]]:
    source = SOURCES[record["source_id"]]
    if source.kind == "cityu":
        return [(
            "ielts_min", {"value": record["ielts_min"]},
            {"applies_when": "english_proficiency_proof_required"},
            record["evidence"]["ielts_min"],
        )]
    return [
        (
            "tuition", {"amount": record["tuition"], "currency": "HKD"},
            {"academic_year": "2027/28", "intake": "2027 Fall", "study_mode": "full_time"},
            record["evidence"]["tuition"],
        ),
        (
            "intake", {"value": record["intake"]},
            {"academic_year": "2027/28"}, record["evidence"]["intake"],
        ),
    ]


def active_evidence(session: Session, program_id: int) -> list[EvidenceFact]:
    return list(session.scalars(
        select(EvidenceFact).where(
            EvidenceFact.program_id == program_id, EvidenceFact.active.is_(True),
        ).order_by(EvidenceFact.field_name, EvidenceFact.id)
    ))


def active_rounds(session: Session, program_id: int) -> list[ApplicationRound]:
    return list(session.scalars(
        select(ApplicationRound).where(
            ApplicationRound.program_id == program_id, ApplicationRound.active.is_(True),
        ).order_by(ApplicationRound.deadline_on, ApplicationRound.id)
    ))


def backfill_additional_review(session: Session, batch: dict, review: dict) -> int:
    """Move previously approved local snapshots into evidence rows, without new approval."""
    try:
        selected = _approved_records(batch, review)
    except AdditionalReviewError as exc:
        raise ReviewWorkflowError("核对快照或批准清单无效") from exc
    added = 0
    try:
        for record in selected:
            program = _program_for_source(session, record["source_id"])
            if program.source_sha256 != record["source_sha256"]:
                raise ReviewWorkflowError("已导入项目与核对快照不一致，停止迁移证据")
            existing_check = session.scalar(select(SourceCheck.id).where(
                SourceCheck.source_id == record["source_id"],
                SourceCheck.source_sha256 == record["source_sha256"],
                SourceCheck.status == "unchanged",
            ).limit(1))
            if existing_check is None:
                session.add(SourceCheck(
                    source_id=record["source_id"], program_id=program.id,
                    source_url=record["source_url"], source_sha256=record["source_sha256"],
                    checked_at=_time(record["retrieved_at"]), status="unchanged",
                ))
            for field, value, scope, excerpt in _facts(record):
                prior = session.scalar(select(EvidenceFact).where(
                    EvidenceFact.program_id == program.id,
                    EvidenceFact.field_name == field,
                    EvidenceFact.scope_json == _json(scope),
                    EvidenceFact.active.is_(True),
                ))
                if prior is not None:
                    if prior.value_json != _json(value) or prior.source_sha256 != record["source_sha256"]:
                        raise ReviewWorkflowError("已迁入的证据与核对快照不一致")
                    continue
                session.add(EvidenceFact(
                    program_id=program.id, field_name=field, value_json=_json(value),
                    scope_json=_json(scope), evidence_excerpt=excerpt,
                    source_url=record["source_url"], source_sha256=record["source_sha256"],
                    retrieved_at=_time(record["retrieved_at"]),
                    reviewed_at=program.reviewed_at or datetime.now(timezone.utc),
                    reviewer="历史人工核对", active=True,
                ))
                added += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return added


def _candidate(
    session: Session, program: Program, *, kind: str, field_name: str | None,
    scope: dict, new_value: dict, old_value: dict | None, excerpt: str,
    source_url: str, source_sha256: str, retrieved_at: datetime,
) -> ReviewCandidate | None:
    identity = {
        "program_id": program.id, "kind": kind, "field_name": field_name,
        "scope": scope, "new_value": new_value, "excerpt": excerpt,
        "source_url": source_url, "source_sha256": source_sha256,
    }
    fingerprint = hashlib.sha256(_json(identity).encode("utf-8")).hexdigest()
    if session.scalar(select(ReviewCandidate.id).where(ReviewCandidate.fingerprint == fingerprint)) is not None:
        return None
    candidate = ReviewCandidate(
        program_id=program.id, kind=kind, field_name=field_name,
        scope_json=_json(scope), new_value_json=_json(new_value),
        old_value_json=_json(old_value) if old_value is not None else None,
        evidence_excerpt=excerpt, source_url=source_url,
        source_sha256=source_sha256, retrieved_at=retrieved_at,
        fingerprint=fingerprint, status="pending", created_at=datetime.now(timezone.utc),
    )
    session.add(candidate)
    session.flush()
    return candidate


def stage_additional_batch(session: Session, batch: dict) -> dict[str, int]:
    """Compare six-source facts and queue changes; never touch published values."""
    records = _records(batch)
    pending = 0
    try:
        for record in records:
            program = _program_for_source(session, record["source_id"])
            changed_fields = False
            for field, value, scope, excerpt in _facts(record):
                prior = session.scalar(select(EvidenceFact).where(
                    EvidenceFact.program_id == program.id,
                    EvidenceFact.field_name == field,
                    EvidenceFact.scope_json == _json(scope),
                    EvidenceFact.active.is_(True),
                ))
                old_value = prior.value if prior else None
                if old_value == value:
                    continue
                changed_fields = True
                if _candidate(
                    session, program, kind="fact", field_name=field,
                    scope=scope, new_value=value, old_value=old_value,
                    excerpt=excerpt, source_url=record["source_url"],
                    source_sha256=record["source_sha256"],
                    retrieved_at=_time(record["retrieved_at"]),
                ) is not None:
                    pending += 1
            page_changed = record["source_sha256"] != program.source_sha256
            if page_changed and not changed_fields:
                if _candidate(
                    session, program, kind="page", field_name=None,
                    scope={}, new_value={"sha256": record["source_sha256"]},
                    old_value={"sha256": program.source_sha256},
                    excerpt="官网页面摘要变化；已提取字段数值未变化，请核对页面其他内容。",
                    source_url=record["source_url"],
                    source_sha256=record["source_sha256"],
                    retrieved_at=_time(record["retrieved_at"]),
                ) is not None:
                    pending += 1
            session.add(SourceCheck(
                source_id=record["source_id"], program_id=program.id,
                source_url=record["source_url"], source_sha256=record["source_sha256"],
                checked_at=_time(record["retrieved_at"]),
                status="changed" if changed_fields or page_changed else "unchanged",
            ))
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {"checked": len(records), "pending": pending}


def record_source_failure(session: Session, source_id: str, error_type: str) -> None:
    program = _program_for_source(session, source_id)
    message = error_type if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type) else "CrawlError"
    session.add(SourceCheck(
        source_id=source_id, program_id=program.id, source_url=program.source_url,
        source_sha256=None, checked_at=datetime.now(timezone.utc),
        status="failed", message=message,
    ))
    session.commit()


def _safe_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def set_source_permission(
    session: Session, *, host: str, terms_url: str, use_scope: str,
    reviewer: str, note: str,
) -> SourcePermission:
    if use_scope not in {"unverified", "local_non_commercial_research", "public_display"}:
        raise ReviewWorkflowError("使用范围无效")
    seed_source_catalog(session)
    known_hosts = set(session.scalars(select(SourceDefinition.host).distinct()))
    normalized_host = host.strip().lower()
    if normalized_host not in known_hosts or not _safe_https_url(terms_url):
        raise ReviewWorkflowError("网站或条款链接无效")
    if not 1 <= len(reviewer.strip()) <= 100 or not 8 <= len(note.strip()) <= 500:
        raise ReviewWorkflowError("请填写核查人和至少 8 字的使用范围说明")
    policy = session.scalar(select(SourcePermission).where(SourcePermission.host == normalized_host))
    if policy is None:
        policy = SourcePermission(host=normalized_host)
        session.add(policy)
    policy.terms_url = terms_url
    policy.use_scope = use_scope
    policy.checked_at = datetime.now(timezone.utc)
    policy.reviewer = reviewer.strip()
    policy.note = note.strip()
    session.commit()
    session.refresh(policy)
    return policy


def stage_round_candidate(
    session: Session, *, program_id: int, intake: str, round_label: str,
    opens_on: date | None, deadline_on: date | None, source_url: str,
    evidence_excerpt: str, source_sha256: str, retrieved_at: datetime,
) -> ReviewCandidate:
    program = session.get(Program, program_id)
    if program is None or program.data_origin != "official_reviewed" or source_url != program.source_url:
        raise ReviewWorkflowError("申请轮次必须关联已导入的官网项目及原始来源")
    if not 1 <= len(intake.strip()) <= 100 or not 1 <= len(round_label.strip()) <= 100:
        raise ReviewWorkflowError("入学季或申请轮次无效")
    if not 8 <= len(evidence_excerpt.strip()) <= 500 or not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ReviewWorkflowError("申请轮次证据或页面摘要无效")
    if deadline_on is None and opens_on is None:
        raise ReviewWorkflowError("申请轮次至少要有一个明确日期")
    if opens_on is not None and deadline_on is not None and opens_on > deadline_on:
        raise ReviewWorkflowError("开放日期不能晚于截止日期")
    observed = _utc(retrieved_at)
    if observed > datetime.now(timezone.utc) + timedelta(minutes=5) or observed < datetime.now(timezone.utc) - timedelta(days=180):
        raise ReviewWorkflowError("申请轮次核查时间无效")
    scope = {"intake": intake.strip(), "round_label": round_label.strip()}
    value = {
        "opens_on": opens_on.isoformat() if opens_on else None,
        "deadline_on": deadline_on.isoformat() if deadline_on else None,
    }
    previous = session.scalar(select(ApplicationRound).where(
        ApplicationRound.program_id == program_id, ApplicationRound.intake == scope["intake"],
        ApplicationRound.round_label == scope["round_label"], ApplicationRound.active.is_(True),
    ))
    old = None if previous is None else {
        "opens_on": previous.opens_on.isoformat() if previous.opens_on else None,
        "deadline_on": previous.deadline_on.isoformat() if previous.deadline_on else None,
    }
    if value == old:
        raise ReviewWorkflowError("申请轮次日期没有变化")
    candidate = _candidate(
        session, program, kind="round", field_name=None, scope=scope,
        new_value=value, old_value=old, excerpt=evidence_excerpt.strip(),
        source_url=source_url, source_sha256=source_sha256, retrieved_at=observed,
    )
    if candidate is None:
        raise ReviewWorkflowError("相同申请轮次已提交核对")
    session.commit()
    session.refresh(candidate)
    return candidate


def _permission_for_candidate(session: Session, candidate: ReviewCandidate) -> None:
    host = urlsplit(candidate.source_url).hostname
    policy = session.scalar(select(SourcePermission).where(SourcePermission.host == host))
    if (
        policy is None or policy.use_scope not in {"local_non_commercial_research", "public_display"}
        or _utc(policy.checked_at) < datetime.now(timezone.utc) - timedelta(days=365)
    ):
        raise ReviewWorkflowError("请先核查并登记官网内容使用范围")


def decide_candidate(
    session: Session, candidate_id: int, *, decision: str, reviewer: str, note: str,
) -> ReviewCandidate:
    if decision not in {"approved", "rejected"}:
        raise ReviewWorkflowError("审核决定无效")
    if not 1 <= len(reviewer.strip()) <= 100 or not 8 <= len(note.strip()) <= 500:
        raise ReviewWorkflowError("请填写核查人和至少 8 字的审核说明")
    candidate = session.get(ReviewCandidate, candidate_id)
    if candidate is None or candidate.status != "pending":
        raise ReviewWorkflowError("待审核记录不存在或已经处理")
    program = session.get(Program, candidate.program_id)
    if program is None or program.data_origin != "official_reviewed" or program.source_url != candidate.source_url:
        raise ReviewWorkflowError("核对来源与已发布项目不一致")
    if _utc(candidate.retrieved_at) < datetime.now(timezone.utc) - timedelta(days=180):
        raise ReviewWorkflowError("候选证据已过期，请重新采集")
    if decision == "approved":
        _permission_for_candidate(session, candidate)
    now = datetime.now(timezone.utc)
    try:
        if decision == "approved" and candidate.kind == "fact":
            if candidate.field_name not in {"tuition", "intake", "ielts_min"}:
                raise ReviewWorkflowError("不支持的证据字段")
            previous = session.scalar(select(EvidenceFact).where(
                EvidenceFact.program_id == program.id,
                EvidenceFact.field_name == candidate.field_name,
                EvidenceFact.scope_json == candidate.scope_json,
                EvidenceFact.active.is_(True),
            ))
            old = previous.value if previous else None
            if old != candidate.old_value:
                raise ReviewWorkflowError("当前已发布值发生变化，请重新核对")
            if previous:
                previous.active = False
            session.add(EvidenceFact(
                program_id=program.id, field_name=candidate.field_name,
                value_json=candidate.new_value_json, scope_json=candidate.scope_json,
                evidence_excerpt=candidate.evidence_excerpt, source_url=candidate.source_url,
                source_sha256=candidate.source_sha256, retrieved_at=candidate.retrieved_at,
                reviewed_at=now, reviewer=reviewer.strip(), active=True,
            ))
            value = candidate.new_value
            if candidate.field_name == "ielts_min":
                program.ielts_min = float(value["value"])
            elif candidate.field_name == "intake":
                program.intake = value["value"]
            elif candidate.scope.get("intake") == program.intake and candidate.scope.get("study_mode") == "full_time":
                program.tuition = float(value["amount"])
                program.currency = value["currency"]
        elif decision == "approved" and candidate.kind == "round":
            scope = candidate.scope
            previous = session.scalar(select(ApplicationRound).where(
                ApplicationRound.program_id == program.id,
                ApplicationRound.intake == scope["intake"],
                ApplicationRound.round_label == scope["round_label"],
                ApplicationRound.active.is_(True),
            ))
            old = None if previous is None else {
                "opens_on": previous.opens_on.isoformat() if previous.opens_on else None,
                "deadline_on": previous.deadline_on.isoformat() if previous.deadline_on else None,
            }
            if old != candidate.old_value:
                raise ReviewWorkflowError("当前申请轮次发生变化，请重新核对")
            if previous:
                previous.active = False
            value = candidate.new_value
            session.add(ApplicationRound(
                program_id=program.id, intake=scope["intake"], round_label=scope["round_label"],
                opens_on=date.fromisoformat(value["opens_on"]) if value["opens_on"] else None,
                deadline_on=date.fromisoformat(value["deadline_on"]) if value["deadline_on"] else None,
                evidence_excerpt=candidate.evidence_excerpt, source_url=candidate.source_url,
                source_sha256=candidate.source_sha256, retrieved_at=candidate.retrieved_at,
                reviewed_at=now, reviewer=reviewer.strip(), active=True,
            ))
        elif decision == "approved" and candidate.kind != "page":
            raise ReviewWorkflowError("不支持的核对记录")
        candidate.status = decision
        candidate.reviewed_at = now
        candidate.reviewer = reviewer.strip()
        candidate.review_note = note.strip()
        session.add(ReviewDecision(
            candidate_id=candidate.id, decision=decision,
            reviewer=reviewer.strip(), note=note.strip(), decided_at=now,
        ))
        if decision == "approved" and candidate.kind in {"fact", "page"}:
            related = list(session.scalars(select(ReviewCandidate).where(
                ReviewCandidate.program_id == program.id,
                ReviewCandidate.source_sha256 == candidate.source_sha256,
                ReviewCandidate.kind.in_(("fact", "page")),
            )))
            if all(item.status == "approved" for item in related):
                program.source_sha256 = candidate.source_sha256
                program.source_retrieved_at = candidate.retrieved_at
                program.reviewed_at = now
                program.updated_at = candidate.retrieved_at
        session.commit()
        session.refresh(candidate)
    except Exception:
        session.rollback()
        raise
    return candidate


def approve_candidate(session: Session, candidate_id: int, *, reviewer: str, note: str) -> ReviewCandidate:
    return decide_candidate(session, candidate_id, decision="approved", reviewer=reviewer, note=note)


def review_alerts(session: Session, program: Program) -> list[str]:
    if program.data_origin != "official_reviewed":
        return []
    alerts = []
    pending = session.scalar(select(ReviewCandidate.id).where(
        ReviewCandidate.program_id == program.id, ReviewCandidate.status == "pending",
    ).limit(1))
    if pending is not None:
        alerts.append("有官网变化待复核")
    latest = session.scalar(select(SourceCheck).where(
        SourceCheck.program_id == program.id,
    ).order_by(SourceCheck.checked_at.desc(), SourceCheck.id.desc()).limit(1))
    if latest is not None and latest.status == "failed":
        alerts.append("最近一次采集失败")
    evidence = active_evidence(session, program.id)
    reviewed_dates = [item.reviewed_at for item in evidence] or ([program.reviewed_at] if program.reviewed_at else [])
    if reviewed_dates and min(_utc(value) for value in reviewed_dates) < datetime.now(timezone.utc) - timedelta(days=90):
        alerts.append("核对超过 90 天")
    return alerts
