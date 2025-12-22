from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from export_utils import write_jobs_csv  # noqa: E402
from amazon.main import DEFAULT_CSV_PATH, DEFAULT_RAW_PATH, build_csv_rows  # noqa: E402


def _rel_path(path: Path) -> Path:
    try:
        return path.relative_to(ROOT_DIR)
    except ValueError:
        return path


def _load_jobs(raw_path: Path) -> List[dict[str, str]]:
    if not raw_path.exists():
        raise SystemExit(f"No raw jobs file found at {_rel_path(raw_path)}; run amazon/main.py first.")

    data = json.loads(raw_path.read_text())
    if not isinstance(data, list):
        raise SystemExit(f"Raw jobs file {_rel_path(raw_path)} must contain a list of job objects.")

    jobs: List[dict[str, str]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        company_value = entry.get("company")
        if company_value in (None, ""):
            company_value = "Amazon"
        jobs.append({
            "url": str(entry.get("url", "")),
            "title": str(entry.get("title", "")),
            "location": str(entry.get("location", "")),
            "company": str(company_value),
            "ats_id": str(entry.get("ats_id", "")),
        })
    return jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Amazon raw jobs JSON into CSV")
    parser.add_argument("--raw", default=str(DEFAULT_RAW_PATH), help="Path to amazon raw jobs JSON")
    parser.add_argument("--csv", default=str(DEFAULT_CSV_PATH), help="Destination CSV path")
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    raw_path = Path(args.raw)
    csv_path = Path(args.csv)
    jobs = _load_jobs(raw_path)
    rows = build_csv_rows(jobs)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path = write_jobs_csv(csv_path, rows)
    print(f"Wrote {len(rows)} rows to {_rel_path(csv_path)}")
    if diff_path:
        print(f"Diff written to {_rel_path(diff_path)}")


def main() -> None:
    run(parse_args())


if __name__ == "__main__":
    main()
