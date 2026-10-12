from dataclasses import dataclass
from datetime import date, datetime, timezone
import re

from sqlalchemy.orm import Session

from app.crud import find_programs
from app.models import Program
from app.schemas import ProgramListItem, ProgramRead
from app.services.data_review_service import review_alerts
from app.services.vector_service import Embedder, VectorUnavailable, vector_scores


@dataclass(frozen=True)
class SearchCriteria:
    q: str | None = None
    country: str | None = None
    countries: tuple[str, ...] | None = None
    degree: str | None = None
    discipline: str | None = None
    gpa: float | None = None
    ielts: float | None = None
    toefl: float | None = None
    budget: float | None = None  # CNY; cross-currency comparisons are uncertain.
    intake: str | None = None
    ranking_max: int | None = None
    page: int = 1
    page_size: int = 20
    sort: str = "relevance"
    source_type: str = "all"
    retrieval: str = "hybrid"
    semantic_query: str | None = None


def validate_search_text(q: str | None) -> str | None:
    if q is None or not q.strip():
        return None
    q = q.strip()
    if len(q) > 500 or not re.search(r"[A-Za-z0-9\u3400-\u9fff]", q):
        raise ValueError("搜索内容必须包含文字或数字，且不超过 500 字符")
    return q


def _score(program: Program, criteria: SearchCriteria) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 100.0
    if criteria.gpa is not None:
        gpa = criteria.gpa
        if gpa > 5:
            gpa = gpa / 25
            notes.append("GPA 百分制仅作粗略换算")
        elif gpa > 4:
            gpa = gpa * 4 / 5
            notes.append("GPA 五分制仅作粗略换算")
        if program.gpa_min is None:
            score -= 10
            notes.append("当前项目未记录 GPA 要求")
        elif gpa < program.gpa_min:
            score -= 20
            notes.append("GPA 低于项目参考门槛")
    if criteria.ielts is not None:
        if program.ielts_min is None:
            score -= 7.5
            notes.append("雅思要求信息缺失")
        elif criteria.ielts < program.ielts_min:
            score -= 15
            notes.append("雅思低于项目参考门槛")
    elif criteria.toefl is not None:
        if program.toefl_min is None:
            score -= 7.5
            notes.append("托福要求信息缺失")
        elif criteria.toefl < program.toefl_min:
            score -= 15
            notes.append("托福低于项目参考门槛")
    if criteria.budget is not None:
        if program.tuition is None or program.currency is None:
            score -= 7.5
            notes.append("学费信息缺失")
        elif program.currency != "CNY":
            score -= 7.5
            currency_names = {
                "HKD": "港币", "GBP": "英镑", "USD": "美元",
                "SGD": "新加坡元", "CAD": "加元", "AUD": "澳元",
            }
            currency = currency_names.get(program.currency, program.currency)
            notes.append(f"学费使用{currency}，与人民币预算币种不同，暂无法直接比较")
        elif program.tuition > criteria.budget:
            score -= 15
            notes.append("学费高于预算；未计入生活费")
    if criteria.ranking_max is not None:
        ranking = program.university_record.ranking
        if ranking is None:
            score -= 5
            notes.append("排名信息缺失")
        elif ranking > criteria.ranking_max:
            score -= 10
            notes.append("排名超出偏好范围")
    if program.deadline and program.deadline < date.today():
        notes.append("截止日期已过，请以项目来源为准")
    updated = program.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    if (datetime.now(timezone.utc) - updated).days > 180:
        notes.append("数据超过 180 天未更新，请核对来源")
    return max(0, min(100, round(score))), notes


def search_programs(
    session: Session, criteria: SearchCriteria, *, embedder: Embedder | None = None,
) -> dict:
    if criteria.retrieval not in {"hybrid", "keyword", "vector"}:
        raise ValueError("检索方式无效")
    filters = dict(
        country=criteria.country, countries=criteria.countries,
        degree=criteria.degree, discipline=criteria.discipline,
        intake=criteria.intake, source_type=criteria.source_type,
    )
    search_text = criteria.semantic_query or criteria.q
    semantic: dict[int, float] = {}
    retrieval_mode = "keyword"
    if search_text and criteria.retrieval != "keyword" and embedder is not None:
        eligible = find_programs(session, **filters)
        try:
            semantic = vector_scores(session, eligible, search_text, embedder)
            retrieval_mode = criteria.retrieval
        except VectorUnavailable:
            retrieval_mode = "keyword"
        if retrieval_mode != "keyword":
            if criteria.q and criteria.retrieval == "hybrid":
                lexical_ids = {item.id for item in find_programs(session, q=criteria.q, **filters)}
                selected_ids = lexical_ids | set(semantic)
                programs = [item for item in eligible if item.id in selected_ids]
            elif criteria.retrieval == "vector":
                programs = [item for item in eligible if item.id in semantic]
            else:
                programs = eligible
        else:
            programs = find_programs(session, q=criteria.q, **filters)
    else:
        programs = find_programs(session, q=criteria.q, **filters)
    scored = [(program, *_score(program, criteria)) for program in programs]
    if criteria.sort == "deadline":
        scored.sort(key=lambda row: (row[0].deadline is None, row[0].deadline or date.max, row[0].id))
    elif criteria.sort == "updated_at":
        scored.sort(key=lambda row: (row[0].updated_at, -row[0].id), reverse=True)
    elif criteria.sort == "ranking":
        scored.sort(key=lambda row: (row[0].university_record.ranking is None, row[0].university_record.ranking or 10**9, row[0].id))
    elif criteria.sort in ("tuition_asc", "tuition_desc"):
        scored.sort(key=lambda row: (row[0].currency or "ZZZ", row[0].tuition is None, row[0].tuition or 0, row[0].id), reverse=criteria.sort == "tuition_desc")
    else:
        scored.sort(key=lambda row: (
            -(row[1] + 20 * semantic.get(row[0].id, 0)),
            row[0].data_origin != "official_reviewed", row[0].id,
        ))
    start = (criteria.page - 1) * criteria.page_size
    items = []
    for program, score, notes in scored[start:start + criteria.page_size]:
        item = ProgramRead.model_validate(program).model_dump(mode="json")
        item.update(
            match_score=score, match_notes=notes,
            university_ranking=program.university_record.ranking,
            review_alerts=review_alerts(session, program),
        )
        items.append(ProgramListItem.model_validate(item).model_dump(mode="json"))
    return {
        "items": items, "total": len(scored), "page": criteria.page,
        "page_size": criteria.page_size, "retrieval_mode": retrieval_mode,
    }
