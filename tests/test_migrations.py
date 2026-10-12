import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import initialize_database, make_engine, migrate_database
from app.main import create_app


ROOT = Path(__file__).resolve().parent.parent


def migration_version(database: Path) -> str | None:
    with sqlite3.connect(database) as connection:
        if not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'alembic_version'"
        ).fetchone():
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None


def test_existing_bootstrap_database_can_be_migrated_without_losing_programs(tmp_path):
    database = tmp_path / "existing.db"
    engine = make_engine(f"sqlite:///{database.as_posix()}")
    initialize_database(engine, ROOT / "data" / "programs.json")
    engine.dispose()
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT COUNT(*) FROM programs").fetchone()[0]
    assert before > 0

    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert migration_version(database) == "0006_opt_in_history"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == before


def test_application_startup_migrates_a_new_database(tmp_path):
    database = tmp_path / "new.db"
    app = create_app(database_url=f"sqlite:///{database.as_posix()}")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200

    assert migration_version(database) == "0006_opt_in_history"


def test_upgrade_from_initial_version_preserves_existing_rows(tmp_path):
    database = tmp_path / "v1.db"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
    initial = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0001_initial"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert initial.returncode == 0, initial.stderr
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO universities (id, name, country) VALUES (1, 'Old U', '英国')")
        connection.execute(
            "INSERT INTO programs (id, university_id, university, country, program_name, degree, "
            "discipline, source_url, updated_at) VALUES "
            "(1, 1, 'Old U', '英国', 'Old course', '硕士', '计算机科学', "
            "'https://example.org/old', '2026-10-01')"
        )
    upgraded = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT program_name, data_origin FROM programs WHERE id = 1").fetchone()
    assert row == ("Old course", "demo")
    assert migration_version(database) == "0006_opt_in_history"


def test_incomplete_existing_schema_is_not_stamped(tmp_path):
    database = tmp_path / "incomplete.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE universities (id INTEGER PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="项目表不完整"):
        migrate_database(f"sqlite:///{database.as_posix()}")

    assert migration_version(database) is None
