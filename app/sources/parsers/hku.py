from app.sources.parsers.common import ParsedSourceRecord, parse_candidate_page


def parse(source, html: str) -> ParsedSourceRecord:
    required = ("Application procedure", "Application") if source.source_id.endswith("application") else (
        "Admission Requirements", "English Language Requirements", "Tuition Fees",
    )
    return parse_candidate_page(source, html, required_sections=required)
