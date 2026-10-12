"""Small SQLite vector index over published programme fields.

The stored document is a programme summary, never a crawled page or a
pending review candidate. SentenceTransformer is an optional local backend.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DocumentChunk, Program


MAX_VECTOR_DIMENSIONS = 2048
MIN_SIMILARITY = 0.15


class VectorUnavailable(RuntimeError):
    """The selected vector model or a usable index is unavailable."""


class Embedder(Protocol):
    model_id: str
    auto_index: bool

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def _normalise(vector: Sequence[float]) -> list[float]:
    if not 1 <= len(vector) <= MAX_VECTOR_DIMENSIONS:
        raise VectorUnavailable("向量维度无效")
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise VectorUnavailable("向量包含无效数值")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise VectorUnavailable("向量为空")
    return [value / norm for value in values]


class HashNgramEmbedder:
    """Dependency-free local vector baseline for Chinese and Latin tokens."""

    model_id = "studycompass-hash-ngram-v1"
    auto_index = True

    def __init__(self, dimensions: int = 384):
        if not 16 <= dimensions <= MAX_VECTOR_DIMENSIONS:
            raise ValueError("向量维度必须在 16 到 2048 之间")
        self.dimensions = dimensions

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            latin = re.findall(r"[a-z0-9]+", lowered)
            han_runs = re.findall(r"[\u3400-\u9fff]+", lowered)
            tokens = latin[:500]
            for run in han_runs:
                tokens.extend(run[:200])
                tokens.extend(run[index:index + 2] for index in range(min(len(run) - 1, 200)))
            values = [0.0] * self.dimensions
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimensions
                sign = 1 if digest[4] & 1 else -1
                values[index] += sign
            results.append(_normalise(values))
        return results


class SentenceTransformerEmbedder:
    """Loads a previously downloaded model from disk; requests stay local."""

    auto_index = False

    def __init__(self, model_path: Path, model_id: str):
        self.model_path = model_path
        self.model_id = model_id
        self._model = None

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.model_path.is_dir():
            raise VectorUnavailable("本地句向量模型尚未下载")
        try:
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(
                    str(self.model_path), device="cpu", local_files_only=True,
                    trust_remote_code=False,
                )
            raw = self._model.encode(
                list(texts), normalize_embeddings=True,
                convert_to_numpy=True, show_progress_bar=False,
            )
            return [_normalise(row.tolist()) for row in raw]
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            raise VectorUnavailable("本地句向量模型不可用") from exc


def make_embedder(kind: str, *, model_path: Path, model_id: str) -> Embedder:
    if kind == "hash":
        return HashNgramEmbedder()
    if kind == "sentence_transformers":
        return SentenceTransformerEmbedder(model_path, model_id)
    raise ValueError("VECTOR_BACKEND 必须是 hash 或 sentence_transformers")


def published_program_text(program: Program) -> str:
    parts = [
        f"院校 {program.university}", f"国家地区 {program.country}",
        f"项目 {program.program_name}", f"学位 {program.degree}",
        f"专业 {program.discipline}",
    ]
    for label, value in (
        ("城市", program.city), ("学制", program.duration),
        ("入学季", program.intake), ("简介", program.description),
        ("标签", program.tags), ("学术要求", program.academic_standard),
    ):
        if value:
            parts.append(f"{label} {str(value)[:1500]}")
    if program.gpa_min is not None:
        parts.append(f"GPA {program.gpa_min:g}")
    if program.ielts_min is not None:
        parts.append(f"雅思 {program.ielts_min:g}")
    if program.toefl_min is not None:
        parts.append(f"托福 {program.toefl_min:g}")
    if program.tuition is not None and program.currency:
        parts.append(f"学费 {program.tuition:g} {program.currency}")
    return "；".join(parts)[:4000]


def _content_sha256(program: Program, content: str) -> str:
    identity = "\n".join((
        content, program.source_url, program.data_origin,
        program.source_sha256 or "", program.updated_at.isoformat(),
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _stored_summary(chunk: DocumentChunk) -> dict | None:
    try:
        value = json.loads(chunk.embedding_json or "null")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("kind") == "program_summary" else None


def build_vector_index(
    session: Session, embedder: Embedder, *, source_type: str = "all",
) -> dict[str, int]:
    """Upsert one vector per published programme and skip unchanged rows."""
    if source_type not in {"all", "official", "demo"}:
        raise ValueError("数据来源类型无效")
    statement = select(Program).where(Program.data_origin.in_(("official_reviewed", "demo")))
    if source_type == "official":
        statement = statement.where(Program.data_origin == "official_reviewed")
    elif source_type == "demo":
        statement = statement.where(Program.data_origin == "demo")
    programs = list(session.scalars(statement.order_by(Program.id)))
    existing: dict[int, DocumentChunk] = {}
    for chunk in session.scalars(select(DocumentChunk)):
        if _stored_summary(chunk) is not None:
            existing.setdefault(chunk.program_id, chunk)
    pending: list[tuple[Program, DocumentChunk | None, str, str]] = []
    unchanged = 0
    for program in programs:
        content = published_program_text(program)
        digest = _content_sha256(program, content)
        chunk = existing.get(program.id)
        envelope = _stored_summary(chunk) if chunk else None
        if envelope and envelope.get("model") == embedder.model_id and envelope.get("content_sha256") == digest and chunk.content == content:
            unchanged += 1
            continue
        pending.append((program, chunk, content, digest))
    if pending:
        try:
            vectors = embedder.encode([item[2] for item in pending])
        except Exception as exc:
            raise VectorUnavailable("向量生成失败") from exc
        if len(vectors) != len(pending):
            raise VectorUnavailable("向量数量不匹配")
        normalized = [_normalise(vector) for vector in vectors]
        for (program, chunk, content, digest), vector in zip(pending, normalized):
            if chunk is None:
                chunk = DocumentChunk(program_id=program.id, content=content)
                session.add(chunk)
            else:
                chunk.content = content
            chunk.embedding_json = json.dumps({
                "kind": "program_summary", "model": embedder.model_id,
                "content_sha256": digest, "vector": vector,
            }, separators=(",", ":"))
        session.commit()
    return {"indexed": len(pending), "unchanged": unchanged, "total": len(programs)}


def vector_scores(
    session: Session, programs: Sequence[Program], query: str, embedder: Embedder,
    *, limit: int = 100,
) -> dict[int, float]:
    """Return cosine scores for fresh vectors from the selected programme set."""
    if not programs:
        return {}
    by_id = {program.id: program for program in programs}
    valid: list[tuple[int, list[float]]] = []
    for chunk in session.scalars(select(DocumentChunk).where(DocumentChunk.program_id.in_(by_id))):
        envelope = _stored_summary(chunk)
        program = by_id.get(chunk.program_id)
        if not envelope or not program or envelope.get("model") != embedder.model_id:
            continue
        content = published_program_text(program)
        if chunk.content != content or envelope.get("content_sha256") != _content_sha256(program, content):
            continue
        try:
            vector = _normalise(envelope["vector"])
        except (KeyError, TypeError, ValueError, VectorUnavailable):
            continue
        valid.append((program.id, vector))
    if not valid:
        raise VectorUnavailable("没有可用的最新向量索引")
    try:
        encoded = embedder.encode([query])
        query_vector = _normalise(encoded[0])
    except Exception as exc:
        raise VectorUnavailable("查询向量生成失败") from exc
    scored = []
    for program_id, vector in valid:
        if len(vector) != len(query_vector):
            continue
        similarity = sum(a * b for a, b in zip(vector, query_vector))
        if similarity >= MIN_SIMILARITY:
            scored.append((program_id, similarity))
    scored.sort(key=lambda row: (-row[1], row[0]))
    return dict(scored[:limit])
