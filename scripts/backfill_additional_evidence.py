"""Backfill evidence only from the previously approved six-course snapshot."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.config import settings
from app.database import make_engine, make_session_factory, migrate_database
from app.services.data_review_service import ReviewWorkflowError, backfill_additional_review
from scripts.review_additional_programs import DEFAULT_BATCH, DEFAULT_REVIEW, _read_json


def main() -> int:
    parser = argparse.ArgumentParser(description="把已核对港校快照迁入逐字段证据表")
    parser.add_argument("--input", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    args = parser.parse_args()
    migrate_database(settings.database_url)
    engine = make_engine(settings.database_url)
    try:
        with make_session_factory(engine)() as session:
            count = backfill_additional_review(session, _read_json(args.input), _read_json(args.review))
    except (ReviewWorkflowError, ValueError, OSError) as exc:
        parser.exit(1, f"证据迁移停止：{exc}\n")
    finally:
        engine.dispose()
    print(f"已迁入 {count} 条已核对字段证据；项目值未改变")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
