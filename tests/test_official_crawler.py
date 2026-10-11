from importlib import import_module

import httpx
import pytest


HTML = """
<main>
  <h1>MPhil in Advanced Computer Science</h1>
  <h2>Expected Academic Standard</h2>
  <p>Applicants for this course should have achieved a <strong>First class Honours Degree</strong>.</p>
  <h2>Language Requirement</h2>
  <section><h3>IELTS (Academic)</h3><table>
    <tr><th>Element</th><th>Score</th></tr>
    <tr><td>Listening</td><td>7.0</td></tr><tr><td>Writing</td><td>7.0</td></tr>
    <tr><td>Reading</td><td>7.0</td></tr><tr><td>Speaking</td><td>7.0</td></tr>
    <tr><td>Total</td><td>7.5</td></tr>
  </table></section>
  <section><h3>TOEFL Internet Score</h3><table>
    <tr><th>Element</th><th>Score</th></tr>
    <tr><td>Listening</td><td>25</td></tr><tr><td>Writing</td><td>25</td></tr>
    <tr><td>Reading</td><td>25</td></tr><tr><td>Speaking</td><td>25</td></tr>
    <tr><td>Total</td><td>107</td></tr>
  </table></section>
</main>
"""


def crawler_module():
    return import_module("scripts.crawl_official_requirements")


def test_extracts_only_scoped_requirement_facts():
    crawler = crawler_module()
    record = crawler.parse_cambridge_html(HTML, crawler.SOURCES["cambridge-acs"])

    assert record["university"] == "University of Cambridge"
    assert record["academic_standard"] == "First class Honours Degree"
    assert record["ielts"] == {
        "overall": 7.5, "listening": 7.0, "writing": 7.0,
        "reading": 7.0, "speaking": 7.0,
    }
    assert record["toefl"]["overall"] == 107
    assert record["subject_requirement_needs_review"] is True
    assert "gpa_min" not in record


def test_robots_unavailable_stops_before_course_request():
    crawler = crawler_module()
    visited = []

    def respond(request):
        visited.append(request.url.path)
        return httpx.Response(403, text="blocked")

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(crawler.CrawlError, match="robots.txt"):
            crawler.crawl(client, [crawler.SOURCES["cambridge-acs"]], pause=lambda _: None)

    assert visited == ["/robots.txt"]


def test_robots_disallow_stops_before_course_request():
    crawler = crawler_module()
    visited = []

    def respond(request):
        visited.append(request.url.path)
        return httpx.Response(
            200, text="User-agent: *\nDisallow: /courses/\n",
            headers={"content-type": "text/plain"},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(crawler.CrawlError, match="不允许"):
            crawler.crawl(client, [crawler.SOURCES["cambridge-acs"]], pause=lambda _: None)

    assert visited == ["/robots.txt"]


def test_crawl_records_source_without_copying_full_page():
    crawler = crawler_module()
    visited = []
    delays = []

    def respond(request):
        visited.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /courses/\nCrawl-delay: 3\n",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(200, text=HTML, headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = crawler.crawl(
            client, [crawler.SOURCES["cambridge-acs"]], pause=delays.append,
        )

    assert visited == [
        "/robots.txt", "/courses/directory/cscsmpacs/requirements",
    ]
    assert delays == [3]
    assert result["records"][0]["ielts"]["overall"] == 7.5
    assert result["records"][0]["source_url"].startswith("https://www.postgraduate.study.cam.ac.uk/")
    assert "Applicants for this course" not in str(result)
    assert result["manual_review_required"] is True


def test_changed_page_is_rejected_instead_of_guessing():
    crawler = crawler_module()
    with pytest.raises(crawler.CrawlError, match="学历|语言"):
        crawler.parse_cambridge_html("<h1>MPhil in Advanced Computer Science</h1>", crawler.SOURCES["cambridge-acs"])


def test_academic_value_cannot_leak_from_another_section():
    crawler = crawler_module()
    misleading = HTML.replace(
        "<p>Applicants for this course should have achieved a <strong>First class Honours Degree</strong>.</p>",
        "<h2>Other information</h2><p>First class Honours Degree</p>",
    )
    with pytest.raises(crawler.CrawlError, match="学历"):
        crawler.parse_cambridge_html(misleading, crawler.SOURCES["cambridge-acs"])


def test_language_scores_are_scoped_to_language_section():
    crawler = crawler_module()
    misleading = HTML.replace(
        "<h2>Expected Academic Standard</h2>",
        "<h3>IELTS (Academic)</h3><table><tr><td>Total</td><td>6.0</td></tr></table>"
        "<h2>Expected Academic Standard</h2>",
    )
    record = crawler.parse_cambridge_html(misleading, crawler.SOURCES["cambridge-acs"])
    assert record["ielts"]["overall"] == 7.5
