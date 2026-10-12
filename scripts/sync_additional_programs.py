"""Recheck the fixed six approved source URLs and queue changes for review."""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import SourcePermission

from app.config import settings
from app.database import make_engine, make_session_factory, migrate_database
from app.services.data_review_service import (
    ReviewWorkflowError, record_source_failure, stage_additional_batch,
)
from scripts.crawl_additional_programs import SOURCES, Source, crawl
from scripts.crawl_official_requirements import CrawlError, USER_AGENT, _loopback_proxy


def sync_one(
    session: Session, client: httpx.Client, source: Source,
    pause: Callable[[float], None] = time.sleep,
) -> dict:
    policy = session.scalar(select(SourcePermission).where(SourcePermission.host == source.host))
    checked_at = None if policy is None else policy.checked_at
    if (
        policy is None
        or policy.use_scope not in {"local_non_commercial_research", "public_display"}
        or checked_at is None
        or checked_at.replace(tzinfo=checked_at.tzinfo or timezone.utc) < datetime.now(timezone.utc) - timedelta(days=365)
    ):
        raise ReviewWorkflowError("请先登记并复核来源使用范围")
    try:
        batch = crawl(client, [source], pause=pause)
    except CrawlError as exc:
        record_source_failure(session, source.key, type(exc).__name__)
        return {"source_id": source.key, "status": "failed", "pending": 0}
    result = stage_additional_batch(session, batch)
    return {"source_id": source.key, "status": "checked", "pending": result["pending"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="逐个复查预设官网项目，仅生成待审核变化")
    parser.add_argument("--course", choices=["all", *SOURCES], default="all")
    parser.add_argument("--proxy", type=_loopback_proxy, help="可选本机代理，如 http://127.0.0.1:3067")
    args = parser.parse_args()
    from scripts import sync_program_sources

    selected = list(SOURCES) if args.course == "all" else [args.course]
    forwarded = []
    for source_id in selected:
        forwarded.extend(("--source", source_id))
    if args.proxy:
        forwarded.extend(("--proxy", args.proxy))
    return sync_program_sources.main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
