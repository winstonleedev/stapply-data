from __future__ import annotations

from pathlib import Path

from amazon.main import (
    DEFAULT_URL,
    build_page_url,
    parse_job_detail,
    parse_list_page,
    scrape_listings,
)

SECOND_PAGE_HTML = """
<html>
  <body>
    <div class="job-tile">
      <div class="job" data-job-id="999900">
        <div class="info">
          <h3 class="job-title">
            <a href="https://www.amazon.jobs/en/jobs/999900/second-page-role">
              Second Page Role
            </a>
          </h3>
          <div class="location-and-id">
            <span>
              <legend class="d-none">Locations</legend>
              <ul class="list-unstyled">
                <li>Seattle, WA, USA</li>
                <li>|</li>
                <li>Job ID: 999900</li>
              </ul>
            </span>
          </div>
        </div>
      </div>
    </div>
    <div class="pagination-control">
      <button class="btn circle right disabled" aria-disabled="true"></button>
    </div>
  </body>
</html>
"""


def _load_sample(name: str) -> str:
    target = Path(__file__).with_name(name)
    return target.read_text()


def test_parse_list_and_detail_samples() -> None:
    list_html = _load_sample("amazon-list.html")
    page = parse_list_page(list_html, DEFAULT_URL)
    assert page.has_next is True
    assert len(page.jobs) == 10

    first_job = page.jobs[0]
    assert first_job["ats_id"] == "3111154"
    assert "Transportation" in first_job["title"]

    detail_html = _load_sample("amazon-detail.html")
    detail = parse_job_detail(detail_html)
    assert detail.ats_id == first_job["ats_id"]
    assert detail.title.startswith("Transportation Specialist")


def test_scrape_listings_fetches_second_page() -> None:
    base_url = DEFAULT_URL
    first_html = _load_sample("amazon-list.html")
    second_html = SECOND_PAGE_HTML

    requests = {
        build_page_url(base_url, 0): first_html,
        build_page_url(base_url, 10): second_html,
    }

    def fake_fetch(url: str) -> str:
        if url not in requests:
            raise AssertionError(f"Unexpected URL requested: {url}")
        return requests[url]

    jobs = scrape_listings(base_url, fetch_page=fake_fetch)
    assert len(jobs) == 11
    assert jobs[-1]["ats_id"] == "999900"
    assert jobs[-1]["location"] == "Seattle, WA, USA"
