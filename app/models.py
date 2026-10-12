import json
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class University(Base):
    __tablename__ = "universities"
    __table_args__ = (UniqueConstraint("name", "country"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    ranking: Mapped[int | None] = mapped_column(Integer)
    logo_url: Mapped[str | None] = mapped_column(String(500))
    programs: Mapped[list["Program"]] = relationship(back_populates="university_record")


class Program(Base):
    __tablename__ = "programs"
    __table_args__ = (Index("ix_programs_source_id", "source_id", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), nullable=False)
    university: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    program_name: Mapped[str] = mapped_column(String(250), nullable=False)
    degree: Mapped[str] = mapped_column(String(50), nullable=False)
    discipline: Mapped[str] = mapped_column(String(150), nullable=False)
    duration: Mapped[str | None] = mapped_column(String(100))
    tuition: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(10))
    intake: Mapped[str | None] = mapped_column(String(100))
    deadline: Mapped[date | None] = mapped_column(Date)
    gpa_min: Mapped[float | None] = mapped_column(Float)
    ielts_min: Mapped[float | None] = mapped_column(Float)
    toefl_min: Mapped[float | None] = mapped_column(Float)
    gre_required: Mapped[bool | None]
    website: Mapped[str | None] = mapped_column(String(500))
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    data_origin: Mapped[str] = mapped_column(String(30), default="demo", server_default="demo", nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(80))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    source_retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    academic_standard: Mapped[str | None] = mapped_column(String(120))
    language_details_json: Mapped[str | None] = mapped_column(Text)
    university_record: Mapped[University] = relationship(back_populates="programs")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="program")

    @property
    def language_details(self) -> dict | None:
        return json.loads(self.language_details_json) if self.language_details_json else None


class QueryLog(Base):
    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_query: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    owner_hash: Mapped[str | None] = mapped_column(String(64), index=True)


class ChatLog(Base):
    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class HistoryConsent(Base):
    __tablename__ = "history_consents"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str | None] = mapped_column(Text)
    program: Mapped[Program] = relationship(back_populates="chunks")


class SourcePermission(Base):
    __tablename__ = "source_permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    host: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    terms_url: Mapped[str] = mapped_column(String(500), nullable=False)
    use_scope: Mapped[str] = mapped_column(String(50), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)


class SourceDefinition(Base):
    """An exact, reviewable official page the collector may consider."""

    __tablename__ = "source_definitions"
    __table_args__ = (Index("ix_source_definitions_host", "host"),)

    source_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    programme_key: Mapped[str] = mapped_column(String(100), nullable=False)
    host: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    university: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    program_name: Mapped[str] = mapped_column(String(250), nullable=False)
    degree: Mapped[str] = mapped_column(String(50), nullable=False)
    discipline: Mapped[str] = mapped_column(String(150), nullable=False)
    parser_key: Mapped[str] = mapped_column(String(50), nullable=False)
    allowed_fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    @property
    def allowed_fields(self) -> tuple[str, ...]:
        return tuple(json.loads(self.allowed_fields_json))


class EvidenceFact(Base):
    __tablename__ = "evidence_facts"
    __table_args__ = (Index("ix_evidence_facts_program_active", "program_id", "active"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(50), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    @property
    def value(self) -> dict:
        return json.loads(self.value_json)

    @property
    def scope(self) -> dict:
        return json.loads(self.scope_json)


class ApplicationRound(Base):
    __tablename__ = "application_rounds"
    __table_args__ = (Index("ix_application_rounds_program_active", "program_id", "active"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), nullable=False)
    intake: Mapped[str] = mapped_column(String(100), nullable=False)
    round_label: Mapped[str] = mapped_column(String(100), nullable=False)
    opens_on: Mapped[date | None] = mapped_column(Date)
    deadline_on: Mapped[date | None] = mapped_column(Date)
    evidence_excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eligible_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checked_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pending_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_statuses_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)

    @property
    def source_statuses(self) -> list[dict]:
        return json.loads(self.source_statuses_json)


class ProgramCandidate(Base):
    """Initial programme facts held for review before Program creation."""

    __tablename__ = "program_candidates"
    __table_args__ = (
        Index("ix_program_candidates_status", "status"),
        Index("ix_program_candidates_fingerprint", "fingerprint", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    programme_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_snapshots_json: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    facts_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer: Mapped[str | None] = mapped_column(String(100))
    review_note: Mapped[str | None] = mapped_column(String(500))

    @property
    def source_ids(self) -> list[str]:
        return json.loads(self.source_ids_json)

    @property
    def source_snapshots(self) -> dict[str, str]:
        return json.loads(self.source_snapshots_json)

    @property
    def program_metadata(self) -> dict:
        return json.loads(self.metadata_json)

    @property
    def facts(self) -> list[dict]:
        return json.loads(self.facts_json)


class SourceCheck(Base):
    __tablename__ = "source_checks"
    __table_args__ = (Index("ix_source_checks_source_time", "source_id", "checked_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False)
    program_id: Mapped[int | None] = mapped_column(ForeignKey("programs.id"))
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(String(300))
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"))


class ReviewCandidate(Base):
    __tablename__ = "review_candidates"
    __table_args__ = (
        Index("ix_review_candidates_status", "status"),
        Index("ix_review_candidates_fingerprint", "fingerprint", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(50))
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    new_value_json: Mapped[str] = mapped_column(Text, nullable=False)
    old_value_json: Mapped[str | None] = mapped_column(Text)
    evidence_excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer: Mapped[str | None] = mapped_column(String(100))
    review_note: Mapped[str | None] = mapped_column(String(500))

    @property
    def scope(self) -> dict:
        return json.loads(self.scope_json)

    @property
    def new_value(self) -> dict:
        return json.loads(self.new_value_json)

    @property
    def old_value(self) -> dict | None:
        return json.loads(self.old_value_json) if self.old_value_json else None


class ReviewDecision(Base):
    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("review_candidates.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
