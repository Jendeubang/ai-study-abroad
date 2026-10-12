"""Build vectors from published programme records only.

The optional model download is an explicit administrator action. Web requests
load only the previously saved local model and never download weights.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy.orm import Session

from app.config import settings
from app.database import initialize_database, make_engine, migrate_database
from app.services.vector_service import VectorUnavailable, build_vector_index, make_embedder


def main() -> int:
    parser = argparse.ArgumentParser(description="重建已发布项目的本地向量索引")
    parser.add_argument("--source-type", choices=("all", "official", "demo"), default="all")
    parser.add_argument("--download-model", action="store_true", help="下载配置的句向量模型到本地")
    args = parser.parse_args()

    if args.download_model:
        if settings.vector_backend != "sentence_transformers":
            parser.error("--download-model 需要 VECTOR_BACKEND=sentence_transformers")
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            parser.error("请先安装 requirements-vector.txt")
        model = SentenceTransformer(settings.vector_model_id, trust_remote_code=False)
        settings.vector_model_path.mkdir(parents=True, exist_ok=True)
        model.save(str(settings.vector_model_path))

    migrate_database(settings.database_url)
    engine = make_engine(settings.database_url)
    try:
        initialize_database(engine, settings.seed_path)
        embedder = make_embedder(
            settings.vector_backend,
            model_path=settings.vector_model_path,
            model_id=settings.vector_model_id,
        )
        with Session(engine) as session:
            summary = build_vector_index(session, embedder, source_type=args.source_type)
        print(json.dumps({"backend": settings.vector_backend, **summary}, ensure_ascii=False))
        return 0
    except VectorUnavailable as exc:
        parser.exit(1, f"向量索引未更新：{exc}\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
