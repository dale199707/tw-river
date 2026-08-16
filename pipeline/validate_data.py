#!/usr/bin/env python3
"""Validate committed financial JSON before automated publication."""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
FIN = DATA / "fin"
QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")
MIN_SCREEN_ROWS = 2000
MIN_FIN_FILES = 2500
MIN_LATEST_COVERAGE = 1500


def reject_constant(value: str):
    raise ValueError(f"invalid JSON constant: {value}")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf8"), parse_constant=reject_constant)


def quarter_no(value: str) -> int:
    if not QUARTER_RE.fullmatch(value):
        raise ValueError(f"invalid quarter: {value}")
    return int(value[:4]) * 4 + int(value[-1]) - 1


def expected_quarter(today: date) -> str:
    year = today.year
    if today >= date(year, 11, 16):
        return f"{year}Q3"
    if today >= date(year, 8, 16):
        return f"{year}Q2"
    if today >= date(year, 5, 16):
        return f"{year}Q1"
    if today >= date(year, 4, 16):
        return f"{year - 1}Q4"
    return f"{year - 1}Q3"


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
        screen = load_json(DATA / "screen.json")
        status = load_json(DATA / "status.json")
    except Exception as exc:
        print(f"[health] FAIL: cannot load screen/status: {exc}")
        return 1

    rows = screen.get("rows")
    if not isinstance(rows, list):
        print("[health] FAIL: screen.json rows must be a list")
        return 1
    if len(rows) < MIN_SCREEN_ROWS:
        errors.append(f"screen rows {len(rows)} < {MIN_SCREEN_ROWS}")

    codes: set[str] = set()
    quarters = Counter()
    row_by_code = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"screen row {index} is not an object")
            continue
        code = str(row.get("c") or "")
        quarter = str(row.get("q") or "")
        if code in codes:
            errors.append(f"duplicate screen code: {code}")
        codes.add(code)
        row_by_code[code] = row
        if not QUARTER_RE.fullmatch(quarter):
            errors.append(f"{code}: invalid screen quarter {quarter!r}")
        else:
            quarters[quarter] += 1
        for location in find_non_finite(row):
            errors.append(f"screen {code}: non-finite number at {location}")

    latest = max(quarters, key=quarter_no) if quarters else None
    latest_count = quarters.get(latest, 0)
    if latest_count < MIN_LATEST_COVERAGE:
        errors.append(f"latest-quarter coverage {latest_count} < {MIN_LATEST_COVERAGE}")
    if status.get("latestQuarter") != latest:
        errors.append(
            f"status latestQuarter {status.get('latestQuarter')!r} != screen latest {latest!r}"
        )
    if status.get("updated") != screen.get("updated"):
        errors.append("status.json updated date != screen.json updated date")

    expected = expected_quarter(date.today())
    if latest and quarter_no(latest) < quarter_no(expected):
        errors.append(f"latest quarter {latest} is older than scheduled {expected}")

    fin_paths = sorted(FIN.glob("*.json"))
    if len(fin_paths) < MIN_FIN_FILES:
        errors.append(f"financial files {len(fin_paths)} < {MIN_FIN_FILES}")

    fin_latest = {}
    eps_rank = []
    for path in fin_paths:
        try:
            obj = load_json(path)
        except Exception as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        code = path.stem
        if str(obj.get("code") or "") != code:
            errors.append(f"{path.name}: code field does not match filename")
        qmap = obj.get("q")
        if not isinstance(qmap, dict):
            errors.append(f"{path.name}: q must be an object")
            continue
        bad_quarters = [q for q in qmap if not QUARTER_RE.fullmatch(q)]
        if bad_quarters:
            errors.append(f"{path.name}: invalid quarter keys {bad_quarters[:3]}")
            continue
        if qmap:
            q_latest = max(qmap, key=quarter_no)
            fin_latest[code] = q_latest
            latest_row = qmap.get(q_latest) or {}
            eps = latest_row.get("eps")
            if isinstance(eps, (int, float)) and math.isfinite(eps):
                eps_rank.append((abs(eps), code, q_latest, eps))
                if abs(eps) > 100000:
                    errors.append(f"{path.name}: implausible latest EPS {eps}")
        if "div" in obj and not isinstance(obj["div"], list):
            errors.append(f"{path.name}: div must be a list")
        for location in find_non_finite(obj):
            errors.append(f"{path.name}: non-finite number at {location}")

    for code, row in row_by_code.items():
        actual = fin_latest.get(code)
        if actual is None:
            errors.append(f"{code}: listed in screen but missing financial quarters")
        elif actual != row.get("q"):
            errors.append(f"{code}: screen quarter {row.get('q')} != fin quarter {actual}")

    ordered_quarters = sorted(quarters.items(), key=lambda item: quarter_no(item[0]), reverse=True)
    print(f"[health] screen rows: {len(rows)}")
    print(f"[health] financial files parsed: {len(fin_paths)}")
    print(f"[health] latest quarter: {latest} ({latest_count} rows), scheduled: {expected}")
    print("[health] quarter distribution: " + ", ".join(f"{q}={n}" for q, n in ordered_quarters[:12]))
    if eps_rank:
        top = sorted(eps_rank, reverse=True)[:5]
        print("[health] largest latest |EPS|: " + ", ".join(f"{c} {q} {eps:g}" for _, c, q, eps in top))

    if errors:
        print(f"[health] FAIL: {len(errors)} error(s)")
        for error in errors[:40]:
            print(f"  - {error}")
        if len(errors) > 40:
            print(f"  - ... and {len(errors) - 40} more")
        return 1

    print("[health] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
