from __future__ import annotations

import argparse
import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import httpx  # type: ignore[import-not-found]
from bs4 import BeautifulSoup

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from export_utils import generate_job_id, write_jobs_csv  # noqa: E402

AMAZON_DIR = Path(__file__).resolve().parent
DEFAULT_URL = "https://www.amazon.jobs/en/search?base_query="
DEFAULT_RAW_PATH = AMAZON_DIR / "data" / "jobs_raw.json"
DEFAULT_CSV_PATH = AMAZON_DIR / "jobs.csv"
DEFAULT_TIMEOUT = 30.0
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"
)

FetchPage = Callable[[str], str]


def _rel_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT_DIR)
    except ValueError:
        return path


@dataclass
class AmazonListPage:
    jobs: List[dict[str, str]]
    has_next: bool
    page_size: int


@dataclass
class JobDetail:
    title: str
    ats_id: str
    site: str


def build_page_url(base_url: str, start: int) -> str:
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["start"] = [str(start)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _extract_location_text(job_el) -> str:
    block = job_el.select_one(".location-and-id")
    if not block:
        return ""
    values: List[str] = []
    for li in block.select("ul li"):
        text = li.get_text(strip=True)
        if not text or text == "|":
            continue
        if text.lower().startswith("job id"):
            break
        if text not in values:
            values.append(text)
    return ", ".join(values)


def _parse_search_meta(soup: BeautifulSoup) -> tuple[int | None, int | None]:
    container = soup.find("div", {"data-react-class": "SearchContent"})
    if not container:
        return None, None
    props_raw = container.get("data-react-props")
    if not isinstance(props_raw, str):
        return None, None
    try:
        decoded = html.unescape(props_raw)
        parsed = json.loads(decoded)
        request_raw = parsed.get("job_posting_search_request")
        if not request_raw:
            return None, None
        request = json.loads(request_raw).get("jobPostingSearchRequest", {})
        start_val = request.get("start")
        size_val = request.get("size")

        def _to_int(value) -> int | None:
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
            return None

        return _to_int(start_val), _to_int(size_val)
    except json.JSONDecodeError:
        return None, None


def _has_next_page(soup: BeautifulSoup) -> bool:
    button = soup.select_one(".pagination-control .btn.circle.right")
    if not button:
        return False
    raw_classes = button.get("class")
    if isinstance(raw_classes, list):
        classes = [str(value) for value in raw_classes]
    elif isinstance(raw_classes, str):
        classes = [raw_classes]
    else:
        classes = []
    if "disabled" in classes:
        return False
    if button.get("aria-disabled") == "true":
        return False
    return True


def parse_list_page(html_text: str, base_url: str) -> AmazonListPage:
    soup = BeautifulSoup(html_text, "html.parser")
    job_elements = soup.select(".job-tile div.job")
    jobs: List[dict[str, str]] = []
    for job_el in job_elements:
        data_job_id = job_el.get("data-job-id")
        ats_id = data_job_id.strip() if isinstance(data_job_id, str) else ""
        link = job_el.select_one("h3.job-title a")
        if not link:
            continue
        href_value = link.get("href")
        href = href_value.strip() if isinstance(href_value, str) else ""
        url = urljoin(base_url, href)
        title = link.get_text(strip=True)
        location = _extract_location_text(job_el)
        job_record = {
            "url": url,
            "title": title,
            "location": location,
            "company": "Amazon",
            "ats_id": ats_id,
        }
        jobs.append(job_record)
    _, page_size = _parse_search_meta(soup)
    has_next = _has_next_page(soup)
    if page_size is None:
        page_size = len(jobs)
    return AmazonListPage(jobs=jobs, has_next=has_next, page_size=page_size or len(jobs))


def parse_job_detail(html_text: str) -> JobDetail:
    soup = BeautifulSoup(html_text, "html.parser")
    title_el = soup.select_one(".apply-header .title")
    meta_el = soup.select_one(".apply-header .details-line .meta")
    title = title_el.get_text(strip=True) if title_el else ""
    ats_id = ""
    site = ""
    if meta_el:
        parts = [part.strip() for part in meta_el.get_text("|", strip=True).split("|")]
        for part in parts:
            lower = part.lower()
            if lower.startswith("job id"):
                ats_id = part.split(":", 1)[1].strip() if ":" in part else part.replace("Job ID", "").strip()
            elif not site:
                site = part
    return JobDetail(title=title, ats_id=ats_id, site=site)


def build_csv_rows(jobs: Iterable[dict[str, str]]) -> List[dict[str, str]]:
    rows: List[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for job in jobs:
        url = job.get("url", "")
        ats_id = job.get("ats_id", "")
        key = (ats_id, url)
        if not any(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        job_id = generate_job_id("amazon", url, ats_id)
        rows.append({
            "url": url,
            "title": job.get("title", ""),
            "location": job.get("location", ""),
            "company": job.get("company", "Amazon"),
            "ats_id": ats_id,
            "id": job_id,
        })
    return rows


def _scrape_with_fetch(base_url: str, fetch_page: FetchPage, max_pages: int) -> List[dict[str, str]]:
    jobs: List[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    start = 0
    page_index = 0
    while True:
        page_url = build_page_url(base_url, start)
        html_text = fetch_page(page_url)
        page = parse_list_page(html_text, base_url)
        if not page.jobs:
            break
        for job in page.jobs:
            key = (job.get("ats_id", ""), job.get("url", ""))
            if not any(key):
                continue
            if key in seen:
                continue
            seen.add(key)
            jobs.append(job)
        page_index += 1
        if not page.has_next:
            break
        if max_pages and page_index >= max_pages:
            break
        increment = page.page_size or len(page.jobs)
        if increment <= 0:
            break
        start += increment
    return jobs


def scrape_listings(
    base_url: str,
    *,
    max_pages: int = 0,
    timeout: float = DEFAULT_TIMEOUT,
    fetch_page: FetchPage | None = None,
) -> List[dict[str, str]]:
    if fetch_page is not None:
        return _scrape_with_fetch(base_url, fetch_page, max_pages)

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }

    with httpx.Client(headers=headers, follow_redirects=True) as client:
        def _fetch(url: str) -> str:
            response = client.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text

        return _scrape_with_fetch(base_url, _fetch, max_pages)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Amazon Jobs listings")
    parser.add_argument("--url", default=DEFAULT_URL, help="Search URL to start from")
    parser.add_argument("--raw", default=str(DEFAULT_RAW_PATH), help="Path to store raw jobs JSON")
    parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH), help="CSV output path (optional immediate export)")
    parser.add_argument("--max-pages", type=int, default=0, help="Limit the number of pages to fetch (0 = all)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout per request in seconds")
    parser.add_argument("--write-csv", action="store_true", help="Write CSV immediately instead of using export_to_csv.py")
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    jobs = scrape_listings(args.url, max_pages=max(0, args.max_pages), timeout=args.timeout)
    print(f"Fetched {len(jobs)} jobs from Amazon")

    raw_path = Path(args.raw)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(jobs, indent=2))
    print(f"Saved raw data to {_rel_path(raw_path)}")

    if args.write_csv:
        rows = build_csv_rows(jobs)
        csv_path = Path(args.csv)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path = write_jobs_csv(csv_path, rows)
        rel_csv = _rel_path(csv_path)
        print(f"Wrote {len(rows)} rows to {rel_csv}")
        if diff_path:
            rel_diff = _rel_path(diff_path)
            print(f"Diff written to {rel_diff}")


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
