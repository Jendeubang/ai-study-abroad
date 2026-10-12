"""Prepare a denied-by-default review, then import approved course facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import ROOT, settings
from app.database import make_engine, make_session_factory, migrate_database
from app.services.additional_import_service import (
    AdditionalReviewError, import_reviewed, prepare_review, validate_review,
)


DEFAULT_BATCH = ROOT / "data" / "official_requirements" / "additional_latest.json"
DEFAULT_REVIEW = ROOT / "data" / "official_requirements" / "additional_review.json"
MAX_INPUT_BYTES = 256_000


def _read_json(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise AdditionalReviewError(f"文件不存在或过大：{path}")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdditionalReviewError(f"JSON 文件无法读取：{path}") from exc
    if not isinstance(content, dict):
        raise AdditionalReviewError("JSON 顶层必须是对象")
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description="人工核对港城大与港科大课程后导入")
    parser.add_argument("action", choices=["prepare", "validate", "import"])
    parser.add_argument("--input", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    args = parser.parse_args()
    try:
        batch = _read_json(args.input)
        if args.action == "prepare":
            review = prepare_review(batch)
            args.review.parent.mkdir(parents=True, exist_ok=True)
            with args.review.open("x", encoding="utf-8") as stream:
                json.dump(review, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(f"已生成默认不批准的核对清单：{args.review}")
            return 0
        review = _read_json(args.review)
        count = validate_review(batch, review)
        if args.action == "validate":
            print(f"已核对 {count} 条批准记录；尚未写入数据库")
            return 0
        migrate_database(settings.database_url)
        engine = make_engine(settings.database_url)
        try:
            with make_session_factory(engine)() as session:
                counts = import_reviewed(session, batch, review)
        finally:
            engine.dispose()
        print("导入完成：新增 {imported}，更新 {updated}，未变 {unchanged}，未批准 {skipped}".format(**counts))
        return 0
    except (AdditionalReviewError, OSError) as exc:
        parser.exit(1, f"操作停止：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
