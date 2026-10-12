from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.crud import get_program
from app.database import get_db
from app.schemas import ProgramRead
from app.services.search_service import SearchCriteria, search_programs, validate_search_text
from app.services.data_review_service import review_alerts
from app.services.history_service import history_owner, save_search


router = APIRouter(prefix="/api/programs", tags=["programs"])
DbSession = Annotated[Session, Depends(get_db)]
SortKey = Literal["relevance", "deadline", "updated_at", "ranking", "tuition_asc", "tuition_desc"]


@router.get("")
def list_programs(
    request: Request,
    db: DbSession,
    q: Annotated[str | None, Query(max_length=500)] = None,
    country: Annotated[str | None, Query(max_length=100)] = None,
    degree: Annotated[str | None, Query(max_length=50)] = None,
    discipline: Annotated[str | None, Query(max_length=150)] = None,
    gpa: Annotated[float | None, Query(ge=0, le=100)] = None,
    ielts: Annotated[float | None, Query(ge=0, le=9)] = None,
    budget: Annotated[float | None, Query(ge=0)] = None,
    intake: Annotated[str | None, Query(max_length=100)] = None,
    ranking_max: Annotated[int | None, Query(ge=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: SortKey = "relevance",
    source_type: Literal["all", "official", "demo"] = "all",
    retrieval: Literal["hybrid", "keyword", "vector"] = "hybrid",
):
    try:
        q = validate_search_text(q)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    criteria = SearchCriteria(
        q=q, country=country, degree=degree, discipline=discipline,
        gpa=gpa, ielts=ielts, budget=budget, intake=intake,
        ranking_max=ranking_max, page=page, page_size=page_size, sort=sort,
        source_type=source_type,
        retrieval=retrieval,
    )
    results = search_programs(
        db, criteria, embedder=request.app.state.vector_embedder,
    )
    if page == 1:
        save_search(db, history_owner(db, request), q, kind="search", filters={
            "country": country, "degree": degree, "discipline": discipline,
            "gpa": gpa, "ielts": ielts, "budget": budget,
            "intake": intake, "ranking_max": ranking_max,
            "source_type": source_type if source_type != "all" else None,
            "retrieval": retrieval if retrieval != "hybrid" else None,
        })
    return {"code": 0, "message": "ok", "data": results, "request_id": request.state.request_id}


@router.get("/{program_id}")
def program_detail(request: Request, db: DbSession, program_id: int):
    program = get_program(db, program_id)
    if program is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    data = ProgramRead.model_validate(program).model_dump(mode="json")
    data["university_ranking"] = program.university_record.ranking
    data["disclaimer"] = "数据仅供参考，不保证录取；请以项目来源及官网为准。"
    data["review_alerts"] = review_alerts(db, program)
    return {"code": 0, "message": "ok", "data": data, "request_id": request.state.request_id}
