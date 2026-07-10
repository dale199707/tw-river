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
  python3 pipeline/price_ingest.py --tpex-snap --delay 2         上櫃每日快照＋當年逐月累計（Actions 平日盤後跑；
                                                                 TPEX 擋 Cloudflare 出口，前端上櫃即時資料一律
                                                                 由本模式產生之靜態檔供應）
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

BLOCK_THRESHOLD = 5              # 連續 blocked 幾次後降溫一次
COOLDOWNS = [120, 300, 600, 1800]  # 階梯式降溫秒數；末段 30 分鐘可睡過 TPEX 的長封鎖（約每 300 請求封一輪）


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
    """回傳 (state, json)。state: ok / blocked。
    blocked = HTTP 錯誤 / 非 JSON（限流頁）/ 逾時 -> 不記錄，之後重試。
    空資料（如 TWSE stat != OK、TPEX 無交易日）由呼叫端以 is_empty 判定。"""
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
    if not isinstance(j, (dict, list)):
        return "blocked", raw[:120]
    return "ok", j


def twse_is_empty(j):
    return not isinstance(j, dict) or j.get("stat") != "OK" or not j.get("data")


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
        self.cooldown_i = 0
        self.n_req = 0
        self.n_ok = 0
        self.n_empty = 0
        self.n_blocked = 0

    def get(self, url, is_empty=None):
        """含節流、單請求重試、連擋降溫/中止。回傳 (state, json_or_msg)，state: ok/empty/blocked"""
        for attempt in range(3):
            time.sleep(self.delay + random.random() * 0.5)
            self.n_req += 1
            state, j = http_get_json(url)
            if state != "blocked":
                self.consecutive_blocked = 0
                self.cooldown_i = 0
                if is_empty and is_empty(j):
                    self.n_empty += 1
                    return "empty", j
                self.n_ok += 1
                return "ok", j
            self.n_blocked += 1
            if attempt < 2:
                time.sleep(self.delay * (attempt + 2))
        self.consecutive_blocked += 1
        if self.consecutive_blocked >= BLOCK_THRESHOLD:
            if self.cooldown_i >= len(COOLDOWNS):
                print(f"\n!! 階梯降溫全部用盡仍連續被擋（{url}），IP 限流未解除，中止本次執行。建議休息 30 分鐘後重跑續補。", flush=True)
                sys.exit(2)
            sec = COOLDOWNS[self.cooldown_i]
            self.cooldown_i += 1
            print(f"\n.. 連續 {self.consecutive_blocked} 次失敗，降溫 {sec}s（第 {self.cooldown_i}/{len(COOLDOWNS)} 次）…", flush=True)
            time.sleep(sec)
            self.consecutive_blocked = 0
        return "blocked", j


def fetch_year(fetcher, code, year):
    """抓一個代號一個完結年度。回傳 (state, entry_or_None)
    state: ok（有資料）/ empty（該年無資料，記 null）/ blocked（跳過，下次再試）"""
    st, pj = fetcher.get(f"{BASE}/FMSRFK?date={year}0101&stockNo={code}&response=json", is_empty=twse_is_empty)
    if st == "blocked":
        return "blocked", None
    if st == "empty":
        return "empty", None
    price = parse_year_price(pj)
    if not price:
        return "empty", None
    st, rj = fetcher.get(f"{BASE}/BWIBBU?date={year}1201&stockNo={code}&response=json", is_empty=twse_is_empty)
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



# ============ TPEX（上櫃）：逐日全市場掃描 ============
# dailyQuotes（新版 API，西元日期）：一天一請求回全市場 OHLC -> 累計出每檔每月 hi/lo/均
# pera_result（舊版 API，民國日期）：12 月逐日全市場 PE/PB/殖利率 -> 正值平均（同 parseMonthRatio 語意）
# 一年 ~261 平日 + ~22 個 12 月交易日 ≈ 283 請求涵蓋全部上櫃股
TPEX_NEW = "https://www.tpex.org.tw/www/zh-tw/afterTrading"
TPEX_PERA = "https://www.tpex.org.tw/web/stock/aftertrading/peratio_analysis/pera_result.php"
TPEX_COMPANIES = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TPEX_PROGRESS = ROOT / "data" / "tpex_price_progress.json"
TPEX_SNAP = ROOT / "data" / "tpex_snap.json"
TPEX_YTD = ROOT / "data" / "tpex_ytd.json"


def tpex_pick_table(j, keywords):
    """從新版 API 回應的 tables 中找出含指定欄位關鍵字且有資料的表"""
    if not isinstance(j, dict):
        return None
    for t in (j.get("tables") or []):
        f = t.get("fields") or []
        if t.get("data") and all(any(kw in str(x) for x in f) for kw in keywords):
            return t
    return None


def tpex_daily_empty(j):
    """非交易日的合法形狀＝有 tables 鍵但無匹配表；缺 tables 鍵（限流/異常頁）不算空，交由呼叫端判 blocked"""
    return isinstance(j, dict) and isinstance(j.get("tables"), list) and tpex_pick_table(j, ["代號", "收盤", "最高", "最低"]) is None


def tpex_pera_empty(j):
    return isinstance(j, dict) and isinstance(j.get("tables"), list) and tpex_pick_table(j, ["代號", "本益比", "殖利率", "淨值比"]) is None


def tpex_fetch_daily(fetcher, d):
    """單一交易日全市場行情。回傳 (state, {code:(close,hi,lo)})"""
    url = f"{TPEX_NEW}/dailyQuotes?date={d.year}/{d.month:02d}/{d.day:02d}&response=json"
    state, j = fetcher.get(url, is_empty=tpex_daily_empty)
    if state != "ok":
        return state, None
    t = tpex_pick_table(j, ["代號", "收盤", "最高", "最低"])
    if not t:
        return "blocked", None
    f = t["fields"]
    i_code = pick_col(f, ["代號"])
    i_close = pick_col(f, ["收盤"])
    i_hi = pick_col(f, ["最高"])
    i_lo = pick_col(f, ["最低"])
    out = {}
    for row in t["data"]:
        code = str(row[i_code]).strip()
        c, h, l = num(row[i_close]), num(row[i_hi]), num(row[i_lo])
        if code and c is not None and h is not None and l is not None:
            out[code] = (c, h, l)
    return "ok", out


def tpex_fetch_pera(fetcher, d):
    """單一交易日全市場 PE/PB/殖利率。回傳 (state, {code:(pe,pb,yield)})"""
    roc = f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"
    url = f"{TPEX_PERA}?l=zh-tw&o=json&d={roc}"
    state, j = fetcher.get(url, is_empty=tpex_pera_empty)
    if state != "ok":
        return state, None
    t = tpex_pick_table(j, ["代號", "本益比", "殖利率", "淨值比"])
    if not t:
        return "blocked", None
    f = t["fields"]
    i_code = pick_col(f, ["代號"])
    i_pe = pick_col(f, ["本益比"])
    i_pb = pick_col(f, ["淨值比"])
    i_y = pick_col(f, ["殖利率"])
    out = {}
    for row in t["data"]:
        code = str(row[i_code]).strip()
        if code:
            out[code] = (num(row[i_pe]), num(row[i_pb]), num(row[i_y]))
    return "ok", out


def tpex_load_codes():
    print("取得上櫃公司清單（openapi mopsfin_t187ap03_O）…", flush=True)
    state, j = http_get_json(TPEX_COMPANIES)
    if state != "ok" or not isinstance(j, list):
        print("!! 上櫃公司清單抓取失敗，中止")
        sys.exit(2)
    codes = sorted({str(r.get("SecuritiesCompanyCode", "")).strip() for r in j})
    codes = [c for c in codes if len(c) == 4 and c.isdigit()]
    print(f"共 {len(codes)} 個上櫃普通股代號", flush=True)
    return codes


def tpex_weekdays(year):
    d = datetime.date(year, 1, 1)
    end = datetime.date(year, 12, 31)
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += datetime.timedelta(days=1)


def tpex_scan_year(fetcher, year, codes):
    """掃一個完結年度。回傳 (code -> entry|None)。任何一天 blocked 直接中止（Fetcher 已有降溫機制）"""
    universe = set(codes)
    monthly = {}          # code -> {mon: {"closes":[], "hi":x, "lo":x}}
    dec_days = []
    n_days = 0
    for d in tpex_weekdays(year):
        state, day = tpex_fetch_daily(fetcher, d)
        if state == "blocked":
            print(f"!! {d} dailyQuotes 連續被擋，中止本年掃描（年度未記完成，重跑續補）", flush=True)
            return None
        if state == "empty":
            continue
        n_days += 1
        if d.month == 12:
            dec_days.append(d)
        for code, (c, h, l) in day.items():
            if code not in universe:
                continue
            m = monthly.setdefault(code, {}).setdefault(d.month, {"closes": [], "hi": h, "lo": l})
            m["closes"].append(c)
            m["hi"] = max(m["hi"], h)
            m["lo"] = min(m["lo"], l)
        if n_days % 40 == 0:
            print(f"  .. {year} 已掃 {n_days} 個交易日｜累計請求 {fetcher.n_req}", flush=True)

    ratios = {}           # code -> {"pe":[], "pb":[], "y":[]}
    for d in dec_days:
        state, day = tpex_fetch_pera(fetcher, d)
        if state == "blocked":
            print(f"!! {d} pera 連續被擋，中止本年掃描", flush=True)
            return None
        if state == "empty":
            continue
        for code, (pe, pb, y) in day.items():
            if code not in universe:
                continue
            r = ratios.setdefault(code, {"pe": [], "pb": [], "y": []})
            if pe is not None and pe > 0:
                r["pe"].append(pe)
            if pb is not None and pb > 0:
                r["pb"].append(pb)
            if y is not None and y > 0:
                r["y"].append(y)

    def mean(a):
        return sum(a) / len(a) if a else None

    out = {}
    for code in codes:
        mm = monthly.get(code)
        if not mm:
            out[code] = None
            continue
        months = [{"mon": mon,
                   "hi": v["hi"], "lo": v["lo"],
                   "avg": mean(v["closes"])} for mon, v in sorted(mm.items())]
        price = {
            "hi": max(m["hi"] for m in months),
            "lo": min(m["lo"] for m in months),
            "avg": mean([m["avg"] for m in months]),
            "months": months,
        }
        r = ratios.get(code, {"pe": [], "pb": [], "y": []})
        pe, pb, yl = mean(r["pe"]), mean(r["pb"]), mean(r["y"])
        out[code] = {
            "hi": price["hi"],
            "lo": price["lo"],
            "avg": rnd(price["avg"]),
            "pe": rnd(pe),
            "pb": rnd(pb),
            "yield": rnd(yl),
            "ref": rnd(ref_price(price)),
        }
    return out


def tpex_fetch_companies():
    """上櫃公司完整清單（openapi，英文欄位、字串常帶尾隨空格）。回傳 snap 用精簡物件列表。"""
    state, j = http_get_json(TPEX_COMPANIES)
    if state != "ok" or not isinstance(j, list):
        return None
    out = []
    for r in j:
        code = str(r.get("SecuritiesCompanyCode", "")).strip()
        if len(code) != 4 or not code.isdigit():
            continue
        out.append({
            "c": code,
            "n": str(r.get("CompanyAbbreviation") or r.get("CompanyName") or "").strip(),
            "f": str(r.get("CompanyName") or "").strip(),
            "i": str(r.get("SecuritiesIndustryCode") or "").strip(),
            "ch": str(r.get("Chairman") or "").strip(),
            "cap": num(r.get("Paidin.Capital.NTDollars")),
            "est": str(r.get("DateOfIncorporation") or "").strip(),
            "ipo": str(r.get("DateOfListing") or "").strip(),
        })
    return out


def run_tpex_snap(args):
    """上櫃每日快照＋當年逐月累計（Actions 平日盤後跑）。TPEX 擋 Cloudflare 出口且無 CORS，
    前端上櫃「即時」資料一律改讀本模式產生的同源靜態檔：
      data/tpex_snap.json  公司清單＋最近交易日 PE/PB/殖利率/收盤
                           {"updated","date","companies":[{c,n,f,i,ch,cap,est,ipo}],"q":{code:{pe,pb,yield,close}}}
      data/tpex_ytd.json   當年逐月 {"year","last","m":{code:{"月":{hi,lo,sum,n}}}}
                           sum=收盤加總、n=交易日數 -> 月均=sum/n（同 tpex_scan_year 的 mean(closes) 語意）
    斷點＝ytd 的 last 日期；「今天」empty（假日/尚未發佈）不推進 last，下次自動續補；
    blocked 中止但已累計日先落檔。重複執行冪等。跨年自動重建當年檔（完結年由 pricedata.yml 落地）。"""
    tz_today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date()
    fetcher = Fetcher(args.delay)

    print("取得上櫃公司清單…", flush=True)
    companies = tpex_fetch_companies()
    if companies is None:
        print("!! 公司清單抓取失敗，中止（不寫檔）")
        sys.exit(2)
    codes = {c["c"] for c in companies}
    print(f"上櫃普通股 {len(companies)} 檔", flush=True)

    ytd = None
    if TPEX_YTD.exists():
        try:
            ytd = json.loads(TPEX_YTD.read_text())
        except Exception:
            ytd = None
    if not ytd or ytd.get("year") != tz_today.year:
        ytd = {"year": tz_today.year, "last": f"{tz_today.year - 1}-12-31", "m": {}}
    last = datetime.date.fromisoformat(ytd["last"])

    latest_close = {}
    latest_date = None
    changed = False
    d = max(last + datetime.timedelta(days=1), datetime.date(tz_today.year, 1, 1))
    while d <= tz_today:
        if d.weekday() >= 5:
            ytd["last"] = d.isoformat()
            d += datetime.timedelta(days=1)
            continue
        state, day = tpex_fetch_daily(fetcher, d)
        if state == "blocked":
            print(f"!! {d} dailyQuotes 連續被擋，中止（已累計日先落檔，下次續跑）", flush=True)
            break
        if state == "empty":
            if d == tz_today:
                print(f"{d} 尚無資料（假日或未發佈），不推進斷點", flush=True)
                break
            ytd["last"] = d.isoformat()
            d += datetime.timedelta(days=1)
            continue
        mon = str(d.month)
        n_in = 0
        for code, (c, h, l) in day.items():
            if code not in codes:
                continue
            m = ytd["m"].setdefault(code, {}).setdefault(mon, {"hi": h, "lo": l, "sum": 0.0, "n": 0})
            m["hi"] = max(m["hi"], h)
            m["lo"] = min(m["lo"], l)
            m["sum"] = round(m["sum"] + c, 2)
            m["n"] += 1
            latest_close[code] = c
            n_in += 1
        latest_date = d
        ytd["last"] = d.isoformat()
        changed = True
        print(f"{d} 入帳 {n_in} 檔", flush=True)
        d += datetime.timedelta(days=1)

    if changed:
        TPEX_YTD.write_text(json.dumps(ytd, separators=(",", ":"), ensure_ascii=False))
        print(f"tpex_ytd.json 已更新（last={ytd['last']}）", flush=True)

    if latest_date is None:
        # 本次無新入帳（重複執行等）：從今天往回找最近交易日，讓 snap 可自癒（如前次 pera 失敗）
        for i in range(6):
            dd = tz_today - datetime.timedelta(days=i)
            if dd.weekday() >= 5:
                continue
            state, day = tpex_fetch_daily(fetcher, dd)
            if state == "blocked":
                break
            if state == "ok" and day:
                latest_date = dd
                latest_close = {c: v[0] for c, v in day.items() if c in codes}
                break
    if latest_date is None:
        print("找不到可用交易日，snap 不更新")
        return

    old = None
    if TPEX_SNAP.exists():
        try:
            old = json.loads(TPEX_SNAP.read_text())
        except Exception:
            old = None
    date_str = latest_date.strftime("%Y%m%d")
    if old and old.get("date") == date_str and old.get("companies") == companies:
        print(f"snap 已是最新（資料日 {date_str}），不重寫")
        return

    state, pera = tpex_fetch_pera(fetcher, latest_date)
    if state != "ok" or not pera:
        print("!! pera 抓取失敗，snap 不更新（ytd 已落檔，下次自癒）")
        return
    q = {}
    for code in codes:
        pe = pb = yl = None
        if code in pera:
            pe, pb, yl = pera[code]
        close = latest_close.get(code)
        if close is None and pe is None and pb is None and yl is None:
            continue
        q[code] = {"pe": pe, "pb": pb, "yield": yl, "close": close}
    snap = {
        "updated": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
        "date": date_str,
        "companies": companies,
        "q": q,
    }
    TPEX_SNAP.write_text(json.dumps(snap, separators=(",", ":"), ensure_ascii=False))
    print(f"tpex_snap.json 已更新（資料日 {date_str}｜quotes {len(q)} 檔）")


def tpex_load_progress():
    if TPEX_PROGRESS.exists():
        try:
            return set(json.loads(TPEX_PROGRESS.read_text())["done"])
        except Exception:
            return set()
    return set()


def tpex_save_progress(done):
    TPEX_PROGRESS.write_text(json.dumps({"done": sorted(done)}))


def run_tpex_sync(args, prune):
    now_y = datetime.date.today().year
    from_y = args.from_year or (now_y - 10)
    years = [str(y) for y in range(from_y, now_y)]
    codes = tpex_load_codes()
    fetcher = Fetcher(args.delay)
    done = tpex_load_progress()
    t0 = time.time()
    n_years = 0

    def year_on_disk(y):
        """斷點檔遺失時（如 Actions runner）以檔案內容判定年度是否已完成：
        抽 8 個代號，全部檔案存在且含該年鍵（值或 null）即視為完成（年度為原子寫入）"""
        sample = codes[:: max(1, len(codes) // 8)][:8]
        if not sample:
            return False
        for c in sample:
            doc = read_price_file(c)
            if not doc or y not in doc.get("y", {}):
                return False
        return True

    for y in years:
        if not args.force and (y in done or year_on_disk(y)):
            if y not in done:
                done.add(y)
                tpex_save_progress(done)
            continue
        print(f"===== 掃描 {y} 年（全上櫃逐日）=====", flush=True)
        result = tpex_scan_year(fetcher, int(y), codes)
        if result is None:
            break
        n_with = sum(1 for v in result.values() if v)
        for code, entry in result.items():
            doc = read_price_file(code) or {"code": code, "updated": None, "y": {}}
            doc.setdefault("y", {})
            doc["y"][y] = entry
            if prune:
                for k in [k for k in doc["y"].keys() if k < years[0]]:
                    del doc["y"][k]
            write_price_file(code, doc)
        done.add(y)
        tpex_save_progress(done)
        print(f"{y}：有資料 {n_with} 檔、無資料 {len(result) - n_with} 檔｜累計請求 {fetcher.n_req}", flush=True)
        n_years += 1

    dt = time.time() - t0
    print("\n===== TPEX 統計 =====")
    print(f"完成年度 {n_years}｜請求 {fetcher.n_req}（ok {fetcher.n_ok}／empty {fetcher.n_empty}／blocked {fetcher.n_blocked}）｜耗時 {dt/60:.1f} 分")
    remain = [y for y in years if y not in done]
    if remain:
        print(f"!! 未完成年度：{remain}，重跑同指令續補（斷點 data/tpex_price_progress.json）")


def run_tpex_probe(args):
    fetcher = Fetcher(args.delay)
    probes = [datetime.date(2016, 1, 4), datetime.date(2024, 6, 4)]
    if args.date:
        y, m, d = args.date.split("/")
        probes = [datetime.date(int(y), int(m), int(d))]
    for d in probes:
        print(f"\n=== dailyQuotes {d}")
        state, day = tpex_fetch_daily(fetcher, d)
        if state == "ok":
            sample = day.get("5483") or next(iter(day.items()))[1]
            print(f"OK：{len(day)} 檔｜5483 或首檔 (close,hi,lo)={sample}")
        else:
            print(f"{state}")
        print(f"=== pera {d}")
        state, day = tpex_fetch_pera(fetcher, d)
        if state == "ok":
            sample = day.get("5483") or next(iter(day.items()))[1]
            print(f"OK：{len(day)} 檔｜5483 或首檔 (pe,pb,yield)={sample}")
        else:
            print(f"{state}")
    codes = tpex_load_codes()
    print(f"公司清單前 5：{codes[:5]}")


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
    ap.add_argument("--force", action="store_true", help="TPEX：忽略年度斷點全部重掃")
    ap.add_argument("--date", help="tpex-probe 指定日期 YYYY/MM/DD")
    ap.add_argument("--tpex-probe", action="store_true", help="TPEX 端點驗證（先跑，貼輸出）")
    ap.add_argument("--tpex-backfill", action="store_true", help="TPEX 全上櫃逐年掃描回補")
    ap.add_argument("--tpex-update", action="store_true", help="TPEX 每月增量（Actions 用）")
    ap.add_argument("--tpex-snap", action="store_true", help="TPEX 每日快照＋當年逐月累計（Actions 平日盤後）")
    args = ap.parse_args()

    if args.tpex_snap:
        run_tpex_snap(args)
    elif args.tpex_probe:
        run_tpex_probe(args)
    elif args.tpex_backfill:
        run_tpex_sync(args, prune=False)
    elif args.tpex_update:
        run_tpex_sync(args, prune=True)
    elif args.probe:
        run_probe(args)
    elif args.backfill:
        run_sync(args, prune=False)
    elif args.update:
        run_sync(args, prune=True)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
