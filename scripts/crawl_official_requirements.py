"""Collect a few factual admission requirements from approved public course pages.

This script is for local, non-commercial research. It does not crawl links,
copy full pages, contact application portals, or write to the application DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag


ROOT = Path(__file__).resolve().parent.parent
HOST = "www.postgraduate.study.cam.ac.uk"
ORIGIN = f"https://{HOST}"
ROBOTS_URL = f"{ORIGIN}/robots.txt"
TERMS_URL = "https://www.cam.ac.uk/about-this-site/terms-and-conditions"
USER_AGENT = "StudyCompassAI/0.1"
MAX_ROBOTS_BYTES = 256_000
MAX_PAGE_BYTES = 2_000_000
MIN_DELAY_SECONDS = 2.0


class CrawlError(RuntimeError):
    """A source cannot be fetched or parsed safely."""


@dataclass(frozen=True)
class Source:
    key: str
    program_name: str
    path: str

    @property
    def url(self) -> str:
        return ORIGIN + self.path


SOURCES = {
    "cambridge-acs": Source(
        "cambridge-acs", "MPhil in Advanced Computer Science",
        "/courses/directory/cscsmpacs/requirements",
    ),
    "cambridge-scientific-computing": Source(
        "cambridge-scientific-computing", "MPhil in Scientific Computing",
        "/courses/directory/pcphmpscm/requirements",
    ),
}


def _heading(soup: BeautifulSoup, tag: str, title: str) -> Tag:
    for candidate in soup.find_all(tag):
        if candidate.get_text(" ", strip=True).casefold() == title.casefold():
            return candidate
    raise CrawlError(f"官网页面缺少预期的学历或语言栏目：{title}")


def _following_table(heading: Tag) -> Tag:
    for candidate in heading.next_elements:
        if not isinstance(candidate, Tag):
            continue
        if candidate.name in {"h2", "h3"}:
            break
        if candidate.name == "table":
            return candidate
    raise CrawlError("官网语言要求表格已变更，未生成猜测值")


def _following_paragraph(heading: Tag) -> Tag:
    for candidate in heading.next_elements:
        if not isinstance(candidate, Tag):
            continue
        if candidate.name == "h2":
            break
        if candidate.name == "p":
            return candidate
    raise CrawlError("官网学历要求缺失")


def _heading_in_section(section: Tag, title: str) -> Tag:
    for candidate in section.next_elements:
        if not isinstance(candidate, Tag):
            continue
        if candidate.name == "h2":
            break
        if candidate.name == "h3" and candidate.get_text(" ", strip=True).casefold() == title.casefold():
            return candidate
    raise CrawlError(f"官网语言栏目缺少预期考试要求：{title}")


def _scores(section: Tag, title: str, maximum: float) -> dict[str, float | int]:
    table = _following_table(_heading_in_section(section, title))
    found: dict[str, float | int] = {}
    allowed = {"listening", "writing", "reading", "speaking", "total"}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True).casefold()
        if label not in allowed:
            continue
        score_text = cells[1].get_text(" ", strip=True)
        if not re.fullmatch(r"\d{1,3}(?:\.\d)?", score_text):
            raise CrawlError(f"{title} 分数格式已变更，未生成猜测值")
        score = float(score_text)
        if score <= 0 or score > maximum:
            raise CrawlError(f"{title} 分数超出合理范围")
        found["overall" if label == "total" else label] = (
            int(score) if score.is_integer() and maximum == 120 else score
        )
    required = {"overall", "listening", "writing", "reading", "speaking"}
    if set(found) != required:
        raise CrawlError(f"{title} 要求字段不完整，未生成猜测值")
    return found


def parse_cambridge_html(html: str, source: Source) -> dict:
    """Extract only explicit facts; never infer a general GPA from UK grades."""
    soup = BeautifulSoup(html, "html.parser")
    titles = {heading.get_text(" ", strip=True) for heading in soup.find_all("h1")}
    if source.program_name not in titles:
        raise CrawlError("官网课程标题与预设来源不一致")
    academic = _heading(soup, "h2", "Expected Academic Standard")
    paragraph = _following_paragraph(academic)
    match = re.search(
        r"\b(First class|High II\.i) Honours Degree\b",
        paragraph.get_text(" ", strip=True), re.IGNORECASE,
    )
    if match is None:
        raise CrawlError("官网学历要求格式已变更，未生成猜测值")
    language_section = _heading(soup, "h2", "Language Requirement")
    return {
        "source_id": source.key,
        "university": "University of Cambridge",
        "program_name": source.program_name,
        "degree": "MPhil",
        "academic_standard": match.group(0),
        "ielts": _scores(language_section, "IELTS (Academic)", 9),
        "toefl": _scores(language_section, "TOEFL Internet Score", 120),
        "subject_requirement_needs_review": True,
    }


def _fetch(client: httpx.Client, url: str, expected_type: str, byte_limit: int) -> bytes:
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != HOST:
        raise CrawlError("来源不在已核准的大学官网域名内")
    try:
        with client.stream("GET", url) as response:
            if response.history or response.status_code != 200:
                raise CrawlError(f"官网请求失败（HTTP {response.status_code}）；不会绕过访问限制")
            content_type = response.headers.get("content-type", "").lower()
            if not content_type.startswith(expected_type):
                raise CrawlError("官网返回了非预期的内容类型")
            chunks = bytearray()
            for chunk in response.iter_bytes():
                chunks.extend(chunk)
                if len(chunks) > byte_limit:
                    raise CrawlError("官网响应超过大小限制")
            return bytes(chunks)
    except httpx.HTTPError as exc:
        raise CrawlError(f"官网网络请求失败：{type(exc).__name__}") from exc


def crawl(
    client: httpx.Client,
    sources: Sequence[Source],
    pause: Callable[[float], None] = time.sleep,
) -> dict:
    """Check robots first, then fetch a small fixed allowlist of public pages."""
    if not sources or any(source.key not in SOURCES or SOURCES[source.key] != source for source in sources):
        raise CrawlError("只能采集脚本内已核准的官方课程页面")
    try:
        robots = _fetch(client, ROBOTS_URL, "text/plain", MAX_ROBOTS_BYTES)
    except CrawlError as exc:
        raise CrawlError(f"无法确认 robots.txt：{exc}") from exc
    try:
        robots_text = robots.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CrawlError("robots.txt 编码无法确认") from exc
    if not re.search(r"(?im)^user-agent\s*:", robots_text):
        raise CrawlError("robots.txt 缺少可识别规则，停止采集")
    policy = RobotFileParser()
    policy.parse(robots_text.splitlines())
    rate = policy.request_rate(USER_AGENT)
    delay = max(
        MIN_DELAY_SECONDS,
        float(policy.crawl_delay(USER_AGENT) or 0),
        float(rate.seconds / rate.requests) if rate and rate.requests else 0,
    )
    records = []
    for source in sources:
        if not policy.can_fetch(USER_AGENT, source.url):
            raise CrawlError(f"robots.txt 不允许抓取 {source.key}")
        pause(delay)
        page = _fetch(client, source.url, "text/html", MAX_PAGE_BYTES)
        try:
            html = page.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CrawlError("官网页面编码无法确认") from exc
        record = parse_cambridge_html(html, source)
        record.update({
            "source_url": source.url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": hashlib.sha256(page).hexdigest(),
        })
        records.append(record)
    return {
        "schema_version": 1,
        "manual_review_required": True,
        "usage": "local_non_commercial_research",
        "robots_url": ROBOTS_URL,
        "terms_url": TERMS_URL,
        "records": records,
    }


def _loopback_proxy(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("代理端口无效") from exc
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or not port or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise argparse.ArgumentTypeError("代理仅支持 http://127.0.0.1:端口")
    return value


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".crawl-",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description="谨慎采集剑桥大学公开课程的招生要求事实")
    parser.add_argument("--course", choices=["all", *SOURCES], default="all")
    parser.add_argument("--proxy", type=_loopback_proxy, help="可选本地代理，如 http://127.0.0.1:3067")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data" / "official_requirements" / "latest.json",
    )
    args = parser.parse_args()
    selected = list(SOURCES.values()) if args.course == "all" else [SOURCES[args.course]]
    try:
        with httpx.Client(
            proxy=args.proxy, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(15, connect=8),
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"},
        ) as client:
            result = crawl(client, selected)
        _write_json(args.output, result)
    except (CrawlError, OSError) as exc:
        parser.exit(1, f"采集停止：{exc}\n")
    print(f"已采集 {len(result['records'])} 个官方课程，待人工核验：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
