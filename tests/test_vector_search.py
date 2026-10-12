import json

from sqlalchemy import select

from app.models import DocumentChunk, Program
from app.services.search_service import SearchCriteria, search_programs
from app.services.vector_service import build_vector_index


class FakeEmbeddings:
    model_id = "fake-semantic-v1"
    auto_index = False

    def __init__(self):
        self.calls = 0
        self.fail = False

    def encode(self, texts):
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding unavailable")
        return [[1.0, 0.0] if "Software Engineering" in text or text == "软件开发" else [0.0, 1.0] for text in texts]


def test_vector_index_is_idempotent_and_uses_published_programs(client):
    embedder = FakeEmbeddings()
    with client.app.state.session_factory() as session:
        first = build_vector_index(session, embedder)
        second = build_vector_index(session, embedder)
        chunks = list(session.scalars(select(DocumentChunk)))
        program_count = len(list(session.scalars(select(Program.id))))

    assert first["indexed"] == program_count
    assert second["indexed"] == 0
    assert second["unchanged"] == program_count
    assert len(chunks) == program_count
    assert all(json.loads(chunk.embedding_json)["model"] == embedder.model_id for chunk in chunks)
    assert all("source_url" not in chunk.content for chunk in chunks)


def test_vector_retrieval_finds_program_without_keyword_match_and_keeps_filters(client):
    embedder = FakeEmbeddings()
    with client.app.state.session_factory() as session:
        build_vector_index(session, embedder)
        keyword = search_programs(session, SearchCriteria(q="软件开发", retrieval="keyword"), embedder=embedder)
        vector = search_programs(session, SearchCriteria(q="软件开发", retrieval="vector"), embedder=embedder)
        excluded = search_programs(session, SearchCriteria(q="软件开发", country="英国", retrieval="vector"), embedder=embedder)

    assert keyword["total"] == 0
    assert vector["retrieval_mode"] == "vector"
    assert [item["id"] for item in vector["items"]] == [6]
    assert excluded["total"] == 0
    assert vector["items"][0]["source_url"].startswith("https://")


def test_stale_or_different_model_vectors_are_not_used(client):
    embedder = FakeEmbeddings()
    with client.app.state.session_factory() as session:
        build_vector_index(session, embedder)
        program = session.get(Program, 6)
        program.description = (program.description or "") + " updated"
        session.commit()
        stale = search_programs(session, SearchCriteria(q="软件开发", retrieval="vector"), embedder=embedder)
        build_vector_index(session, embedder)
        refreshed = search_programs(session, SearchCriteria(q="软件开发", retrieval="vector"), embedder=embedder)

    assert stale["total"] == 0
    assert [item["id"] for item in refreshed["items"]] == [6]


def test_embedding_failure_falls_back_to_keyword_results(client):
    embedder = FakeEmbeddings()
    with client.app.state.session_factory() as session:
        build_vector_index(session, embedder)
        embedder.fail = True
        result = search_programs(session, SearchCriteria(q="Software Engineering", retrieval="hybrid"), embedder=embedder)

    assert result["retrieval_mode"] == "keyword"
    assert result["items"]
    assert any(item["id"] == 6 for item in result["items"])


def test_vector_mode_and_invalid_mode_are_exposed_by_program_api(client):
    embedder = FakeEmbeddings()
    with client.app.state.session_factory() as session:
        build_vector_index(session, embedder)
    client.app.state.vector_embedder = embedder

    response = client.get("/api/programs", params={"q": "软件开发", "retrieval": "vector"})
    invalid = client.get("/api/programs", params={"q": "软件开发", "retrieval": "nonsense"})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]["items"]] == [6]
    assert invalid.status_code == 422
