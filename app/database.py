import json
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Request
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import ROOT
from app.models import Base, Program, University


FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS program_fts USING fts5("
    "program_name, discipline, description, tags, university, "
    "content='programs', content_rowid='id')",
    """CREATE TRIGGER IF NOT EXISTS programs_ai AFTER INSERT ON programs BEGIN
        INSERT INTO program_fts(rowid, program_name, discipline, description, tags, university)
        VALUES (new.id, new.program_name, new.discipline, new.description, new.tags, new.university);
    END""",
    """CREATE TRIGGER IF NOT EXISTS programs_ad AFTER DELETE ON programs BEGIN
        INSERT INTO program_fts(program_fts, rowid, program_name, discipline, description, tags, university)
        VALUES ('delete', old.id, old.program_name, old.discipline, old.description, old.tags, old.university);
    END""",
    """CREATE TRIGGER IF NOT EXISTS programs_au AFTER UPDATE ON programs BEGIN
        INSERT INTO program_fts(program_fts, rowid, program_name, discipline, description, tags, university)
        VALUES ('delete', old.id, old.program_name, old.discipline, old.description, old.tags, old.university);
        INSERT INTO program_fts(rowid, program_name, discipline, description, tags, university)
        VALUES (new.id, new.program_name, new.discipline, new.description, new.tags, new.university);
    END""",
)


def make_engine(database_url: str):
    if not database_url.startswith("sqlite:"):
        raise ValueError("第一阶段仅支持 SQLite 数据库")
    return create_engine(database_url, connect_args={"check_same_thread": False, "timeout": 5})


def migrate_database(database_url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def initialize_database(engine, seed_path: Path) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        fts_exists = connection.scalar(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='program_fts'")
        ) is not None
        for statement in FTS_DDL:
            connection.execute(text(statement))
        if not fts_exists:
            connection.execute(text("INSERT INTO program_fts(program_fts) VALUES ('rebuild')"))
    with Session(engine) as session:
        if session.scalar(select(Program.id).limit(1)) is not None:
            return
        records = json.loads(seed_path.read_text(encoding="utf-8"))
        universities: dict[tuple[str, str], University] = {}
        for record in records:
            key = (record["university"], record["country"])
            university = universities.get(key)
            if university is None:
                university = University(
                    name=record["university"], country=record["country"],
                    ranking=record.get("ranking"), logo_url=None,
                )
                session.add(university)
                session.flush()
                universities[key] = university
            program_data = {key: value for key, value in record.items() if key != "ranking"}
            if program_data.get("deadline"):
                program_data["deadline"] = date.fromisoformat(program_data["deadline"])
            session.add(Program(university_id=university.id, **program_data))
        session.commit()


def get_db(request: Request):
    with request.app.state.session_factory() as session:
        yield session


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)
