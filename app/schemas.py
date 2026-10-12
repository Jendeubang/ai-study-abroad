from datetime import date, datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator



class ApiResponse(BaseModel):
    code: int = 0
    message: str = "ok"
    data: Any
    request_id: str


class ProgramRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    university: str
    country: str
    city: str | None
    program_name: str
    degree: str
    discipline: str
    duration: str | None
    tuition: float | None
    currency: str | None
    intake: str | None
    deadline: date | None
    gpa_min: float | None
    ielts_min: float | None
    toefl_min: float | None
    gre_required: bool | None
    website: str | None
    source_url: str
    description: str | None
    tags: str | None
    updated_at: datetime
    data_origin: Literal["demo", "official_reviewed"]
    source_id: str | None
    source_retrieved_at: datetime | None
    reviewed_at: datetime | None
    academic_standard: str | None
    language_details: dict | None


class ProgramListItem(ProgramRead):
    match_score: int = Field(ge=0, le=100)
    match_notes: list[str]
    university_ranking: int | None
    review_alerts: list[str] = Field(default_factory=list)


class GPAValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float = Field(ge=0, le=100)
    scale: float = Field(ge=4, le=100)

    @model_validator(mode="after")
    def check_scale(self):
        if self.scale not in (4.0, 5.0, 100.0) or self.value > self.scale:
            raise ValueError("GPA 分制无效")
        return self


class LanguageScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ielts: float | None = Field(default=None, ge=0, le=9)
    toefl: float | None = Field(default=None, ge=0, le=120)


class BudgetValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float = Field(ge=0, le=100_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class ParsedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countries: list[str] = Field(default_factory=list, max_length=5)
    degree: str | None = Field(default=None, max_length=50)
    discipline: str | None = Field(default=None, max_length=100)
    gpa: GPAValue | None = None
    language: LanguageScores = Field(default_factory=LanguageScores)
    budget: BudgetValue | None = None
    intake: str | None = Field(default=None, max_length=50)
    preferences: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("countries", "preferences")
    @classmethod
    def short_items(cls, values: list[str]) -> list[str]:
        if any(len(value) > 50 for value in values):
            raise ValueError("筛选项过长")
        return values


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=1000)

    @field_validator("query")
    @classmethod
    def valid_query(cls, value: str) -> str:
        validated = value.strip()
        if not validated or not re.search(r"[A-Za-z0-9\u3400-\u9fff]", validated):
            raise ValueError("请输入有效的留学需求")
        return validated


class RecommendationFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str | None = Field(default=None, min_length=1, max_length=100)
    degree: str | None = Field(default=None, min_length=1, max_length=50)
    discipline: str | None = Field(default=None, min_length=1, max_length=150)
    gpa: float | None = Field(default=None, ge=0, le=100)
    ielts: float | None = Field(default=None, ge=0, le=9)
    budget: float | None = Field(default=None, ge=0, le=100_000_000)
    intake: str | None = Field(default=None, min_length=1, max_length=100)
    ranking_max: int | None = Field(default=None, ge=1, le=10_000)
    source_type: Literal["all", "official", "demo"] = "all"
    sort: Literal["relevance", "deadline", "updated_at", "ranking", "tuition_asc", "tuition_desc"] = "relevance"
    retrieval: Literal["hybrid", "keyword", "vector"] = "hybrid"


class RecommendRequest(QueryRequest):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)
    filters: RecommendationFilters = Field(default_factory=RecommendationFilters)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)
    program_ids: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("message")
    @classmethod
    def valid_message(cls, value: str) -> str:
        if not value.strip() or not any(character.isalnum() for character in value):
            raise ValueError("请输入有效的问题")
        return value.strip()

    @field_validator("program_ids")
    @classmethod
    def valid_ids(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values) or len(values) != len(set(values)):
            raise ValueError("项目 ID 无效")
        return values
