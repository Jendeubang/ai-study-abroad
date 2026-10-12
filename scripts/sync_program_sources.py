"""Permission-gated weekly source synchronizer; all updates go to review."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import make_engine, make_session_factory, migrate_database
from app.models import (
    EvidenceFact, Program, ProgramCandidate, ReviewCandidate, SourceCheck,
    SourceDefinition, SyncRun, University,
)
from app.services.data_review_service import _candidate, _json
from app.sources.catalog import permission_allows, source_specs
from app.sources.parsers import ParsedSourceRecord, SourceParseError, parse_source_html


USER_AGENT = "StudyCompassAI/0.2 (+https://github.com/yutianfanxing/study-abroad-research)"
MAX_ROBOTS_BYTES = 256_000
MAX_PAGE_BYTES = 2_000_000
MIN_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT = httpx.Timeout(15.0, connect=8.0)
logger = logging.getLogger("study_abroad.source_sync")


class SourceSyncError(RuntimeError):
    """A source cannot be collected safely or parsed deterministically."""


@dataclass
class SyncSummary:
    run_id: str
    eligible: int = 0
    checked: int = 0
    changed: int = 0
    failed: int = 0
    skipped: int = 0
    unchanged: int = 0
    pending: int = 0
    source_digests: dict[str, str] = field(default_factory=dict)
    statuses: list[dict] = field(default_factory=list)


def _json_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pace(pause: Callable[[float], None], seconds: float, counter: list[int]) -> None:
    if counter[0]:
        pause(max(MIN_DELAY_SECONDS, seconds))


def _request_bytes(
    client: httpx.Client, url: str, *, host: str, content_type: str,
    limit: int, pause: Callable[[float], None], counter: list[int], delay: float,
) -> bytes:
    _pace(pause, delay, counter)
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.password:
        raise SourceSyncError("请求 URL 不符合登记的 HTTPS 主机")
    try:
        with client.stream(
            "GET", url, headers={"User-Agent": USER_AGENT, "Accept": content_type},
            follow_redirects=False, timeout=REQUEST_TIMEOUT,
        ) as response:
            counter[0] += 1
            if response.is_redirect or response.status_code != 200:
                raise SourceSyncError(f"官网请求返回 HTTP {response.status_code}，停止该来源")
            actual = urlsplit(str(response.url))
            if actual.scheme != "https" or actual.hostname != host or str(response.url) != url:
                raise SourceSyncError("官网请求发生跳转或 URL 偏离来源白名单")
            if not response.headers.get("content-type", "").lower().startswith(content_type):
                raise SourceSyncError("官网返回了非预期内容类型")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > limit:
                    raise SourceSyncError("官网响应超过大小限制")
            return bytes(body)
    except httpx.HTTPError as exc:
        raise SourceSyncError(f"官网网络请求失败：{type(exc).__name__}") from exc


def _robots_policy(
    client: httpx.Client, host: str, *, pause: Callable[[float], None],
    counter: list[int], last_delay: float,
) -> tuple[RobotFileParser, float]:
    url = f"https://{host}/robots.txt"
    raw = _request_bytes(
        client, url, host=host, content_type="text/plain", limit=MAX_ROBOTS_BYTES,
        pause=pause, counter=counter, delay=last_delay,
    )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceSyncError("robots.txt 编码无法确认") from exc
    if not re.search(r"(?im)^\s*user-agent\s*:", text):
        raise SourceSyncError("robots.txt 缺少可识别规则")
    policy = RobotFileParser(url)
    policy.parse(text.splitlines())
    rate = policy.request_rate(USER_AGENT)
    delay = max(
        MIN_DELAY_SECONDS, float(policy.crawl_delay(USER_AGENT) or 0),
        float(rate.seconds / rate.requests) if rate and rate.requests else 0,
    )
    return policy, delay


def _program_for_source(session: Session, source: SourceDefinition, programme_keys: dict[str, str]) -> Program | None:
    peer_ids = [key for key, value in programme_keys.items() if value == source.programme_key]
    return session.scalar(select(Program).where(
        Program.source_id.in_(peer_ids), Program.data_origin == "official_reviewed",
    ).limit(1)) if peer_ids else None


def _fact_payload(record: ParsedSourceRecord, digest: str) -> list[dict]:
    return [
        {
            "source_id": record.source_id, "field_name": fact.field_name,
            "value": fact.value, "scope": fact.scope,
            "evidence_excerpt": fact.evidence_excerpt,
            "source_url": fact.source_url, "source_sha256": digest,
        }
        for fact in record.facts
    ]


def _initial_candidate(
    session: Session, record: ParsedSourceRecord, *, digest: str,
    retrieved_at: datetime,
) -> tuple[ProgramCandidate | None, bool]:
    current = session.scalar(select(ProgramCandidate).where(
        ProgramCandidate.programme_key == record.programme_key,
        ProgramCandidate.status == "pending",
    ).order_by(ProgramCandidate.id.desc()).limit(1))
    source_snapshots = current.source_snapshots if current else {}
    if current and source_snapshots.get(record.source_id) == digest:
        return current, False
    source_ids = set(current.source_ids if current else [])
    source_ids.add(record.source_id)
    source_snapshots[record.source_id] = digest
    facts = current.facts if current else []
    facts = [item for item in facts if item.get("source_id") != record.source_id]
    facts.extend(_fact_payload(record, digest))
    metadata = {
        "university": record.university, "country": record.country,
        "city": record.city, "program_name": record.program_name,
        "degree": record.degree, "discipline": record.discipline,
    }
    snapshot_sha256 = _json_hash(source_snapshots)
    fingerprint = _json_hash({"programme_key": record.programme_key, "snapshot": snapshot_sha256})
    if current is None:
        candidate = ProgramCandidate(
            programme_key=record.programme_key,
            source_ids_json=json.dumps(sorted(source_ids), ensure_ascii=False),
            source_snapshots_json=json.dumps(source_snapshots, ensure_ascii=False, sort_keys=True),
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            facts_json=json.dumps(facts, ensure_ascii=False, sort_keys=True),
            snapshot_sha256=snapshot_sha256, fingerprint=fingerprint,
            retrieved_at=retrieved_at, status="pending",
        )
        session.add(candidate)
    else:
        current.source_ids_json = json.dumps(sorted(source_ids), ensure_ascii=False)
        current.source_snapshots_json = json.dumps(source_snapshots, ensure_ascii=False, sort_keys=True)
        current.metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        current.facts_json = json.dumps(facts, ensure_ascii=False, sort_keys=True)
        current.snapshot_sha256 = snapshot_sha256
        current.fingerprint = fingerprint
        current.retrieved_at = retrieved_at
        candidate = current
    session.flush()
    return candidate, True


def _latest_digest(session: Session, source: SourceDefinition, program: Program) -> str | None:
    check = session.scalar(select(SourceCheck).where(
        SourceCheck.source_id == source.source_id,
        SourceCheck.status.in_(("unchanged", "changed")),
        SourceCheck.source_sha256.is_not(None),
    ).order_by(SourceCheck.checked_at.desc(), SourceCheck.id.desc()).limit(1))
    if check is not None:
        return check.source_sha256
    if program.source_id == source.source_id:
        return program.source_sha256
    return None


def _stage_existing_changes(
    session: Session, source: SourceDefinition, program: Program,
    record: ParsedSourceRecord, digest: str, retrieved_at: datetime,
) -> set[int]:
    staged: set[int] = set()
    for fact in record.facts:
        scope_json = _json(fact.scope)
        prior = session.scalar(select(EvidenceFact).where(
            EvidenceFact.program_id == program.id,
            EvidenceFact.field_name == fact.field_name,
            EvidenceFact.scope_json == scope_json,
            EvidenceFact.active.is_(True),
        ).order_by(EvidenceFact.id.desc()).limit(1))
        old_value = prior.value if prior else None
        if old_value == fact.value:
            continue
        candidate = _candidate(
            session, program, kind="fact", field_name=fact.field_name,
            scope=fact.scope, new_value=fact.value, old_value=old_value,
            excerpt=fact.evidence_excerpt[:500], source_url=fact.source_url,
            source_sha256=digest, retrieved_at=retrieved_at,
        )
        if candidate is not None:
            staged.add(candidate.id)
    previous_digest = _latest_digest(session, source, program)
    if not staged and previous_digest != digest:
        candidate = _candidate(
            session, program, kind="page", field_name=None, scope={},
            new_value={"digest": digest},
            old_value={"digest": previous_digest} if previous_digest else None,
            excerpt=f"官网页面摘要发生变化：{record.title}"[:500],
            source_url=source.url, source_sha256=digest,
            retrieved_at=retrieved_at,
        )
        if candidate is not None:
            staged.add(candidate.id)
    return staged


def _record_check(
    session: Session, source: SourceDefinition, run: SyncRun, *,
    program: Program | None, now: datetime, status: str,
    digest: str | None = None, message: str | None = None,
) -> None:
    session.add(SourceCheck(
        source_id=source.source_id, program_id=program.id if program else None,
        source_url=source.url, source_sha256=digest, checked_at=now,
        status=status, message=message[:300] if message else None,
        sync_run_id=run.id,
    ))


def sync_sources(
    session: Session, client: httpx.Client, specs: Iterable[SourceDefinition] | None = None,
    *, now: datetime | None = None, pause: Callable[[float], None] = time.sleep,
    purpose: str = "local_non_commercial_research", trigger: str = "manual",
) -> SyncSummary:
    observed = now or datetime.now(timezone.utc)
    observed = observed.replace(tzinfo=timezone.utc) if observed.tzinfo is None else observed.astimezone(timezone.utc)
    run_key = str(uuid4())
    run = SyncRun(run_id=run_key, trigger=trigger, started_at=observed, source_statuses_json="[]")
    session.add(run)
    session.commit()
    session.refresh(run)
    summary = SyncSummary(run_id=run_key)
    pending_keys: set[tuple[str, int]] = set()
    definitions = list(specs) if specs is not None else list(session.scalars(
        select(SourceDefinition).order_by(SourceDefinition.country, SourceDefinition.university, SourceDefinition.source_id)
    ))
    catalogue = {item.source_id: item for item in source_specs()}
    programme_keys = {item.source_id: item.programme_key for item in source_specs()}
    robot_cache: dict[str, tuple[RobotFileParser, float]] = {}
    request_count = [0]
    last_delay = MIN_DELAY_SECONDS

    for source in definitions:
        source = session.get(SourceDefinition, source.source_id)
        if source is None:
            summary.failed += 1
            summary.statuses.append({"source_id": "unknown", "status": "failed", "message": "来源目录中不存在"})
            continue
        program = _program_for_source(session, source, programme_keys)
        if not permission_allows(session, source, purpose=purpose, now=observed):
            summary.skipped += 1
            _record_check(session, source, run, program=program, now=observed, status="skipped_permission", message="来源停用、未核实、过期或用途不匹配")
            summary.statuses.append({"source_id": source.source_id, "status": "skipped_permission"})
            run.skipped_count = summary.skipped
            run.source_statuses_json = json.dumps(summary.statuses, ensure_ascii=False)
            session.commit()
            continue
        summary.eligible += 1
        summary.checked += 1
        try:
            spec = catalogue.get(source.source_id)
            if (
                spec is None or source.url != spec.url or source.host != spec.host
                or source.parser_key != spec.parser_key or source.allowed_fields != spec.allowed_fields
                or not source.enabled
            ):
                raise SourceSyncError("来源目录与固定解析器不一致，停止请求")
            if source.host not in robot_cache:
                policy, last_delay = _robots_policy(
                    client, source.host, pause=pause, counter=request_count,
                    last_delay=last_delay,
                )
                robot_cache[source.host] = (policy, last_delay)
            policy, last_delay = robot_cache[source.host]
            if not policy.can_fetch(USER_AGENT, source.url):
                raise SourceSyncError("robots.txt 不允许采集该页面")
            raw = _request_bytes(
                client, source.url, host=source.host, content_type="text/html",
                limit=MAX_PAGE_BYTES, pause=pause, counter=request_count,
                delay=last_delay,
            )
            try:
                html = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise SourceSyncError("官网页面编码无法确认") from exc
            digest = hashlib.sha256(raw).hexdigest()
            summary.source_digests[source.source_id] = digest
            record = parse_source_html(spec, html)
            retrieved_at = observed
            if program is None:
                if not record.facts:
                    raise SourceParseError("页面没有可核实的招生事实，不创建空项目候选")
                candidate, staged = _initial_candidate(session, record, digest=digest, retrieved_at=retrieved_at)
                added = 1 if staged else 0
                if staged and candidate is not None:
                    pending_keys.add(("program", candidate.id))
            else:
                staged = _stage_existing_changes(session, source, program, record, digest, retrieved_at)
                added = len(staged)
                pending_keys.update(("change", item) for item in staged)
            status = "changed" if added else "unchanged"
            summary.pending = len(pending_keys)
            if added:
                summary.changed += 1
            else:
                summary.unchanged += 1
            _record_check(session, source, run, program=program, now=observed, status=status, digest=digest)
            summary.statuses.append({
                "source_id": source.source_id, "status": status,
                "source_sha256": digest, "pending": added,
            })
        except Exception as exc:
            session.rollback()
            run = session.get(SyncRun, run.id)
            source = session.get(SourceDefinition, source.source_id)
            program = _program_for_source(session, source, programme_keys) if source else None
            summary.failed += 1
            message = str(exc) if isinstance(exc, SourceSyncError | SourceParseError) else f"采集失败：{type(exc).__name__}"
            if source:
                _record_check(session, source, run, program=program, now=observed, status="failed", message=message)
                summary.statuses.append({"source_id": source.source_id, "status": "failed", "message": message[:120]})
        run.eligible_count = summary.eligible
        run.checked_count = summary.checked
        run.changed_count = summary.changed
        run.failed_count = summary.failed
        run.skipped_count = summary.skipped
        run.pending_count = summary.pending
        run.source_statuses_json = json.dumps(summary.statuses, ensure_ascii=False)
        session.commit()

    run = session.get(SyncRun, run.id)
    run.finished_at = observed
    run.eligible_count = summary.eligible
    run.checked_count = summary.checked
    run.changed_count = summary.changed
    run.failed_count = summary.failed
    run.skipped_count = summary.skipped
    run.pending_count = summary.pending
    run.source_statuses_json = json.dumps(summary.statuses, ensure_ascii=False)
    session.commit()
    return summary


def _printable_summary(summary: SyncSummary) -> dict:
    return {
        "run_id": summary.run_id, "eligible": summary.eligible,
        "checked": summary.checked, "changed": summary.changed,
        "unchanged": summary.unchanged, "failed": summary.failed,
        "skipped_permission": summary.skipped, "pending_review": summary.pending,
        "source_ids": [item["source_id"] for item in summary.statuses],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按权限目录检查固定官网招生页面；所有更新只进入人工复核")
    parser.add_argument("--all", action="store_true", help="检查目录中的所有已启用来源")
    parser.add_argument("--source", action="append", dest="source_ids", help="只检查指定来源 ID，可重复使用")
    parser.add_argument("--purpose", choices=("local_non_commercial_research", "public_display"), default="local_non_commercial_research")
    parser.add_argument("--trigger", choices=("manual", "scheduler"), default="manual")
    parser.add_argument("--proxy", help="可选本机代理；仅接受 loopback 代理地址")
    args = parser.parse_args(argv)
    if not args.all and not args.source_ids:
        parser.error("请指定 --all 或至少一个 --source")
    proxy = None
    if args.proxy:
        from scripts.crawl_official_requirements import _loopback_proxy
        try:
            proxy = _loopback_proxy(args.proxy)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
    migrate_database(settings.database_url)
    engine = make_engine(settings.database_url)
    SessionFactory = make_session_factory(engine)
    try:
        with SessionFactory() as session:
            all_sources = list(session.scalars(select(SourceDefinition).order_by(SourceDefinition.source_id)))
            if args.all:
                selected = all_sources
            else:
                wanted = set(args.source_ids)
                selected = [source for source in all_sources if source.source_id in wanted]
                missing = wanted - {source.source_id for source in selected}
                if missing:
                    parser.error("来源目录中不存在 ID：" + ", ".join(sorted(missing)))
            with httpx.Client(
                proxy=proxy,
                trust_env=False, follow_redirects=False,
                timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT},
            ) as client:
                summary = sync_sources(
                    session, client, selected, purpose=args.purpose, trigger=args.trigger,
                )
        print(json.dumps(_printable_summary(summary), ensure_ascii=False, sort_keys=True))
        return 1 if summary.failed else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
