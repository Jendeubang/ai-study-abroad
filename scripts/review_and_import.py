"""Prepare an explicit review file, then import only approved official facts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import ROOT, settings
from app.database import make_engine, make_session_factory, migrate_database
from app.services.official_import_service import (
    ReviewError, import_reviewed_snapshot, prepare_review, validate_review,
)


DEFAULT_SNAPSHOT = ROOT / "data" / "official_requirements" / "latest.json"
DEFAULT_REVIEW = ROOT / "data" / "official_requirements" / "review.json"
MAX_INPUT_BYTES = 256_000


def _read_json(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ReviewError(f"文件不存在或过大：{path}")
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"JSON 文件无法读取：{path}") from exc
    if not isinstance(content, dict):
        raise ReviewError("JSON 顶层必须是对象")
    return content


def main() -> int:
    parser = argparse.ArgumentParser(description="核对并导入固定官网课程的招生要求摘要")
    parser.add_argument("action", choices=["prepare", "import"])
    parser.add_argument("--input", type=Path, default=DEFAULT_SNAPSHOT, help="爬虫输出的 JSON")
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW, help="人工核对清单")
    args = parser.parse_args()
    try:
        batch = _read_json(args.input)
        if args.action == "prepare":
            review = prepare_review(batch)
            args.review.parent.mkdir(parents=True, exist_ok=True)
            with args.review.open("x", encoding="utf-8") as stream:
                json.dump(review, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            print(f"已生成待核对清单：{args.review}")
            print("请逐条打开 source_url 核对，再填写 approved、review_note 和 rights_confirmed。")
            return 0
        review = _read_json(args.review)
        validate_review(batch, review)
        migrate_database(settings.database_url)
        engine = make_engine(settings.database_url)
        try:
            factory = make_session_factory(engine)
            with factory() as session:
                counts = import_reviewed_snapshot(session, batch, review)
        finally:
            engine.dispose()
        print("导入完成：新增 {imported}，更新 {updated}，未变 {unchanged}，未批准 {skipped}".format(**counts))
        return 0
    except (ReviewError, OSError) as exc:
        parser.exit(1, f"操作停止：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
