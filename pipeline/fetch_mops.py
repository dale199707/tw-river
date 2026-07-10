#!/usr/bin/env python3
"""
tw-river 股利與篩選彙總管線
資料來源：公開資訊觀測站（mopsov.twse.com.tw）

季報財務數字已改由 XBRL 整批檔產生（pipeline/xbrl_ingest.py，每季手動跑），
本檔僅保留兩件事：
  股利爬蟲   ajax_t05st09_new 暖機 + t05st09sub（一年一請求，資料散在多張小表）
  篩選彙總   --build-screen 由 data/fin/*.json 純本機計算 data/screen.json

輸出：data/fin/{code}.json 的 div 陣列（單行 compact JSON）
      data/screen.json

用法：
  python3 fetch_mops.py --dividends-backfill 2018 2026   回補股利
  python3 fetch_mops.py --probe-div 113                  股利端點驗證（除錯用）
  python3 fetch_mops.py --update                         更新近兩年股利＋重建篩選彙總（排程用）
  python3 fetch_mops.py --build-screen                   只重建篩選彙總
"""

import argparse
import io
import json
import random
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = "https://mopsov.twse.com.tw"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FIN_DIR = DATA_DIR / "fin"

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
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--probe-div", type=str, metavar="ROC_YEAR")
    ap.add_argument("--dividends-backfill", nargs=2, type=int, metavar=("Y1", "Y2"))
    ap.add_argument("--build-screen", action="store_true")
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

    if args.build_screen:
        build_screen()
        return

    if args.update:
        run_dividends(date.today().year - 1, date.today().year)
        build_screen()
        return

    ap.print_help()


if __name__ == "__main__":
    main()
