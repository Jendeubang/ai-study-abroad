import asyncio

from app.services.parser_service import parse_with_fallback


class PlaceholderAI:
    async def complete_json(self, _system_prompt, _payload):
        return {
            "countries": ["英国"],
            "degree": "硕士",
            "discipline": "计算机科学",
            "gpa": {"value": "未提及", "scale": 4},
            "language": {"ielts": None, "toefl": None},
            "budget": {"amount": "未提供", "currency": None},
            "intake": None,
            "preferences": [],
        }


def test_ai_parse_treats_incomplete_optional_gpa_and_budget_as_missing():
    parsed, used_ai = asyncio.run(parse_with_fallback("英国计算机硕士", PlaceholderAI()))

    assert used_ai is True
    assert parsed.countries == ["英国"]
    assert parsed.gpa is None
    assert parsed.budget is None


def test_ai_parse_restores_explicit_budget_from_safe_rule_parser():
    parsed, used_ai = asyncio.run(parse_with_fallback("英国计算机硕士预算40万元", PlaceholderAI()))

    assert used_ai is True
    assert parsed.budget is not None
    assert parsed.budget.amount == 400_000
    assert parsed.budget.currency == "CNY"
