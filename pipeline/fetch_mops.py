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


_DIV_WARMED = False


def _warm_dividend_session():
    global _DIV_WARMED
    if _DIV_WARMED:
        return
    try:
        SESSION.post(f"{BASE}/mops/web/ajax_t05st09_new",
                     data={"encodeURIComponent": "1", "step": "1", "firstin": "1",
                           "off": "1", "TYPEK": "sii"},
                     timeout=30)
    except Exception:
        pass
    _DIV_WARMED = True


def _flat_cols(df):
    return [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]


def _parse_div_table(df, roc_year, out):
    cols = _flat_cols(df)
    code_c = next((i for i, c in enumerate(cols) if "代號" in c), None)
    if code_c is None:
        return
    period_c = next((i for i, c in enumerate(cols)
                     if "期間" in c or "期別" in c or ("股利所屬" in c and "度" in c)), None)
    cash_total = [i for i, c in enumerate(cols) if "現金" in c and ("合計" in c or "總計" in c)]
    cash_parts = [i for i, c in enumerate(cols) if "現金股利" in c and ("盈餘" in c or "公積" in c)]
    cash_any = [i for i, c in enumerate(cols) if "現金股利" in c]
    stk_total = [i for i, c in enumerate(cols) if "股票" in c and ("合計" in c or "總計" in c)]
    stk_parts = [i for i, c in enumerate(cols) if "股票股利" in c and ("盈餘" in c or "公積" in c)]
    cash_cols = cash_total[:1] or cash_parts or cash_any[-1:]
    stk_cols = stk_total[:1] or stk_parts
    if not cash_cols:
        return
    for _, row in df.iterrows():
        raw_code = str(row.iloc[code_c]).strip().split(".")[0]
        m = re.match(r"(\d{4,6})", raw_code)
        if not m:
            continue
        code = m.group(1)
        period = str(row.iloc[period_c]).strip() if period_c is not None else f"{roc_year}年"
        if period in ("", "nan"):
            period = f"{roc_year}年"
        cash = sum(v for v in (to_num(row.iloc[ci]) for ci in cash_cols) if v)
        stk = sum(v for v in (to_num(row.iloc[ci]) for ci in stk_cols) if v)
        if not cash and not stk:
            continue
        out.setdefault(code, {})[period] = {"p": period, "cash": round(cash, 5), "stock": round(stk, 5)}


def fetch_dividends_year(roc_year):
    """抓取單一年度全部上市公司股利分派（一個請求涵蓋全市場）"""
    _warm_dividend_session()
    url = f"{BASE}/server-java/t05st09sub?step=1&TYPEK=sii&YEAR={roc_year}"
    r = SESSION.get(url, timeout=90,
                    headers={"Referer": f"{BASE}/mops/web/t05st09_new"})
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "big5"
    try:
        tables = pd.read_html(io.StringIO(r.text))
    except ValueError:
        return {}, []
    out = {}
    for df in tables:
        _parse_div_table(df, roc_year, out)
    if not out:
        return {}, tables
    return {code: list(periods.values()) for code, periods in out.items()}, []


def run_dividends(y1, y2):
    merged = {}
    for y in range(y1, y2 + 1):
        roc = y - 1911
        print(f"[div] {roc} 年度股利分派 ...", flush=True)
        try:
            data, _ = fetch_dividends_year(roc)
        except Exception as e:
            print(f"[div] {roc} 失敗：{e}")
            polite_sleep(3)
            continue
        for code, rows in data.items():
            merged.setdefault(code, []).extend(rows)
        print(f"[div] {roc} 完成，{len(data)} 檔")
        polite_sleep()
    n = 0
    for code, rows in merged.items():
        obj = load_stock(code)
        old = {d["p"]: d for d in obj.get("div", [])}
        for d in rows:
            old[d["p"]] = d
        obj["div"] = list(old.values())
        save_stock(obj)
        n += 1
    print(f"[div] 已寫入 {n} 檔股利紀錄")


def fetch_detail(co_id, year, season):
    """回傳 (status, data)。status: "ok"=有資料 / "empty"=確定查無報表（永久記錄）
    / "blocked"=疑似被 MOPS 擋或回異常頁（不記錄進度，之後重試）。"""
    url = (f"{BASE}/server-java/t164sb01?step=1&CO_ID={co_id}"
           f"&SYEAR={year}&SSEASON={season}&REPORT_ID=C")
    r = SESSION.get(url, timeout=45)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "big5"
    text = r.text
    if "查無" in text or "無應編製" in text:
        return "empty", None
    low = text.lower()
    if len(text) < 3000 or "overrun" in low or "頻繁" in text or "稍後再試" in text:
        return "blocked", None
    try:
        tables = pd.read_html(io.StringIO(text))
    except ValueError:
        return "blocked", None
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
    if found:
        return "ok", found
    # 頁面像正式報表（夠大、有表格）但關鍵字全沒中：多為特殊行業格式，視為確定無資料
    return ("empty", None) if len(text) > 20000 else ("blocked", None)


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
    blocked_streak = 0
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
                status, d = fetch_detail(code, y, s)
            except Exception as e:
                print(f"[detail] {code} {y}Q{s} 失敗：{e}（下次重試）")
                blocked_streak += 1
                if blocked_streak >= 15:
                    save_progress(done)
                    print("[detail] 連續 15 次失敗，MOPS 可能封鎖中，中止本輪（進度已存檔，稍後再跑）")
                    return count
                polite_sleep(5)
                continue
            if status == "blocked":
                blocked_streak += 1
                print(f"[detail] {code} {y}Q{s} 被擋/異常頁（不記錄，下次重試）")
                if blocked_streak >= 15:
                    save_progress(done)
                    print("[detail] 連續 15 次被擋，MOPS 可能封鎖中，中止本輪（進度已存檔，稍後再跑）")
                    return count
                if blocked_streak % 5 == 0:
                    print("[detail] 連續被擋，暫停 90 秒降溫…")
                    time.sleep(90)
                else:
                    polite_sleep(5)
                continue
            blocked_streak = 0
            done.add(tuple(key))
            count += 1
            if status == "ok":
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


DETAIL_FIELDS = ["inv", "ar", "ap", "ppe", "cash_bs", "stb", "ltb", "lti", "dep", "ocf", "capex"]


def retry_missing(y1, y2, limit):
    """重試假性「無資料」：progress 已標完成、但該季在 fin json 內沒有任何 detail 欄位者，
    自進度移除後重跑 run_detail。MOPS 偶發失敗（限流/伺服器錯誤）會被記成無資料，用此模式撈回。
    真的沒有財報的季（上市前等）會再被查一次然後重新標記，成本只是每季一個請求。"""
    done = load_progress()
    quarters = quarters_between(y1, y2)
    qset = set(quarters)
    retry = set()
    for code in all_codes_with_data():
        obj = load_stock(code)
        qmap = obj.get("q") or {}
        for (c, y, s) in list(done):
            if c != code or (y, s) not in qset:
                continue
            row = qmap.get(qkey(y, s)) or {}
            if not any(row.get(f) is not None for f in DETAIL_FIELDS):
                retry.add((c, y, s))
    if not retry:
        print("[retry] 沒有需要重試的季")
        return 0
    print(f"[retry] 發現 {len(retry)} 季標記完成但無 detail 資料，自進度移除後重抓")
    save_progress(done - retry)
    codes = sorted(set(c for (c, _, _) in retry))
    return run_detail(codes, quarters, limit)


def all_codes_with_data():
    if not FIN_DIR.exists():
        return []
    return sorted(p.stem for p in FIN_DIR.glob("*.json"))


def build_screen():
    """彙總全部股票的篩選指標 -> data/screen.json（供前端篩選功能，一檔一列）
    償債年數 = (最新季 stb+ltb) / 近4季單季 ni 合計（ni 來自彙總表，全市場都有）
    折舊年數 = 最新季 ppe / 近4季單季 dep 合計（dep 來自 detail，回補完成的股票才有）
    近4季任一季無法算出單季值則該指標為 null（前端顯示 —、排序墊底）
    """
    def dec(qmap, key, field):
        v = qmap.get(key, {}).get(field)
        if v is None:
            return None
        y, s = int(key[:4]), int(key[5])
        if s == 1:
            return v
        prev = qmap.get(f"{y}Q{s-1}", {}).get(field)
        return (v - prev) if prev is not None else None

    rows = []
    for code in all_codes_with_data():
        obj = load_stock(code)
        qmap = obj.get("q") or {}
        keys = sorted(qmap.keys())
        if len(keys) < 4:
            continue
        last4 = keys[-4:]
        ni4 = dep4 = None
        nis = [dec(qmap, k, "ni") for k in last4]
        deps = [dec(qmap, k, "dep") for k in last4]
        if all(v is not None for v in nis):
            ni4 = sum(nis)
        if all(v is not None for v in deps):
            dep4 = sum(deps)
        ppe = stb = ltb = None
        has_detail = False
        for k in reversed(keys):
            r = qmap[k]
            if r.get("ppe") is not None or r.get("dep") is not None:
                ppe, stb, ltb = r.get("ppe"), r.get("stb"), r.get("ltb")
                has_detail = True
                break
        debt_y = dep_y = None
        if has_detail and ni4 is not None and ni4 > 0:
            debt_y = round(((stb or 0) + (ltb or 0)) / ni4, 2)
        if ppe is not None and dep4 is not None and dep4 > 0:
            dep_y = round(ppe / dep4, 2)
        rows.append({"c": code, "q": keys[-1], "debt": debt_y, "dep": dep_y})
    out = {"updated": date.today().isoformat(), "rows": rows}
    (DATA_DIR / "screen.json").write_text(
        json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf8")
    print(f"[screen] {len(rows)} 檔（償債年數可算 {sum(1 for r in rows if r['debt'] is not None)}、"
          f"折舊年數可算 {sum(1 for r in rows if r['dep'] is not None)}）-> data/screen.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk-backfill", nargs=2, type=int, metavar=("Y1", "Y2"))
    ap.add_argument("--detail-backfill", nargs=2, type=int, metavar=("Y1", "Y2"))
    ap.add_argument("--detail-codes", type=str)
    ap.add_argument("--from-year", type=int, default=2018)
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--probe", nargs=3, metavar=("CODE", "YEAR", "SEASON"))
    ap.add_argument("--probe-div", type=str, metavar="ROC_YEAR")
    ap.add_argument("--dividends-backfill", nargs=2, type=int, metavar=("Y1", "Y2"))
    ap.add_argument("--build-screen", action="store_true")
    ap.add_argument("--retry-missing", nargs=2, type=int, metavar=("Y1", "Y2"))
    args = ap.parse_args()

    if args.probe_div:
        data, raw = fetch_dividends_year(int(args.probe_div))
        if data:
            sample = data.get("2330") or next(iter(data.values()))
            print(f"共 {len(data)} 檔。範例（2330 或第一檔）：")
            print(json.dumps(sample, ensure_ascii=False, indent=2))
        else:
            print("解析不到股利資料。以下是頁面表格診斷：")
            for i, df in enumerate(raw[:5]):
                cols = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in df.columns]
                print(f"table {i}: shape={df.shape}")
                print("  cols:", [c[:40] for c in cols[:12]])
        return

    if args.dividends_backfill:
        run_dividends(args.dividends_backfill[0], args.dividends_backfill[1])
        return

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

    if args.build_screen:
        build_screen()
        return

    if args.retry_missing:
        y1, y2 = args.retry_missing
        n = retry_missing(y1, y2, args.limit)
        print(f"[retry] 本次重抓 {n} 筆")
        build_screen()
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
        build_screen()
        return

    if args.update:
        y, s = latest_published_quarter()
        run_bulk(y, s)
        run_dividends(date.today().year - 1, date.today().year)
        codes = all_codes_with_data()
        run_detail(codes, [(y, s)], limit=10**9)
        build_screen()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
