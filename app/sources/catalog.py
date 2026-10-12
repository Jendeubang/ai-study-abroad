"""Allowlisted official programme pages and source permission checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SourceDefinition, SourcePermission


RIGHTS_REVIEW_DAYS = 365


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    programme_key: str
    host: str
    url: str
    university: str
    country: str
    city: str | None
    program_name: str
    degree: str
    discipline: str
    parser_key: str
    allowed_fields: tuple[str, ...]
    enabled: bool


class SourceCatalogError(ValueError):
    """A source cannot be changed under the current catalogue or rights state."""


def source_specs() -> tuple[SourceSpec, ...]:
    """Return fixed source pages. New candidate pages are disabled by default."""
    rows = (
        # Existing six Hong Kong pages.
        ("cityu-cs", "cityu-cs", "www.cs.cityu.edu.hk", "https://www.cs.cityu.edu.hk/academic-programmes/msc-computer-science/admissions", "City University of Hong Kong", "香港", "香港", "Master of Science in Computer Science", "硕士", "计算机科学", "cityu", ("ielts_min",), True),
        ("cityu-ai", "cityu-ai", "www.cs.cityu.edu.hk", "https://www.cs.cityu.edu.hk/en/academic-programmes/msc-artificial-intelligence/admissions", "City University of Hong Kong", "香港", "香港", "Master of Science in Artificial Intelligence", "硕士", "人工智能", "cityu", ("ielts_min",), True),
        ("cityu-cybersecurity", "cityu-cybersecurity", "www.cs.cityu.edu.hk", "https://www.cs.cityu.edu.hk/en/academic-programmes/msc-cybersecurity/admissions", "City University of Hong Kong", "香港", "香港", "Master of Science in Cybersecurity", "硕士", "网络安全", "cityu", ("ielts_min",), True),
        ("hkust-it", "hkust-it", "seng.hkust.edu.hk", "https://seng.hkust.edu.hk/academics/taught-postgraduate/msc-it", "The Hong Kong University of Science and Technology", "香港", "香港", "MSc in Information Technology", "硕士", "信息技术", "hkust", ("tuition", "intake"), True),
        ("hkust-bdt", "hkust-bdt", "seng.hkust.edu.hk", "https://seng.hkust.edu.hk/academics/taught-postgraduate/msc-bdt", "The Hong Kong University of Science and Technology", "香港", "香港", "MSc in Big Data Technology", "硕士", "数据科学", "hkust", ("tuition", "intake"), True),
        ("hkust-ai", "hkust-ai", "seng.hkust.edu.hk", "https://seng.hkust.edu.hk/academics/taught-postgraduate/msc-ai", "The Hong Kong University of Science and Technology", "香港", "香港", "MSc in Artificial Intelligence", "硕士", "人工智能", "hkust", ("tuition", "intake"), True),
        # Existing two Cambridge pages.
        ("cambridge-acs", "cambridge-acs", "www.postgraduate.study.cam.ac.uk", "https://www.postgraduate.study.cam.ac.uk/courses/directory/cscsmpacs/requirements", "University of Cambridge", "英国", "剑桥", "MPhil in Advanced Computer Science", "研究型硕士", "计算机科学", "cambridge", ("academic_standard", "language_details"), True),
        ("cambridge-scientific-computing", "cambridge-scientific-computing", "www.postgraduate.study.cam.ac.uk", "https://www.postgraduate.study.cam.ac.uk/courses/directory/pcphmpscm/requirements", "University of Cambridge", "英国", "剑桥", "MPhil in Scientific Computing", "研究型硕士", "科学计算", "cambridge", ("academic_standard", "language_details"), True),
        # Candidate pages. These remain disabled until permissions are reviewed.
        ("hku-msccs-application", "hku-msccs", "master.cds.hku.hk", "https://master.cds.hku.hk/msccs/about/msc-in-computer-science-application-procedure-2027-28/", "The University of Hong Kong", "香港", "香港", "MSc in Computer Science", "硕士", "计算机科学", "hku", ("intake", "application_rounds"), False),
        ("hku-msccs-details", "hku-msccs", "master.cds.hku.hk", "https://master.cds.hku.hk/msccs/admission/application-details/", "The University of Hong Kong", "香港", "香港", "MSc in Computer Science", "硕士", "计算机科学", "hku", ("tuition", "ielts_min", "toefl_min", "academic_standard"), False),
        ("cuhk-msc-cs-requirements", "cuhk-msc-cs", "msc.cse.cuhk.edu.hk", "https://msc.cse.cuhk.edu.hk/?lang=en&page_id=136", "The Chinese University of Hong Kong", "香港", "香港", "MSc in Computer Science", "硕士", "计算机科学", "cuhk", ("ielts_min", "toefl_min", "academic_standard"), False),
        ("oxford-msc-advanced-cs", "oxford-msc-advanced-cs", "www.ox.ac.uk", "https://www.ox.ac.uk/admissions/graduate/courses/msc-advanced-computer-science", "University of Oxford", "英国", "牛津", "MSc in Advanced Computer Science", "硕士", "计算机科学", "oxford", ("duration", "tuition", "intake", "ielts_min", "toefl_min", "academic_standard"), False),
        ("ucl-computer-science-msc", "ucl-computer-science-msc", "www.ucl.ac.uk", "https://www.ucl.ac.uk/study/prospective-students/graduate/courses/computer-science-msc", "University College London", "英国", "伦敦", "Computer Science MSc", "硕士", "计算机科学", "ucl", ("duration", "tuition", "intake", "ielts_min", "toefl_min", "academic_standard"), False),
    )
    specs = tuple(SourceSpec(*row) for row in rows)
    ids: set[str] = set()
    urls: set[str] = set()
    for source in specs:
        parsed = urlsplit(source.url)
        if (
            source.source_id in ids or source.url in urls or parsed.scheme != "https"
            or parsed.hostname != source.host or parsed.username or parsed.password
            or not source.allowed_fields
        ):
            raise ValueError(f"来源目录定义无效：{source.source_id}")
        ids.add(source.source_id)
        urls.add(source.url)
    return specs


def seed_source_catalog(session: Session) -> int:
    """Insert missing allowlist definitions without changing reviewed state."""
    inserted = 0
    for spec in source_specs():
        existing = session.get(SourceDefinition, spec.source_id)
        if existing is not None:
            continue
        session.add(SourceDefinition(
            source_id=spec.source_id, programme_key=spec.programme_key,
            host=spec.host, url=spec.url, university=spec.university,
            country=spec.country, city=spec.city, program_name=spec.program_name,
            degree=spec.degree, discipline=spec.discipline,
            parser_key=spec.parser_key,
            allowed_fields_json=json.dumps(spec.allowed_fields, ensure_ascii=False),
            enabled=spec.enabled,
        ))
        inserted += 1
    session.flush()
    return inserted


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _host_permission_allows(
    session: Session, host: str, *, purpose: str, now: datetime | None,
) -> bool:
    if purpose not in {"local_non_commercial_research", "public_display"}:
        return False
    permission = session.scalar(select(SourcePermission).where(SourcePermission.host == host))
    if permission is None or permission.use_scope != purpose:
        return False
    return _utc(now or datetime.now(timezone.utc)) < _utc(permission.checked_at) + timedelta(days=RIGHTS_REVIEW_DAYS)


def permission_expiry(permission: SourcePermission) -> datetime:
    return _utc(permission.checked_at) + timedelta(days=RIGHTS_REVIEW_DAYS)


def permission_allows(
    session: Session, source: SourceDefinition | SourceSpec, *,
    purpose: str, now: datetime | None = None,
) -> bool:
    definition = session.get(SourceDefinition, source.source_id)
    if definition is None or not definition.enabled:
        return False
    return _host_permission_allows(session, definition.host, purpose=purpose, now=now)


def set_source_enabled(
    session: Session, source_id: str, *, enabled: bool,
    now: datetime | None = None,
) -> SourceDefinition:
    source = session.get(SourceDefinition, source_id)
    if source is None:
        raise SourceCatalogError("来源目录中不存在该页面")
    if enabled and not any(
        _host_permission_allows(session, source.host, purpose=purpose, now=now)
        for purpose in ("local_non_commercial_research", "public_display")
    ):
        raise SourceCatalogError("来源未核实、权限已过期或未授权采集")
    source.enabled = enabled
    session.commit()
    session.refresh(source)
    return source
