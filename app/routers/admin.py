"""Token-protected local review workflow. No token is rendered into HTML."""

from __future__ import annotations

import hmac
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from slowapi import Limiter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Program, ReviewCandidate, SourceCheck, SourceDefinition, SourcePermission
from app.sources.catalog import (
    SourceCatalogError, permission_expiry, set_source_enabled,
)
from app.services.data_review_service import (
    ReviewWorkflowError, decide_candidate, set_source_permission, stage_round_candidate,
)


DbSession = Annotated[Session, Depends(get_db)]


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=4, max_length=200)
    terms_url: str = Field(min_length=12, max_length=500)
    use_scope: Literal["unverified", "local_non_commercial_research", "public_display"]
    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=8, max_length=500)


class SourceEnableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer: str = Field(min_length=1, max_length=100)
    note: str = Field(min_length=8, max_length=500)


class RoundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_id: int = Field(ge=1)
    intake: str = Field(min_length=1, max_length=100)
    round_label: str = Field(min_length=1, max_length=100)
    opens_on: date | None = None
    deadline_on: date | None = None
    evidence_excerpt: str = Field(min_length=8, max_length=500)


def _response(request: Request, data: object) -> dict:
    return {"code": 0, "message": "ok", "data": data, "request_id": request.state.request_id}


def _candidate_data(candidate: ReviewCandidate, program: Program) -> dict:
    return {
        "id": candidate.id,
        "program_id": candidate.program_id,
        "university": program.university,
        "program_name": program.program_name,
        "kind": candidate.kind,
        "field_name": candidate.field_name,
        "scope": candidate.scope,
        "old_value": candidate.old_value,
        "new_value": candidate.new_value,
        "evidence_excerpt": candidate.evidence_excerpt,
        "source_url": candidate.source_url,
        "retrieved_at": candidate.retrieved_at.isoformat(),
        "status": candidate.status,
        "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
        "reviewer": candidate.reviewer,
        "review_note": candidate.review_note,
    }


def require_admin(
    request: Request,
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    expected = request.app.state.admin_token
    if not expected:
        raise HTTPException(status_code=503, detail="管理功能未配置 ADMIN_TOKEN")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="管理 token 无效")


Admin = Annotated[None, Depends(require_admin)]


def build_router(limiter: Limiter) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["review administration"])

    @router.get("/reviews")
    @limiter.limit("20/minute")
    def list_reviews(
        request: Request, db: DbSession, _admin: Admin,
        status: Annotated[Literal["pending", "approved", "rejected", "all"], Query()] = "pending",
    ):
        statement = select(ReviewCandidate).order_by(ReviewCandidate.created_at.desc(), ReviewCandidate.id.desc())
        if status != "all":
            statement = statement.where(ReviewCandidate.status == status)
        candidates = list(db.scalars(statement.limit(200)))
        programs = {program.id: program for program in db.scalars(
            select(Program).where(Program.id.in_({item.program_id for item in candidates}))
        )} if candidates else {}
        return _response(request, [_candidate_data(item, programs[item.program_id]) for item in candidates])

    @router.get("/sources")
    @limiter.limit("20/minute")
    def list_sources(request: Request, db: DbSession, _admin: Admin):
        policies = {item.host: item for item in db.scalars(select(SourcePermission))}
        data = []
        definitions = list(db.scalars(select(SourceDefinition).order_by(SourceDefinition.country, SourceDefinition.university, SourceDefinition.program_name, SourceDefinition.source_id)))
        now = datetime.now(timezone.utc)
        for source in definitions:
            policy = policies.get(source.host)
            expires_at = permission_expiry(policy) if policy else None
            expired = expires_at is None or now >= expires_at
            allowed_purposes = [] if expired or policy.use_scope == "unverified" else [policy.use_scope]
            data.append({
                "source_id": source.source_id,
                "programme_key": source.programme_key,
                "host": source.host,
                "url": source.url,
                "university": source.university,
                "program_name": source.program_name,
                "parser_key": source.parser_key,
                "allowed_fields": list(source.allowed_fields),
                "enabled": source.enabled,
                "terms_url": policy.terms_url if policy else None,
                "use_scope": policy.use_scope if policy else "unverified",
                "checked_at": policy.checked_at.isoformat() if policy else None,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "expired": expired,
                "allowed_purposes": allowed_purposes,
                "reviewer": policy.reviewer if policy else None,
                "note": policy.note if policy else None,
            })
        return _response(request, data)

    @router.post("/sources")
    @limiter.limit("20/minute")
    def save_source(request: Request, db: DbSession, _admin: Admin, body: PolicyBody):
        try:
            policy = set_source_permission(db, **body.model_dump())
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _response(request, {"host": policy.host, "use_scope": policy.use_scope})

    @router.patch("/sources/{source_id}")
    @limiter.limit("20/minute")
    def update_source(
        request: Request, db: DbSession, _admin: Admin,
        source_id: str, body: SourceEnableBody,
    ):
        try:
            source = set_source_enabled(db, source_id, enabled=body.enabled)
        except SourceCatalogError as exc:
            status_code = 404 if "不存在" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _response(request, {
            "source_id": source.source_id, "enabled": source.enabled,
        })

    @router.post("/rounds/candidates")
    @limiter.limit("20/minute")
    def create_round(request: Request, db: DbSession, _admin: Admin, body: RoundBody):
        program = db.get(Program, body.program_id)
        if program is None or program.data_origin != "official_reviewed" or not program.source_id:
            raise HTTPException(status_code=404, detail="官网项目不存在")
        check = db.scalar(select(SourceCheck).where(
            SourceCheck.program_id == program.id,
        ).order_by(SourceCheck.checked_at.desc(), SourceCheck.id.desc()).limit(1))
        if check is None or check.status == "failed" or not check.source_sha256:
            raise HTTPException(status_code=422, detail="请先成功采集该项目官网，再登记申请轮次")
        try:
            candidate = stage_round_candidate(
                db, **body.model_dump(), source_url=program.source_url,
                source_sha256=check.source_sha256, retrieved_at=check.checked_at,
            )
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _response(request, {"id": candidate.id, "status": candidate.status})

    @router.post("/reviews/{candidate_id}/{decision}")
    @limiter.limit("20/minute")
    def decide(
        request: Request, db: DbSession, _admin: Admin, candidate_id: int,
        decision: Literal["approve", "reject"], body: DecisionBody,
    ):
        try:
            candidate = decide_candidate(
                db, candidate_id, decision="approved" if decision == "approve" else "rejected",
                reviewer=body.reviewer, note=body.note,
            )
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _response(request, {"id": candidate.id, "status": candidate.status})

    return router
