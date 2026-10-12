from app.config import Settings


def test_default_ai_timeout_allows_deepseek_reason_generation():
    assert Settings.__dataclass_fields__["ai_timeout_seconds"].default == 25.0


def test_default_request_timeout_covers_two_sequential_ai_calls():
    assert Settings.__dataclass_fields__["request_timeout_seconds"].default == 60.0
