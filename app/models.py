import json
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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


class ChatLog(Base):
    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    assistant_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(ForeignKey("programs.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_json: Mapped[str | None] = mapped_column(Text)
    program: Mapped[Program] = relationship(back_populates="chunks")
