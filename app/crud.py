import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from app.models import Program


def get_program(session: Session, program_id: int) -> Program | None:
    return session.scalar(
        select(Program).options(joinedload(Program.university_record)).where(Program.id == program_id)
    )


def find_programs(
    session: Session,
    *,
    q: str | None = None,
    country: str | None = None,
    countries: tuple[str, ...] | None = None,
    degree: str | None = None,
    discipline: str | None = None,
    intake: str | None = None,
    source_type: str = "all",
) -> list[Program]:
    statement = select(Program).options(joinedload(Program.university_record))
    if source_type == "official":
        statement = statement.where(Program.data_origin == "official_reviewed")
    elif source_type == "demo":
        statement = statement.where(Program.data_origin == "demo")
    if country:
        statement = statement.where(Program.country == country)
    if countries:
        statement = statement.where(Program.country.in_(countries))
    if degree:
        statement = statement.where(Program.degree == degree)
    if discipline:
        statement = statement.where(Program.discipline.contains(discipline, autoescape=True))
    if intake:
        statement = statement.where(Program.intake.contains(intake, autoescape=True))
    if q:
        latin_terms = re.findall(r"[A-Za-z0-9]+", q.lower())[:12]
        han_terms = re.findall(r"[\u3400-\u9fff]+", q)[:6]
        matching_ids: set[int] = set()
        if latin_terms:
            fts_query = " OR ".join(f'"{term}"' for term in latin_terms)
            matching_ids.update(
                session.scalars(
                    text("SELECT rowid FROM program_fts WHERE program_fts MATCH :query"),
                    {"query": fts_query},
                ).all()
            )
        if han_terms:
            for term in han_terms:
                matching_ids.update(
                    session.scalars(
                        select(Program.id).where(
                            Program.program_name.contains(term, autoescape=True)
                            | Program.discipline.contains(term, autoescape=True)
                            | Program.description.contains(term, autoescape=True)
                            | Program.university.contains(term, autoescape=True)
                        )
                    ).all()
                )
        if not matching_ids:
            return []
        statement = statement.where(Program.id.in_(matching_ids))
    return list(session.scalars(statement).all())
