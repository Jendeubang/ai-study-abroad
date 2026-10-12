"""Collect a fixed set of public university programme facts for manual review.

Only six explicitly listed HTML pages are fetched. No links are followed, no
access controls are bypassed, and nothing is written to the application DB.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag

from scripts.crawl_official_requirements import (
    CrawlError, ROOT, USER_AGENT, _loopback_proxy, _write_json,
)


MAX_ROBOTS_BYTES = 256_000
MAX_PAGE_BYTES = 2_000_000
MIN_DELAY_SECONDS = 2.0


@dataclass(frozen=True)
class Source:
    key: str
    host: str
    path: str
    university: str
    program_name: str
    discipline: str
    kind: str

    @property
    def url(self) -> str:
        return f"https://{self.host}{self.path}"

    @property
    def robots_url(self) -> str:
        return f"https://{self.host}/robots.txt"


SOURCES = {
    "cityu-cs": Source(
        "cityu-cs", "www.cs.cityu.edu.hk",
        "/academic-programmes/msc-computer-science/admissions",
        "City University of Hong Kong", "Master of Science in Computer Science",
        "计算机科学", "cityu",
    ),
    "cityu-ai": Source(
        "cityu-ai", "www.cs.cityu.edu.hk",
        "/en/academic-programmes/msc-artificial-intelligence/admissions",
        "City University of Hong Kong", "Master of Science in Artificial Intelligence",
        "人工智能", "cityu",
    ),
    "cityu-cybersecurity": Source(
        "cityu-cybersecurity", "www.cs.cityu.edu.hk",
        "/en/academic-programmes/msc-cybersecurity/admissions",
        "City University of Hong Kong", "Master of Science in Cybersecurity",
        "网络安全", "cityu",
    ),
    "hkust-it": Source(
        "hkust-it", "seng.hkust.edu.hk",
        "/academics/taught-postgraduate/msc-it",
        "The Hong Kong University of Science and Technology",
        "MSc in Information Technology", "信息技术", "hkust",
    ),
    "hkust-bdt": Source(
        "hkust-bdt", "seng.hkust.edu.hk",
        "/academics/taught-postgraduate/msc-bdt",
        "The Hong Kong University of Science and Technology",
        "MSc in Big Data Technology", "数据科学", "hkust",
    ),
    "hkust-ai": Source(
        "hkust-ai", "seng.hkust.edu.hk",
        "/academics/taught-postgraduate/msc-ai",
        "The Hong Kong University of Science and Technology",
        "MSc in Artificial Intelligence", "人工智能", "hkust",
    ),
}


def _normal_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _main_and_title(html: str, source: Source) -> Tag:
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main")
    if not isinstance(main, Tag):
        raise CrawlError(f"{source.key}：官网缺少正文区域")
    titles = [_normal_text(tag.get_text(" ", strip=True)) for tag in main.find_all("h1")]
    if titles != [source.program_name]:
        raise CrawlError(f"{source.key}：课程标题与已核准页面不一致")
    return main


def _section_paragraph(main: Tag, heading_text: str) -> str:
    heading = next(
        (tag for tag in main.find_all("h2")
         if _normal_text(tag.get_text(" ", strip=True)).casefold() == heading_text.casefold()),
        None,
    )
    if heading is None:
        raise CrawlError(f"官网缺少预期栏目：{heading_text}")
    for element in heading.next_elements:
        if not isinstance(element, Tag):
            continue
        if element.name in {"h2", "h3"}:
            break
        if element.name == "p":
            paragraph = _normal_text(element.get_text(" ", strip=True))
            if paragraph:
                return paragraph
    raise CrawlError(f"官网栏目缺少正文：{heading_text}")


def parse_cityu_html(html: str, source: Source) -> dict:
    main = _main_and_title(html, source)
    body = _normal_text(main.get_text(" ", strip=True))
    if "Entrance Requirements" not in body or "Non-local Applicants" not in body:
        raise CrawlError(f"{source.key}：招生要求栏目已变更")
    admissions = body.split("Entrance Requirements", 1)[1].split("Non-local Applicants", 1)[0]
    if "bachelor's degree" not in admissions.lower():
        raise CrawlError(f"{source.key}：本科学历要求无法确认")
    match = re.search(
        r"an overall band score of\s+(\d(?:\.\d)?)\s+in International English "
        r"Language Testing System\s*\(IELTS\)",
        admissions, re.IGNORECASE,
    )
    if match is None:
        raise CrawlError(f"{source.key}：雅思总分要求无法确认")
    score = float(match.group(1))
    if not 4 <= score <= 9:
        raise CrawlError(f"{source.key}：雅思总分不在预期范围")
    return {
        "tuition": None, "currency": None, "intake": None,
        "ielts_min": score,
        "evidence": {
            "program_name": source.program_name,
            "ielts_min": match.group(0),
        },
    }


def parse_hkust_html(html: str, source: Source) -> dict:
    main = _main_and_title(html, source)
    fee_text = _section_paragraph(main, "Program Fee")
    if "2027/28" not in fee_text or "full-time" not in fee_text.lower():
        raise CrawlError(f"{source.key}：学费学年或学习方式已变更")
    fee_match = (
        re.search(r"\bfull-time study is HK\$\s*([\d,]+)", fee_text, re.IGNORECASE)
        or re.search(r"\bis HK\$\s*([\d,]+)\s*\(full-time study", fee_text, re.IGNORECASE)
    )
    if fee_match is None:
        raise CrawlError(f"{source.key}：全日制学费无法确认")
    tuition = int(fee_match.group(1).replace(",", ""))
    if not 10_000 <= tuition <= 1_000_000:
        raise CrawlError(f"{source.key}：学费不在预期范围")
    intake_headings = [
        _normal_text(tag.get_text(" ", strip=True))
        for tag in main.find_all("h3")
        if re.search(r"For 2027/28 Fall intake\s*\(September\s+2027\)",
                     _normal_text(tag.get_text(" ", strip=True)), re.IGNORECASE)
    ]
    if len(intake_headings) != 1:
        raise CrawlError(f"{source.key}：入学季无法确认")
    return {
        "tuition": tuition, "currency": "HKD", "intake": "2027 Fall",
        "ielts_min": None,
        "evidence": {
            "program_name": source.program_name,
            "tuition": fee_text[:300],
            "intake": intake_headings[0],
        },
    }


def _fetch(client: httpx.Client, url: str, host: str, content_type: str, limit: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != host or parsed.port is not None:
        raise CrawlError("采集地址不在预设的 HTTPS 官网域名内")
    try:
        with client.stream("GET", url) as response:
            if response.history or response.status_code != 200:
                raise CrawlError(f"官网请求失败（HTTP {response.status_code}），停止采集")
            if not response.headers.get("content-type", "").lower().startswith(content_type):
                raise CrawlError("官网返回了非预期内容类型")
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > limit:
                    raise CrawlError("官网响应超过大小限制")
            return bytes(content)
    except httpx.HTTPError as exc:
        raise CrawlError(f"官网网络请求失败：{type(exc).__name__}") from exc


def crawl(
    client: httpx.Client, sources: Sequence[Source],
    pause: Callable[[float], None] = time.sleep,
) -> dict:
    if not sources or any(SOURCES.get(source.key) != source for source in sources):
        raise CrawlError("只能采集脚本内已核准的课程页面")
    policies: dict[str, tuple[RobotFileParser, float]] = {}
    records = []
    for source in sources:
        if source.host not in policies:
            robots = _fetch(client, source.robots_url, source.host, "text/plain", MAX_ROBOTS_BYTES)
            try:
                rules = robots.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise CrawlError("robots.txt 编码无法确认") from exc
            if not re.search(r"(?im)^user-agent\s*:", rules):
                raise CrawlError("robots.txt 缺少可识别规则")
            policy = RobotFileParser()
            policy.parse(rules.splitlines())
            rate = policy.request_rate(USER_AGENT)
            delay = max(
                MIN_DELAY_SECONDS, float(policy.crawl_delay(USER_AGENT) or 0),
                float(rate.seconds / rate.requests) if rate and rate.requests else 0,
            )
            policies[source.host] = policy, delay
        policy, delay = policies[source.host]
        if not policy.can_fetch(USER_AGENT, source.url):
            raise CrawlError(f"robots.txt 不允许采集 {source.key}")
        pause(delay)
        page = _fetch(client, source.url, source.host, "text/html", MAX_PAGE_BYTES)
        try:
            html = page.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CrawlError(f"{source.key}：页面编码无法确认") from exc
        fact = parse_cityu_html(html, source) if source.kind == "cityu" else parse_hkust_html(html, source)
        records.append({
            "source_id": source.key, "university": source.university,
            "country": "香港", "city": "香港", "program_name": source.program_name,
            "degree": "硕士", "discipline": source.discipline, **fact,
            "source_url": source.url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_sha256": hashlib.sha256(page).hexdigest(),
        })
    return {
        "schema_version": 1,
        "manual_review_required": True,
        "usage": "local_non_commercial_research",
        "robots_urls": sorted({source.robots_url for source in sources}),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="采集港城大与港科大预设课程，供人工核对")
    parser.add_argument("--course", choices=["all", *SOURCES], default="all")
    parser.add_argument("--proxy", type=_loopback_proxy, help="可选本机代理，如 http://127.0.0.1:3067")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "data" / "official_requirements" / "additional_latest.json",
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
    print(f"已采集 {len(result['records'])} 个课程，待人工核对：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
