from typing import Annotated

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import ChatRequest
from app.services.chat_service import answer_question


DbSession = Annotated[Session, Depends(get_db)]


def build_router(limiter: Limiter) -> APIRouter:
    router = APIRouter(tags=["AI chat"])

    @router.post("/api/chat")
    @limiter.limit("10/minute")
    async def chat(request: Request, body: ChatRequest, db: DbSession):
        answer = await answer_question(db, body.message, body.program_ids, request.app.state.ai_service)
        return {"code": 0, "message": "ok", "data": answer, "request_id": request.state.request_id}

    return router
