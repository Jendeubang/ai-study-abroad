"""Token-protected local review workflow. No token is rendered into HTML."""

from __future__ import annotations

import hmac
import logging
from datetime import date, datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from slowapi import Limiter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import (
    Program, ProgramCandidate, ReviewCandidate, SourceCheck, SourceDefinition,
    SourcePermission, SyncRun,
)
from app.sources.catalog import (
    SourceCatalogError, permission_expiry, set_source_enabled,
)
from app.services.data_review_service import (
    ReviewWorkflowError, decide_candidate, decide_program_candidate,
    set_source_permission, stage_round_candidate,
)
from app.services.vector_service import VectorUnavailable, build_vector_index


DbSession = Annotated[Session, Depends(get_db)]
logger = logging.getLogger("study_abroad")


def _refresh_local_vectors(request: Request, db: Session) -> None:
    embedder = request.app.state.vector_embedder
    if embedder.auto_index:
        try:
            build_vector_index(db, embedder, source_type="official")
        except VectorUnavailable:
            logger.warning("local vector index refresh unavailable after review")


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


class ProgramCandidateDecisionBody(DecisionBody):
    purpose: Literal["local_non_commercial_research", "public_display"] = "local_non_commercial_research"


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


def _program_candidate_data(candidate: ProgramCandidate) -> dict:
    metadata = candidate.program_metadata
    return {
        "id": candidate.id,
        "programme_key": candidate.programme_key,
        "university": metadata.get("university", ""),
        "country": metadata.get("country", ""),
        "city": metadata.get("city"),
        "program_name": metadata.get("program_name", ""),
        "degree": metadata.get("degree", ""),
        "discipline": metadata.get("discipline", ""),
        "source_ids": candidate.source_ids,
        "source_snapshots": candidate.source_snapshots,
        "facts": candidate.facts,
        "snapshot_sha256": candidate.snapshot_sha256,
        "retrieved_at": candidate.retrieved_at.isoformat(),
        "status": candidate.status,
        "created_at": candidate.created_at.isoformat(),
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
            latest_check = db.scalar(select(SourceCheck).where(
                SourceCheck.source_id == source.source_id,
            ).order_by(SourceCheck.checked_at.desc(), SourceCheck.id.desc()).limit(1))
            last_success = db.scalar(select(SourceCheck).where(
                SourceCheck.source_id == source.source_id,
                SourceCheck.status.in_(("changed", "unchanged")),
            ).order_by(SourceCheck.checked_at.desc(), SourceCheck.id.desc()).limit(1))
            pending_programme = db.scalar(select(ProgramCandidate.id).where(
                ProgramCandidate.programme_key == source.programme_key,
                ProgramCandidate.status == "pending",
            ).limit(1))
            pending_changes = db.scalar(select(ReviewCandidate.id).where(
                ReviewCandidate.source_url == source.url,
                ReviewCandidate.status == "pending",
            ).limit(1))
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
                "last_check_at": latest_check.checked_at.isoformat() if latest_check else None,
                "last_check_status": latest_check.status if latest_check else None,
                "last_success_at": last_success.checked_at.isoformat() if last_success else None,
                "has_pending_review": pending_programme is not None or pending_changes is not None,
            })
        return _response(request, data)

    @router.get("/sync-runs")
    @limiter.limit("20/minute")
    def list_sync_runs(
        request: Request, db: DbSession, _admin: Admin,
        limit: Annotated[int, Query(ge=1, le=30)] = 30,
    ):
        runs = list(db.scalars(select(SyncRun).order_by(
            SyncRun.started_at.desc(), SyncRun.id.desc(),
        ).limit(limit)))
        return _response(request, [{
            "run_id": run.run_id,
            "trigger": run.trigger,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "eligible_count": run.eligible_count,
            "checked_count": run.checked_count,
            "changed_count": run.changed_count,
            "failed_count": run.failed_count,
            "skipped_count": run.skipped_count,
            "pending_count": run.pending_count,
            "source_statuses": run.source_statuses,
        } for run in runs])

    @router.get("/program-candidates")
    @limiter.limit("20/minute")
    def list_program_candidates(
        request: Request, db: DbSession, _admin: Admin,
        status: Annotated[Literal["pending", "approved", "rejected", "all"], Query()] = "pending",
    ):
        statement = select(ProgramCandidate).order_by(
            ProgramCandidate.created_at.desc(), ProgramCandidate.id.desc(),
        )
        if status != "all":
            statement = statement.where(ProgramCandidate.status == status)
        candidates = list(db.scalars(statement.limit(200)))
        return _response(request, [_program_candidate_data(candidate) for candidate in candidates])

    @router.post("/program-candidates/{candidate_id}/{decision}")
    @limiter.limit("20/minute")
    def decide_program(
        request: Request, db: DbSession, _admin: Admin,
        candidate_id: int, decision: Literal["approve", "reject"],
        body: ProgramCandidateDecisionBody,
    ):
        try:
            candidate = decide_program_candidate(
                db, candidate_id,
                decision="approved" if decision == "approve" else "rejected",
                reviewer=body.reviewer, note=body.note, purpose=body.purpose,
            )
        except ReviewWorkflowError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if decision == "approve":
            _refresh_local_vectors(request, db)
        return _response(request, {
            "id": candidate.id, "status": candidate.status,
            "reviewer": candidate.reviewer, "review_note": candidate.review_note,
        })

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
        if decision == "approve":
            _refresh_local_vectors(request, db)
        return _response(request, {"id": candidate.id, "status": candidate.status})

    return router
