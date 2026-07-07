#!/usr/bin/env python3
"""
tw-river 財報資料管線
資料來源：公開資訊觀測站（mopsov.twse.com.tw）

彙總表（全上市公司，每季 2 個請求）：
  ajax_t163sb04  綜合損益彙總      -> 營收/毛利/營益/淨利/EPS（年初至當季累計）
  ajax_t163sb05  資產負債彙總      -> 資產/負債/權益/每股淨值（期末時點）
個別公司（每檔每季 1 個請求）：
  server-java/t164sb01             -> 存貨/應收/應付/不動產廠房設備/現金
                                      折舊/營業現金流/資本支出（現金流量為累計）

輸出：data/fin/{code}.json（單行 compact JSON）
      data/fin_progress.json（detail 回補進度，可斷點續傳）

用法：
  python3 fetch_mops.py --bulk-backfill 2018 2026     回補彙總表
  python3 fetch_mops.py --detail-backfill 2018 2026 --limit 1200
                                                      回補個別公司（分批，自動續傳）
  python3 fetch_mops.py --detail-codes 2330,2317 --from-year 2018
                                                      只回補指定股票
  python3 fetch_mops.py --update                      抓最新一季（排程用）
  python3 fetch_mops.py --probe 2330 2024 4           檢視解析結果（除錯用）
"""

import argparse
import io
import json
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = "https://mopsov.twse.com.tw"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FIN_DIR = DATA_DIR / "fin"
PROGRESS_FILE = DATA_DIR / "fin_progress.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def polite_sleep(base=1.6):
    time.sleep(base + random.random())


def to_num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "--", "nan", "None"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if neg else n


def latest_published_quarter(today=None):
    """依申報期限判斷目前應已公布的最新一季 (year, season)"""
    d = today or date.today()
    y, m, dd = d.year, d.month, d.day
    if (m, dd) >= (11, 15):
        return (y, 3)
    if (m, dd) >= (8, 15):
        return (y, 2)
    if (m, dd) >= (5, 16):
        return (y, 1)
    if (m, dd) >= (4, 1):
        return (y - 1, 4)
    return (y - 1, 3)


def quarters_between(y1, y2):
    q = []
    ylast, slast = latest_published_quarter()
    for y in range(y1, y2 + 1):
        for s in (1, 2, 3, 4):
            if (y, s) <= (ylast, slast):
                q.append((y, s))
    return q


# ═══════════════════════════════════════════
#  彙總表
# ═══════════════════════════════════════════

def fetch_bulk_tables(ajax, year, season):
    url = f"{BASE}/mops/web/{ajax}"
    payload = {
        "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
        "isQuery": "Y", "TYPEK": "sii",
        "year": str(year - 1911), "season": f"{season:02d}",
    }
    r = SESSION.post(url, data=payload, timeout=45)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf8"
    try:
        return pd.read_html(io.StringIO(r.text))
    except ValueError:
        return []


def col_like(df, keywords):
    for c in df.columns:
        cs = str(c)
        for kw in keywords:
            if kw in cs:
                return c
    return None


def parse_bulk_income(tables):
    out = {}
    for df in tables:
        df.columns = [str(c) for c in df.columns]
        code_c = col_like(df, ["公司代號", "公司 代號"])
        if code_c is None:
            continue
        rev_c = col_like(df, ["營業收入", "收益"])
        gp_c = col_like(df, ["營業毛利"])
        op_c = col_like(df, ["營業利益"])
        nonop_c = col_like(df, ["營業外收入及支出", "營業外損益"])
        ni_c = col_like(df, ["淨利（淨損）歸屬於母公司", "淨利(淨損)歸屬於母公司", "本期淨利"])
        eps_c = col_like(df, ["基本每股盈餘"])
        for _, row in df.iterrows():
            code = str(row[code_c]).strip()
            if not re.fullmatch(r"\d{4,6}", code):
                continue
            out[code] = {
                "rev": to_num(row[rev_c]) if rev_c is not None else None,
                "gp": to_num(row[gp_c]) if gp_c is not None else None,
                "op": to_num(row[op_c]) if op_c is not None else None,
                "nonop": to_num(row[nonop_c]) if nonop_c is not None else None,
                "ni": to_num(row[ni_c]) if ni_c is not None else None,
                "eps": to_num(row[eps_c]) if eps_c is not None else None,
            }
    return out


def parse_bulk_balance(tables):
    out = {}
    for df in tables:
        df.columns = [str(c) for c in df.columns]
        code_c = col_like(df, ["公司代號", "公司 代號"])
        if code_c is None:
            continue
        asset_c = col_like(df, ["資產總計", "資產總額"])
        liab_c = col_like(df, ["負債總計", "負債總額"])
        eq_c = col_like(df, ["權益總計", "權益總額"])
        ca_c = col_like(df, ["流動資產"])
        cl_c = col_like(df, ["流動負債"])
        bvps_c = col_like(df, ["每股參考淨值"])
        for _, row in df.iterrows():
            code = str(row[code_c]).strip()
            if not re.fullmatch(r"\d{4,6}", code):
                continue
            out[code] = {
                "assets": to_num(row[asset_c]) if asset_c is not None else None,
                "liab": to_num(row[liab_c]) if liab_c is not None else None,
                "eq": to_num(row[eq_c]) if eq_c is not None else None,
                "ca": to_num(row[ca_c]) if ca_c is not None else None,
                "cl": to_num(row[cl_c]) if cl_c is not None else None,
                "bvps": to_num(row[bvps_c]) if bvps_c is not None else None,
            }
    return out


# ═══════════════════════════════════════════
#  個別公司三大報表
# ═══════════════════════════════════════════

DETAIL_ITEMS = [
    ("inv", ["存貨"]),
    ("ar", ["應收帳款淨額", "應收帳款"]),
    ("ap", ["應付帳款"]),
    ("ppe", ["不動產、廠房及設備", "不動產廠房及設備"]),
    ("cash_bs", ["現金及約當現金"]),
    ("stb", ["短期借款"]),
    ("ltb", ["長期借款"]),
    ("lti", ["採用權益法之投資"]),
    ("dep", ["折舊費用"]),
    ("ocf", ["營業活動之淨現金流入", "營業活動之淨現金"]),
    ("capex", ["取得不動產、廠房及設備", "取得不動產廠房及設備"]),
]

EXCLUDE_ROW = re.compile(r"合計|總計|週轉|days|Total")


def fetch_detail(co_id, year, season):
    url = (f"{BASE}/server-java/t164sb01?step=1&CO_ID={co_id}"
           f"&SYEAR={year}&SSEASON={season}&REPORT_ID=C")
    r = SESSION.get(url, timeout=45)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "big5"
    text = r.text
    if "查無" in text or "無應編製" in text:
        return None
    try:
        tables = pd.read_html(io.StringIO(text))
    except ValueError:
        return None
    found = {}
    for df in tables:
        if df.shape[1] < 2:
            continue
        for _, row in df.iterrows():
            cells = [str(v).replace("\u3000", " ").replace("\xa0", " ").strip()
                     for v in row.tolist()]
            for key, kws in DETAIL_ITEMS:
                if key in found:
                    continue
                hit = -1
                for ci, cell in enumerate(cells):
                    if cell in ("", "nan"):
                        continue
                    for kw in kws:
                        if cell.startswith(kw) and not EXCLUDE_ROW.search(cell.replace(kw, "")):
                            hit = ci
                            break
                    if hit >= 0:
                        break
                if hit < 0:
                    continue
                val = None
                for ci in range(hit + 1, len(cells)):
                    val = to_num(cells[ci])
                    if val is not None:
                        break
                if val is not None:
                    found[key] = val
    return found or None


# ═══════════════════════════════════════════
#  儲存
# ═══════════════════════════════════════════

def qkey(year, season):
    return f"{year}Q{season}"


def load_stock(code):
    p = FIN_DIR / f"{code}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf8"))
        except Exception:
            pass
    return {"code": code, "q": {}}


def save_stock(obj):
    FIN_DIR.mkdir(parents=True, exist_ok=True)
    obj["updated"] = date.today().isoformat()
    p = FIN_DIR / f"{obj['code']}.json"
    p.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf8")


def load_progress():
    if PROGRESS_FILE.exists():
        try:
            return set(tuple(x) for x in json.loads(PROGRESS_FILE.read_text()))
        except Exception:
            pass
    return set()


def save_progress(done):
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_FILE.write_text(json.dumps(sorted(done), separators=(",", ":")))


# ═══════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════

def run_bulk(year, season):
    print(f"[bulk] {year}Q{season} 損益彙總 ...", flush=True)
    inc = parse_bulk_income(fetch_bulk_tables("ajax_t163sb04", year, season))
    polite_sleep()
    print(f"[bulk] {year}Q{season} 資產負債彙總 ...", flush=True)
    bal = parse_bulk_balance(fetch_bulk_tables("ajax_t163sb05", year, season))
    polite_sleep()
    codes = set(inc) | set(bal)
    if not codes:
        print(f"[bulk] {year}Q{season} 無資料（可能尚未公布）")
        return 0
    k = qkey(year, season)
    for code in codes:
        obj = load_stock(code)
        q = obj["q"].setdefault(k, {})
        q.update({kk: vv for kk, vv in (inc.get(code) or {}).items() if vv is not None})
        q.update({kk: vv for kk, vv in (bal.get(code) or {}).items() if vv is not None})
        save_stock(obj)
    print(f"[bulk] {year}Q{season} 完成，{len(codes)} 檔")
    return len(codes)


def run_detail(codes, quarters, limit):
    done = load_progress()
    count = 0
    for code in codes:
        for (y, s) in quarters:
            key = [code, y, s]
            if tuple(key) in done:
                continue
            if count >= limit:
                save_progress(done)
                print(f"[detail] 已達本次上限 {limit}，進度已存檔，下次自動續傳")
                return count
            try:
                d = fetch_detail(code, y, s)
            except Exception as e:
                print(f"[detail] {code} {y}Q{s} 失敗：{e}（下次重試）")
                polite_sleep(3)
                continue
            done.add(tuple(key))
            count += 1
            if d:
                obj = load_stock(code)
                obj["q"].setdefault(qkey(y, s), {}).update(d)
                save_stock(obj)
                print(f"[detail] {code} {y}Q{s} ok ({len(d)} 項)")
            else:
                print(f"[detail] {code} {y}Q{s} 無資料")
            if count % 20 == 0:
                save_progress(done)
            polite_sleep()
    save_progress(done)
    return count


def all_codes_with_data():
    if not FIN_DIR.exists():
        return []
    return sorted(p.stem for p in FIN_DIR.glob("*.json"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk-backfill", nargs=2, type=int, metavar=("Y1", "Y2"))
    ap.add_argument("--detail-backfill", nargs=2, type=int, metavar=("Y1", "Y2"))
    ap.add_argument("--detail-codes", type=str)
    ap.add_argument("--from-year", type=int, default=2018)
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--probe", nargs=3, metavar=("CODE", "YEAR", "SEASON"))
    args = ap.parse_args()

    if args.probe:
        code, y, s = args.probe[0], int(args.probe[1]), int(args.probe[2])
        d = fetch_detail(code, y, s)
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return

    if args.bulk_backfill:
        y1, y2 = args.bulk_backfill
        for (y, s) in quarters_between(y1, y2):
            k = qkey(y, s)
            sample = load_stock("2330")["q"].get(k, {})
            if sample.get("rev") is not None and sample.get("ca") is not None:
                print(f"[bulk] {k} 已有資料，略過")
                continue
            run_bulk(y, s)
        return

    if args.detail_codes:
        codes = [c.strip() for c in args.detail_codes.split(",") if c.strip()]
        quarters = quarters_between(args.from_year, date.today().year)
        run_detail(codes, quarters, args.limit)
        return

    if args.detail_backfill:
        y1, y2 = args.detail_backfill
        codes = all_codes_with_data()
        if not codes:
            print("data/fin/ 是空的，請先跑 --bulk-backfill 建立公司清單")
            sys.exit(1)
        quarters = quarters_between(y1, y2)
        n = run_detail(codes, quarters, args.limit)
        remain = len(codes) * len(quarters) - len(load_progress())
        print(f"[detail] 本次 {n} 筆，估計剩餘 {max(remain,0)} 筆")
        return

    if args.update:
        y, s = latest_published_quarter()
        run_bulk(y, s)
        codes = all_codes_with_data()
        run_detail(codes, [(y, s)], limit=10**9)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
