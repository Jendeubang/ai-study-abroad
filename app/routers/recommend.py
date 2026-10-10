from typing import Annotated

from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import QueryRequest, RecommendRequest
from app.services.parser_service import parse_with_fallback
from app.services.recommend_service import add_recommendations, apply_recommendation_filters
from app.services.search_service import search_programs


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
        results = search_programs(db, criteria)
        items, ai_available = await add_recommendations(
            results["items"], parsed, request.app.state.ai_service, use_ai=parsed_by_ai,
        )
        warnings = []
        if parsed.gpa and parsed.gpa.scale != 4:
            warnings.append("GPA 已按分制比例粗略换算，仅供初筛参考")
        if parsed.budget and parsed.budget.currency != "CNY":
            warnings.append("预算币种非人民币，未进行预算打分或汇率换算")
        return {
            "code": 0, "message": "ok",
            "data": {
                "parsed": parsed.model_dump(mode="json"),
                "items": items,
                "total": results["total"],
                "page": results["page"],
                "page_size": results["page_size"],
                "ai_available": ai_available,
                "notice": None if ai_available else "AI 推荐暂不可用",
                "warnings": warnings,
            },
            "request_id": request.state.request_id,
        }

    return router
