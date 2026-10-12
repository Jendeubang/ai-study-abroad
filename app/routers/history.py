"""Browser-local history controls. Mutations require a same-origin custom header."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from slowapi import Limiter
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.history_service import (
    COOKIE_MAX_AGE, COOKIE_NAME, delete_all, delete_one, grant_consent,
    history_items, history_owner, prune_expired,
)


DbSession = Annotated[Session, Depends(get_db)]


def _action(x_history_action: Annotated[str | None, Header(alias="X-History-Action")] = None) -> None:
    if x_history_action != "1":
        raise HTTPException(status_code=403, detail="缺少历史记录操作标识")


Action = Annotated[None, Depends(_action)]


def _response(request: Request, data: dict) -> dict:
    return {"code": 0, "message": "ok", "data": data, "request_id": request.state.request_id}


def _owner(request: Request, db: Session) -> str:
    owner = history_owner(db, request)
    if owner is None:
        raise HTTPException(status_code=403, detail="请先开启历史记录")
    return owner


def build_router(limiter: Limiter) -> APIRouter:
    router = APIRouter(prefix="/api/history", tags=["history"])

    @router.get("")
    @limiter.limit("30/minute")
    def list_history(
        request: Request, db: DbSession,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ):
        prune_expired(db)
        owner = history_owner(db, request)
        return _response(request, {
            "enabled": owner is not None,
            "items": history_items(db, owner, limit=limit) if owner else [],
        })

    @router.post("/consent")
    @limiter.limit("10/minute")
    def consent(request: Request, response: Response, db: DbSession, _action_required: Action):
        prune_expired(db)
        if history_owner(db, request) is None:
            token = grant_consent(db)
            response.set_cookie(
                COOKIE_NAME, token, max_age=COOKIE_MAX_AGE,
                httponly=True, secure=request.url.scheme == "https", samesite="strict",
            )
        return _response(request, {"enabled": True, "items": []})

    @router.delete("/consent")
    @limiter.limit("10/minute")
    def revoke(request: Request, response: Response, db: DbSession, _action_required: Action):
        owner = _owner(request, db)
        delete_all(db, owner, revoke=True)
        response.delete_cookie(COOKIE_NAME, samesite="strict")
        return _response(request, {"enabled": False, "items": []})

    @router.delete("")
    @limiter.limit("20/minute")
    def clear(request: Request, db: DbSession, _action_required: Action):
        delete_all(db, _owner(request, db))
        return _response(request, {"enabled": True, "items": []})

    @router.delete("/{kind}/{item_id}")
    @limiter.limit("30/minute")
    def remove(
        request: Request, db: DbSession, _action_required: Action,
        kind: Literal["search", "recommend", "chat"], item_id: int,
    ):
        if item_id < 1 or not delete_one(db, _owner(request, db), kind, item_id):
            raise HTTPException(status_code=404, detail="历史记录不存在")
        return _response(request, {"deleted": True})

    return router
