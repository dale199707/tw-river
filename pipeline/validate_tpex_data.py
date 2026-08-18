#!/usr/bin/env python3
"""Validate the daily TPEX snapshot against its YTD ingestion checkpoint."""

from __future__ import annotations

import json
import math
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATE_RE = re.compile(r"^\d{8}$")
MIN_COMPANIES = 800
MIN_QUOTES = 800


def reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf8"), parse_constant=reject_constant)


def find_non_finite(value, path=""):
    if isinstance(value, float) and not math.isfinite(value):
        yield path or "<root>"
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from find_non_finite(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from find_non_finite(item, f"{path}[{index}]")


def main() -> int:
    errors: list[str] = []
    try:
        snap = load_json(DATA / "tpex_snap.json")
        ytd = load_json(DATA / "tpex_ytd.json")
    except Exception as exc:
        print(f"[tpex-health] FAIL: cannot load snapshot/YTD: {exc}")
        return 1

    companies = snap.get("companies")
    quotes = snap.get("q")
    if not isinstance(companies, list):
        errors.append("tpex_snap companies must be a list")
        companies = []
    if not isinstance(quotes, dict):
        errors.append("tpex_snap q must be an object")
        quotes = {}
    if len(companies) < MIN_COMPANIES:
        errors.append(f"companies {len(companies)} < {MIN_COMPANIES}")
    if len(quotes) < MIN_QUOTES:
        errors.append(f"quotes {len(quotes)} < {MIN_QUOTES}")

    company_codes = [str(item.get("c") or "") for item in companies if isinstance(item, dict)]
    if len(company_codes) != len(set(company_codes)):
        errors.append("duplicate company codes in tpex snapshot")
    unknown_quotes = sorted(set(quotes) - set(company_codes))
    if unknown_quotes:
        errors.append(f"quote codes missing from companies: {unknown_quotes[:5]}")

    snap_date_text = str(snap.get("date") or "")
    ytd_last_text = str(ytd.get("last") or "")
    snap_date = None
    ytd_last = None
    try:
        if not DATE_RE.fullmatch(snap_date_text):
            raise ValueError(snap_date_text)
        snap_date = datetime.strptime(snap_date_text, "%Y%m%d").date()
    except ValueError:
        errors.append(f"invalid snapshot date: {snap_date_text!r}")
    try:
        ytd_last = date.fromisoformat(ytd_last_text)
    except ValueError:
        errors.append(f"invalid YTD last date: {ytd_last_text!r}")
    if snap_date and ytd_last and snap_date != ytd_last:
        errors.append(f"snapshot date {snap_date} != YTD checkpoint {ytd_last}")

    ratio_date_text = str(snap.get("ratioDate") or snap_date_text)
    try:
        if not DATE_RE.fullmatch(ratio_date_text):
            raise ValueError(ratio_date_text)
        ratio_date = datetime.strptime(ratio_date_text, "%Y%m%d").date()
        if snap_date and ratio_date > snap_date:
            errors.append(f"ratio date {ratio_date} is newer than snapshot {snap_date}")
    except ValueError:
        errors.append(f"invalid ratio date: {ratio_date_text!r}")

    close_count = sum(
        1 for item in quotes.values()
        if isinstance(item, dict) and isinstance(item.get("close"), (int, float))
    )
    if close_count < MIN_QUOTES:
        errors.append(f"quotes with close {close_count} < {MIN_QUOTES}")
    for location in find_non_finite(snap):
        errors.append(f"snapshot non-finite number at {location}")
    for location in find_non_finite(ytd):
        errors.append(f"YTD non-finite number at {location}")

    print(f"[tpex-health] companies: {len(companies)}")
    print(f"[tpex-health] quotes: {len(quotes)} (close: {close_count})")
    print(f"[tpex-health] price date: {snap_date_text}; ratio date: {ratio_date_text}; YTD last: {ytd_last_text}")
    if errors:
        print(f"[tpex-health] FAIL: {len(errors)} error(s)")
        for error in errors[:30]:
            print(f"  - {error}")
        return 1
    print("[tpex-health] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
