from app.sources.parsers.common import ParsedSourceRecord, parse_candidate_page


def parse(source, html: str) -> ParsedSourceRecord:
    return parse_candidate_page(
        source, html, required_sections=("Entry requirements", "Fees and funding"),
    )
