"""Opt-in local history. Cookie secrets are never written to the database."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ChatLog, HistoryConsent, QueryLog


COOKIE_NAME = "studycompass_history"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60
RETENTION_DAYS = 90
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_IDENTITY = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{40,128}$")


def redact(value: str, *, max_length: int = 4000) -> str:
    value = _EMAIL.sub("[邮箱]", value[:max_length])
    value = _IDENTITY.sub("[身份证号]", value)
    return _PHONE.sub("[手机号]", value)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def history_owner(db: Session, request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME, "")
    if not _TOKEN.fullmatch(token):
        return None
    digest = token_hash(token)
    return digest if db.scalar(select(HistoryConsent.id).where(HistoryConsent.token_hash == digest)) else None


def grant_consent(db: Session) -> str:
    token = secrets.token_urlsafe(32)
    db.add(HistoryConsent(token_hash=token_hash(token)))
    db.commit()
    return token


def prune_expired(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    db.execute(delete(QueryLog).where(QueryLog.owner_hash.is_not(None), QueryLog.created_at < cutoff))
    db.execute(delete(ChatLog).where(ChatLog.created_at < cutoff))
    db.execute(delete(HistoryConsent).where(
        HistoryConsent.created_at < datetime.now(timezone.utc) - timedelta(seconds=COOKIE_MAX_AGE),
    ))
    db.commit()


def save_search(db: Session, owner: str | None, query: str | None, *, kind: str, filters: dict) -> None:
    if owner is None:
        return
    query = (query or "").strip()
    selected = {key: str(value)[:150] for key, value in filters.items() if value not in (None, "", "all")}
    if not query and not selected:
        return
    db.add(QueryLog(
        owner_hash=owner, raw_query=redact(query) if query else "筛选检索",
        parsed_json=json.dumps({"kind": kind, "filters": {
            key: redact(value, max_length=150) for key, value in selected.items()
        }}, ensure_ascii=False),
        ip_hash=None,
    ))
    db.commit()


def save_chat(db: Session, owner: str | None, message: str, answer: str) -> None:
    if owner is None:
        return
    db.add(ChatLog(
        session_id=owner, user_message=redact(message), assistant_message=redact(answer),
    ))
    db.commit()


def history_items(db: Session, owner: str, *, limit: int = 50) -> list[dict]:
    def timestamp(value: datetime) -> str:
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()

    queries = list(db.scalars(select(QueryLog).where(QueryLog.owner_hash == owner).order_by(
        QueryLog.created_at.desc(), QueryLog.id.desc(),
    ).limit(limit)))
    chats = list(db.scalars(select(ChatLog).where(ChatLog.session_id == owner).order_by(
        ChatLog.created_at.desc(), ChatLog.id.desc(),
    ).limit(limit)))
    items = []
    for entry in queries:
        try:
            details = json.loads(entry.parsed_json or "{}")
        except ValueError:
            details = {}
        items.append({
            "kind": "recommend" if details.get("kind") == "recommend" else "search",
            "id": entry.id, "query": entry.raw_query,
            "filters": details.get("filters", {}), "created_at": timestamp(entry.created_at),
        })
    for entry in chats:
        items.append({
            "kind": "chat", "id": entry.id, "query": entry.user_message,
            "answer": entry.assistant_message, "created_at": timestamp(entry.created_at),
        })
    items.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    return items[:limit]


def delete_one(db: Session, owner: str, kind: str, item_id: int) -> bool:
    if kind == "chat":
        statement = delete(ChatLog).where(ChatLog.id == item_id, ChatLog.session_id == owner)
    else:
        entry = db.scalar(select(QueryLog).where(QueryLog.id == item_id, QueryLog.owner_hash == owner))
        if entry is None:
            return False
        try:
            actual_kind = json.loads(entry.parsed_json or "{}").get("kind", "search")
        except (TypeError, ValueError, AttributeError):
            actual_kind = "search"
        if actual_kind != kind:
            return False
        statement = delete(QueryLog).where(QueryLog.id == item_id, QueryLog.owner_hash == owner)
    removed = db.execute(statement).rowcount
    db.commit()
    return bool(removed)


def delete_all(db: Session, owner: str, *, revoke: bool = False) -> None:
    db.execute(delete(QueryLog).where(QueryLog.owner_hash == owner))
    db.execute(delete(ChatLog).where(ChatLog.session_id == owner))
    if revoke:
        db.execute(delete(HistoryConsent).where(HistoryConsent.token_hash == owner))
    db.commit()
