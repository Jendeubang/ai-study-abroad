from dataclasses import replace
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import QueryRequest, RecommendRequest
from app.services.parser_service import parse_with_fallback
from app.services.recommend_service import add_recommendations, apply_recommendation_filters
from app.services.search_service import search_programs
from app.services.history_service import history_owner, save_search


DbSession = Annotated[Session, Depends(get_db)]


def build_router(limiter: Limiter) -> APIRouter:
    router = APIRouter(tags=["AI recommendation"])

    @router.post("/api/parse")
    @limiter.limit("10/minute")
    async def parse(request: Request, body: QueryRequest):
        parsed, ai_available = await parse_with_fallback(body.query, request.app.state.ai_service)
        return {
            "code": 0, "message": "ok",
            "data": {
                "parsed": parsed.model_dump(mode="json"),
                "ai_available": ai_available,
                "notice": None if ai_available else "AI 解析暂不可用，已使用规则解析",
            },
            "request_id": request.state.request_id,
        }

    @router.post("/api/recommend")
    @limiter.limit("10/minute")
    async def recommend(request: Request, body: RecommendRequest, db: DbSession):
        parsed, parsed_by_ai = await parse_with_fallback(body.query, request.app.state.ai_service)
        parsed, criteria = apply_recommendation_filters(
            parsed, body.filters, page=body.page, page_size=body.page_size,
        )
        criteria = replace(criteria, semantic_query=body.query)
        results = search_programs(
            db, criteria, embedder=request.app.state.vector_embedder,
        )
        items, ai_available = await add_recommendations(
            results["items"], parsed, request.app.state.ai_service, use_ai=parsed_by_ai,
        )
        warnings = []
        if parsed.gpa and parsed.gpa.scale != 4:
            warnings.append("GPA 已按分制比例粗略换算，仅供初筛参考")
        if parsed.budget and parsed.budget.currency != "CNY":
            warnings.append("预算币种非人民币，未进行预算打分或汇率换算")
        if body.page == 1:
            save_search(
                db, history_owner(db, request), body.query, kind="recommend",
                filters=body.filters.model_dump(exclude_none=True),
            )
        return {
            "code": 0, "message": "ok",
            "data": {
                "parsed": parsed.model_dump(mode="json"),
                "items": items,
                "total": results["total"],
                "page": results["page"],
                "page_size": results["page_size"],
                "retrieval_mode": results["retrieval_mode"],
                "ai_available": ai_available,
                "notice": None if ai_available else "AI 推荐暂不可用",
                "warnings": warnings,
            },
            "request_id": request.state.request_id,
        }

    return router
