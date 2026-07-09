#!/usr/bin/env python3
"""
tw-river 歷史價格落地管線（待辦：河流圖九年資料改靜態檔，根治 TWSE 對 Cloudflare 限流）

資料來源（與 Worker /bundle 完全相同的兩個端點，直連 TWSE）：
  FMSRFK  逐月最高/最低/平均價   https://www.twse.com.tw/rwd/zh/afterTrading/FMSRFK?date={Y}0101&stockNo={code}&response=json
  BWIBBU  逐日 PE/PB/殖利率     https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU?date={Y}1201&stockNo={code}&response=json

輸出 data/price/{code}.json（單行 compact）：
  {"code":"2330","updated":"YYYY-MM-DD","y":{
     "2018":{"hi":..,"lo":..,"avg":..,"pe":..,"pb":..,"yield":..,"ref":..},
     "2017":null,          # null = 已查證該年無資料（上市前/停牌），下次不再重抓
     ...}}
  只存「已完結年度」（去年以前）；當年由前端走 /bundle 即時。
  欄位語意與前端一致：hi/lo/avg=全年月線極值與月均平均；pe/pb/yield=12 月逐日值的
  正值平均（同 parseMonthRatio）；ref=12 月的月平均價（同 refPrice，缺則 (hi+lo)/2，再缺用年 avg）。
  前端以 buildYearData(y,{hi,lo,avg},{pe,pb,yield},ref) 直接重建，公式零改動。

斷點＝輸出檔本身：某代號某年鍵已存在（含 null）即跳過；中斷重跑不重工、不壞資料。

指令：
  python3 pipeline/price_ingest.py --probe 2330                  端點驗證（請 Dale 先跑並確認）
  python3 pipeline/price_ingest.py --backfill --delay 3          一次回補全部上市代號（建議本機過夜跑）
  python3 pipeline/price_ingest.py --backfill --codes 2330 2313  只跑指定代號（抽測用）
  python3 pipeline/price_ingest.py --update                      每月增量（Actions 用；平時近乎 no-op，
                                                                 每年 1 月自動補前一完結年＋修剪視窗外舊年）
  python3 pipeline/price_ingest.py --backfill --limit 200        本次最多處理 200 個「有缺年」代號（分段跑）
"""

import argparse
import datetime
import json
import pathlib
import random
import sys
import time
import urllib.request

BASE = "https://www.twse.com.tw/rwd/zh/afterTrading"
OPENAPI_COMPANIES = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json",
}
ROOT = pathlib.Path(__file__).resolve().parent.parent
PRICE_DIR = ROOT / "data" / "price"

BLOCK_THRESHOLD = 5      # 連續 blocked 幾次後降溫一次
COOLDOWN_SEC = 90        # 降溫秒數；降溫後仍立即再擋 -> 中止本次執行


def num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "--", "—"):
        return None
    try:
        f = float(s)
        return f
    except ValueError:
        return None


def http_get_json(url):
    """回傳 (state, json)。state: ok / empty / blocked。
    empty = TWSE 正常回應但無資料（stat != OK）-> 視為最終結果記 null。
    blocked = HTTP 錯誤 / 非 JSON（限流頁）/ 逾時 -> 不記錄，之後重試。"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return "blocked", str(e)
    try:
        j = json.loads(raw)
    except ValueError:
        return "blocked", raw[:120]
    if not isinstance(j, dict):
        return "blocked", raw[:120]
    if j.get("stat") != "OK" or not j.get("data"):
        return "empty", j
    return "ok", j


def pick_col(fields, keywords):
    for kw in keywords:
        for i, f in enumerate(fields):
            if kw in str(f):
                return i
    return -1


def parse_year_price(j):
    """對齊前端 parseYearPrice：月列 -> {hi,lo,avg,months}"""
    f = j.get("fields") or []
    i_mon = pick_col(f, ["月份", "月"])
    i_hi = pick_col(f, ["最高價"])
    i_lo = pick_col(f, ["最低價"])
    i_avg = pick_col(f, ["平均"])
    months = []
    for row in j.get("data") or []:
        m = {
            "mon": num(row[i_mon]) if i_mon >= 0 else None,
            "hi": num(row[i_hi]) if i_hi >= 0 else None,
            "lo": num(row[i_lo]) if i_lo >= 0 else None,
            "avg": num(row[i_avg]) if i_avg >= 0 else None,
        }
        if m["hi"] is not None and m["lo"] is not None:
            months.append(m)
    if not months:
        return None
    avg = sum((m["avg"] if m["avg"] is not None else (m["hi"] + m["lo"]) / 2) for m in months) / len(months)
    return {
        "hi": max(m["hi"] for m in months),
        "lo": min(m["lo"] for m in months),
        "avg": avg,
        "months": months,
    }


def parse_month_ratio(j):
    """對齊前端 parseMonthRatio：逐日列正值平均 -> {pe,pb,yield}"""
    f = j.get("fields") or []
    i_pe = pick_col(f, ["本益比"])
    i_pb = pick_col(f, ["股價淨值比"])
    i_y = pick_col(f, ["殖利率"])

    def mean(idx):
        if idx < 0:
            return None
        vals = [num(r[idx]) for r in (j.get("data") or [])]
        vals = [v for v in vals if v is not None and v > 0]
        return sum(vals) / len(vals) if vals else None

    out = {"pe": mean(i_pe), "pb": mean(i_pb), "yield": mean(i_y)}
    return out if (out["pe"] or out["pb"] or out["yield"]) else None


def ref_price(price):
    """對齊前端：12 月月均價，缺 avg 用 (hi+lo)/2，找不到 12 月列用年 avg"""
    for m in price["months"]:
        if m["mon"] == 12:
            return m["avg"] if m["avg"] is not None else (m["hi"] + m["lo"]) / 2
    return price["avg"]


def rnd(v, nd=4):
    return None if v is None else round(v, nd)


class Fetcher:
    def __init__(self, delay):
        self.delay = delay
        self.consecutive_blocked = 0
        self.cooled = False
        self.n_req = 0
        self.n_ok = 0
        self.n_empty = 0
        self.n_blocked = 0

    def get(self, url):
        """含節流、單請求重試、連擋降溫/中止。回傳 (state, json_or_msg)"""
        for attempt in range(3):
            time.sleep(self.delay + random.random() * 0.5)
            self.n_req += 1
            state, j = http_get_json(url)
            if state != "blocked":
                self.consecutive_blocked = 0
                self.cooled = False
                if state == "ok":
                    self.n_ok += 1
                else:
                    self.n_empty += 1
                return state, j
            self.n_blocked += 1
            if attempt < 2:
                time.sleep(self.delay * (attempt + 2))
        self.consecutive_blocked += 1
        if self.consecutive_blocked >= BLOCK_THRESHOLD:
            if self.cooled:
                print(f"\n!! 降溫後仍連續被擋（{url}），疑似 IP 限流，中止本次執行。稍後重跑會從斷點續傳。", flush=True)
                sys.exit(2)
            print(f"\n.. 連續 {self.consecutive_blocked} 次失敗，降溫 {COOLDOWN_SEC}s …", flush=True)
            time.sleep(COOLDOWN_SEC)
            self.cooled = True
            self.consecutive_blocked = 0
        return "blocked", j


def fetch_year(fetcher, code, year):
    """抓一個代號一個完結年度。回傳 (state, entry_or_None)
    state: ok（有資料）/ empty（該年無資料，記 null）/ blocked（跳過，下次再試）"""
    st, pj = fetcher.get(f"{BASE}/FMSRFK?date={year}0101&stockNo={code}&response=json")
    if st == "blocked":
        return "blocked", None
    if st == "empty":
        return "empty", None
    price = parse_year_price(pj)
    if not price:
        return "empty", None
    st, rj = fetcher.get(f"{BASE}/BWIBBU?date={year}1201&stockNo={code}&response=json")
    if st == "blocked":
        return "blocked", None       # 價抓到但比率被擋：整年不記，下次重抓（避免半套資料定型）
    ratio = parse_month_ratio(rj) if st == "ok" else None
    entry = {
        "hi": price["hi"],
        "lo": price["lo"],
        "avg": rnd(price["avg"]),
        "pe": rnd(ratio["pe"]) if ratio else None,
        "pb": rnd(ratio["pb"]) if ratio else None,
        "yield": rnd(ratio["yield"]) if ratio else None,
        "ref": rnd(ref_price(price)),
    }
    return "ok", entry


def load_codes(args):
    if args.codes:
        return list(args.codes)
    print("取得上市公司清單（openapi t187ap03_L）…", flush=True)
    state, j = http_get_json(OPENAPI_COMPANIES)
    if state != "ok" and not isinstance(j, list):
        # openapi 回傳為 JSON 陣列，http_get_json 會因非 dict 判 blocked，這裡直接重抓解析
        req = urllib.request.Request(OPENAPI_COMPANIES, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode("utf-8"))
    codes = sorted({str(r.get("公司代號", "")).strip() for r in j if r.get("公司代號")})
    codes = [c for c in codes if c]
    print(f"共 {len(codes)} 個上市代號", flush=True)
    return codes


def read_price_file(code):
    p = PRICE_DIR / f"{code}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def write_price_file(code, doc):
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    doc["updated"] = datetime.date.today().isoformat()
    # y 依年份排序輸出，方便 diff
    doc["y"] = {k: doc["y"][k] for k in sorted(doc["y"].keys())}
    (PRICE_DIR / f"{code}.json").write_text(
        json.dumps(doc, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )


def run_sync(args, prune):
    now_y = datetime.date.today().year
    from_y = args.from_year or (now_y - 10)
    years = [str(y) for y in range(from_y, now_y)]  # 只含已完結年度
    codes = load_codes(args)
    fetcher = Fetcher(args.delay)

    n_codes_touched = 0
    n_new_years = 0
    n_null_years = 0
    n_skipped_blocked = 0
    new_files = 0
    t0 = time.time()

    for idx, code in enumerate(codes, 1):
        doc = read_price_file(code)
        if doc is None:
            doc = {"code": code, "updated": None, "y": {}}
            is_new = True
        else:
            is_new = False
            doc.setdefault("y", {})
        missing = [y for y in years if y not in doc["y"]]
        pruned = False
        if prune:
            for k in [k for k in doc["y"].keys() if k < years[0]]:
                del doc["y"][k]
                pruned = True
        if not missing:
            if pruned:
                write_price_file(code, doc)
            continue
        if args.limit and n_codes_touched >= args.limit:
            print(f".. 已達 --limit {args.limit}，停止。剩餘代號下次續跑。", flush=True)
            break
        n_codes_touched += 1
        got, nul, blk = 0, 0, 0
        for y in missing:
            state, entry = fetch_year(fetcher, code, int(y))
            if state == "ok":
                doc["y"][y] = entry
                got += 1
            elif state == "empty":
                doc["y"][y] = None
                nul += 1
            else:
                blk += 1
        if got or nul or pruned:
            write_price_file(code, doc)
            if is_new:
                new_files += 1
        n_new_years += got
        n_null_years += nul
        n_skipped_blocked += blk
        print(f"[{idx}/{len(codes)}] {code}：補 {got} 年、無資料 {nul} 年"
              + (f"、被擋跳過 {blk} 年" if blk else "")
              + f"｜累計請求 {fetcher.n_req}", flush=True)

    dt = time.time() - t0
    print("\n===== 統計 =====")
    print(f"處理代號 {n_codes_touched}（新建檔 {new_files}）｜寫入年度 {n_new_years}｜無資料年度 {n_null_years}｜被擋待重試年度 {n_skipped_blocked}")
    print(f"請求 {fetcher.n_req}（ok {fetcher.n_ok}／empty {fetcher.n_empty}／blocked {fetcher.n_blocked}）｜耗時 {dt/60:.1f} 分")
    if n_skipped_blocked:
        print("!! 有年度因限流被跳過，請稍後重跑同一指令續補（斷點＝檔案本身，冪等）。")


def run_probe(args):
    code = args.probe
    year = args.year or (datetime.date.today().year - 1)
    for name, url in [
        ("FMSRFK", f"{BASE}/FMSRFK?date={year}0101&stockNo={code}&response=json"),
        ("BWIBBU", f"{BASE}/BWIBBU?date={year}1201&stockNo={code}&response=json"),
    ]:
        print(f"\n=== {name} {url}")
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode("utf-8", errors="replace")
            print("HTTP 200，前 600 字元：")
            print(raw[:600])
            j = json.loads(raw)
            if name == "FMSRFK":
                p = parse_year_price(j)
                if p:
                    print(f"解析：hi={p['hi']} lo={p['lo']} avg={round(p['avg'],4)} ref={round(ref_price(p),4)} 月數={len(p['months'])}")
                else:
                    print("解析：無月資料（stat 非 OK 或空）")
            else:
                rt = parse_month_ratio(j) if j.get("stat") == "OK" else None
                print(f"解析：{rt}")
        except Exception as e:
            print(f"失敗：{e}")
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description="tw-river 歷史價格落地")
    ap.add_argument("--probe", metavar="CODE", help="端點驗證：抓指定代號一年份的兩端點並列印原始＋解析結果")
    ap.add_argument("--year", type=int, help="probe 用年份（預設去年）")
    ap.add_argument("--backfill", action="store_true", help="回補視窗內所有缺漏年度（斷點續傳）")
    ap.add_argument("--update", action="store_true", help="每月增量：同 backfill 並修剪視窗外舊年（Actions 用）")
    ap.add_argument("--codes", nargs="+", help="只處理指定代號（預設抓 openapi 上市全清單）")
    ap.add_argument("--from-year", type=int, help="視窗起始年（預設今年-10）")
    ap.add_argument("--delay", type=float, default=3.0, help="每請求間隔秒數（預設 3.0）")
    ap.add_argument("--limit", type=int, help="本次最多處理 N 個有缺年的代號（分段跑）")
    args = ap.parse_args()

    if args.probe:
        run_probe(args)
    elif args.backfill:
        run_sync(args, prune=False)
    elif args.update:
        run_sync(args, prune=True)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
