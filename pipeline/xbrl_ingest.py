#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xbrl_ingest.py — 用本機 XBRL 整批檔建立/覆寫 data/fin/{code}.json 的 q 欄位。

輸入：XBRL/tifrs-{YYYY}Q{n}/  底下扁平的 *.html（2019+，inline XBRL）
      或 *.xml（2018，plain XBRL）。兩種格式自動偵測，context 皆為語意字串
      （From{Y}0101To{季末}、AsOf{季末}）。

用法：
    python3 pipeline/xbrl_ingest.py --quarter 2026Q1          # 跑單季（會寫檔）
    python3 pipeline/xbrl_ingest.py --quarter 2026Q1 --dry-run  # 只比對出統計，不寫檔
    python3 pipeline/xbrl_ingest.py --all                     # 2018Q1→2026Q1 逐季，每季存檔斷點

合併策略：
  * 除 bvps 外，XBRL 有值→覆寫；XBRL 無值→保留爬蟲舊值。
  * bvps 特規：爬蟲既有值優先、XBRL 計算值只補缺（面額非10元/特別股公司會算錯），
    出入單獨列清單供抽查。
  * div（股利）陣列絕不更動。
"""
import re, os, sys, json, glob, argparse, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIN_DIR = os.path.join(REPO, 'data', 'fin')
XBRL_DIR = os.path.join(REPO, 'XBRL')
PROGRESS = os.path.join(REPO, 'data', 'xbrl_progress.json')  # --all 逐季斷點
QEND = {'1': '0331', '2': '0630', '3': '0930', '4': '1231'}

IXTAG = re.compile(r'<ix:nonFraction\b([^>]*)>([^<]*)</ix:nonFraction>')
ATTR = re.compile(r'([\w:-]+)="([^"]*)"')
# plain-xbrl：<prefix:Local ...>純數字</prefix:Local>
PLAINTAG = re.compile(r'<([a-zA-Z][\w-]*:[A-Za-z][\w-]*)\s+([^>]*?)>\s*(-?[\d,]+(?:\.\d+)?)\s*</\1>')
FNAME = re.compile(r'tifrs-(fr\d)-m\d-([a-z]+)-([a-z]+)-([0-9A-Z]+)-(\d{4})Q(\d)\.(html|xml)$')

# field -> (element(s), kind[P期間/T時點], divide1000, negate)
# element 可為 tuple，依序 fallback（例：lti 舊 taxonomy 用單數）
FIELDS = {
    'inv':    ('ifrs-full:Inventories', 'T', 1, 0),
    'ar':     ('tifrs-bsci-ci:AccountsReceivableNet', 'T', 1, 0),
    'ap':     ('ifrs-full:TradeAndOtherCurrentPayablesToTradeSuppliers', 'T', 1, 0),
    'ppe':    ('ifrs-full:PropertyPlantAndEquipment', 'T', 1, 0),
    'cash_bs':('ifrs-full:CashAndCashEquivalents', 'T', 1, 0),
    'stb':    ('ifrs-full:ShorttermBorrowings', 'T', 1, 0),
    'ltb':    ('ifrs-full:LongtermBorrowings', 'T', 1, 0),
    'lti':    (('ifrs-full:InvestmentsAccountedForUsingEquityMethod',
                'ifrs-full:InvestmentAccountedForUsingEquityMethod'), 'T', 1, 0),
    'dep':    ('ifrs-full:AdjustmentsForDepreciationExpense', 'P', 1, 0),
    'ocf':    ('ifrs-full:CashFlowsFromUsedInOperatingActivities', 'P', 1, 0),
    'capex':  ('ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities', 'P', 1, 1),
    'rev':    ('ifrs-full:Revenue', 'P', 1, 0),
    'gp':     ('ifrs-full:GrossProfit', 'P', 1, 0),
    'op':     ('ifrs-full:ProfitLossFromOperatingActivities', 'P', 1, 0),
    'nonop':  ('tifrs-bsci-ci:NonoperatingIncomeAndExpenses', 'P', 1, 0),
    'ni':     ('ifrs-full:ProfitLoss', 'P', 1, 0),
    'eps':    ('ifrs-full:BasicEarningsLossPerShare', 'P', 0, 0),  # 元，不除千
    'assets': ('ifrs-full:Assets', 'T', 1, 0),
    'liab':   ('ifrs-full:Liabilities', 'T', 1, 0),
    'eq':     ('ifrs-full:Equity', 'T', 1, 0),
    'ca':     ('ifrs-full:CurrentAssets', 'T', 1, 0),
    'cl':     ('ifrs-full:CurrentLiabilities', 'T', 1, 0),
}
FIELD_ORDER = list(FIELDS.keys())  # 22 欄；bvps 另計


def build(text):
    """回傳 dict[(element, contextRef)] -> 數值（元），首次出現者為準（正文表格）。"""
    d = {}
    ix = IXTAG.findall(text)
    if ix:  # inline XBRL
        for attrs, body in ix:
            a = dict(ATTR.findall(attrs))
            nm, ctx = a.get('name'), a.get('contextRef')
            if not nm or not ctx:
                continue
            try:
                v = float(body.replace(',', ''))
            except ValueError:
                continue
            if a.get('sign') == '-':
                v = -abs(v)
            if a.get('scale'):
                v *= 10 ** int(a['scale'])
            d.setdefault((nm, ctx), v)
    else:   # plain XBRL（2018）：完整元、無 scale
        for nm, attrs, body in PLAINTAG.findall(text):
            a = dict(ATTR.findall(attrs))
            ctx = a.get('contextRef')
            if not ctx:
                continue
            try:
                v = float(body.replace(',', ''))
            except ValueError:
                continue
            d.setdefault((nm, ctx), v)
    return d


def _lookup(d, els, ctx):
    if isinstance(els, str):
        els = (els,)
    for el in els:
        if (el, ctx) in d:
            return d[(el, ctx)]
    return None


def extract(d, year, q):
    """從 (element,ctx)->值 的 map 抽出 23 欄（單位：千元；eps/bvps=元；capex 負值）。"""
    pc = 'From%s0101To%s%s' % (year, year, QEND[q])
    tc = 'AsOf%s%s' % (year, QEND[q])
    out = {}
    for f, (els, kind, div, neg) in FIELDS.items():
        v = _lookup(d, els, pc if kind == 'P' else tc)
        if v is not None:
            if div:
                v /= 1000.0
            if neg:
                v = -abs(v)
        out[f] = v
    # bvps = 歸屬母公司權益(千元) ÷ (實收資本額(千元) ÷ 10)
    pe = d.get(('ifrs-full:EquityAttributableToOwnersOfParent', tc))
    cap = d.get(('ifrs-full:IssuedCapital', tc))
    out['bvps'] = round((pe / 1000.0) / ((cap / 1000.0) / 10), 2) if (pe and cap) else None
    return out


def close(a, b):
    if a is None or b is None:
        return False
    return abs(a - b) < 0.5 or abs(a - b) / (abs(b) or 1) < 0.001


def choose_files(qdir):
    """glob 該季所有檔，依代號去重：優先 cr，無 cr 才 ir。回傳 [(path, ind, report, code)]。"""
    paths = sorted(glob.glob(os.path.join(qdir, '*.html')) +
                   glob.glob(os.path.join(qdir, '*.xml')))
    best = {}  # code -> (rank, path, ind, report)  rank: cr=0, ir=1, 其他=2
    skipped = []
    for p in paths:
        m = FNAME.search(os.path.basename(p))
        if not m:
            skipped.append(os.path.basename(p))
            continue
        _fr, ind, report, code = m.group(1), m.group(2), m.group(3), m.group(4)
        rank = 0 if report == 'cr' else (1 if report == 'ir' else 2)
        if code not in best or rank < best[code][0]:
            best[code] = (rank, p, ind, report)
    chosen = [(v[1], v[2], v[3], code) for code, v in best.items()]
    return chosen, len(paths), skipped


def load_json(code):
    path = os.path.join(FIN_DIR, '%s.json' % code)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as fh:
            return json.load(fh), False
    return {'code': code, 'q': {}, 'div': []}, True


def save_json(code, obj):
    obj['updated'] = datetime.date.today().isoformat()
    path = os.path.join(FIN_DIR, '%s.json' % code)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(obj, separators=(',', ':'), ensure_ascii=False))


def merge_quarter(ext, obj, qk):
    """把抽出的 23 欄合併進 obj['q'][qk]。回傳逐檔比對計數與出入紀錄。"""
    q = obj.setdefault('q', {})
    old = dict(q.get(qk, {}))
    new = dict(old)  # 保留 XBRL 沒有的舊欄位
    agree = fill = diff = 0
    diffs = []       # (field, old, xbrl)
    for f in FIELD_ORDER:
        xv = ext[f]
        if xv is None:
            continue                       # XBRL 無值 → 保留舊
        ov = old.get(f)
        if ov is None:
            new[f] = xv; fill += 1
        elif close(xv, ov):
            new[f] = xv; agree += 1
        else:
            new[f] = xv; diff += 1
            diffs.append((f, ov, xv))
    # bvps 特規：爬蟲值優先，XBRL 只補缺
    xb, ob = ext['bvps'], old.get('bvps')
    bvps_diff = None
    if ob is None:
        if xb is not None:
            new['bvps'] = xb
    else:
        new['bvps'] = ob
        if xb is not None and not close(xb, ob):
            bvps_diff = (ob, xb)
    q[qk] = new
    return agree, fill, diff, diffs, bvps_diff


def process_quarter(qk, dry_run=False, top=10):
    year, q = qk[:4], qk[5]
    qdir = os.path.join(XBRL_DIR, 'tifrs-%s' % qk)
    if not os.path.isdir(qdir):
        print('  ✗ 找不到資料夾：%s' % qdir)
        return
    chosen, scanned, skipped_names = choose_files(qdir)
    cr = sum(1 for _p, _i, r, _c in chosen if r == 'cr')
    ir = sum(1 for _p, _i, r, _c in chosen if r == 'ir')
    other = len(chosen) - cr - ir

    parse_fail = []          # (filename, reason)
    fin_empty = []           # (code, ind)  rev 留空（金融/特殊業）
    new_codes = 0
    tot_agree = tot_fill = tot_diff = 0
    all_diffs = []           # (code, field, old, xbrl)
    bvps_diffs = []          # (code, old, xbrl)
    parsed_ok = 0

    for path, ind, _report, code in chosen:
        try:
            with open(path, encoding='utf-8', errors='replace') as fh:
                text = fh.read()
            d = build(text)
            ext = extract(d, year, q)
        except Exception as e:  # noqa
            parse_fail.append((os.path.basename(path), repr(e)))
            continue
        # 解析成功判定：至少抓到一個核心欄（assets/ni/eq 任一）
        if ext.get('assets') is None and ext.get('ni') is None and ext.get('eq') is None:
            parse_fail.append((os.path.basename(path), 'no core fields (assets/ni/eq all empty)'))
            continue
        parsed_ok += 1
        if ext.get('rev') is None:
            fin_empty.append((code, ind))

        obj, is_new = load_json(code)
        if is_new:
            new_codes += 1
        agree, fill, diff, diffs, bvps_diff = merge_quarter(ext, obj, qk)
        tot_agree += agree; tot_fill += fill; tot_diff += diff
        for f, ov, xv in diffs:
            all_diffs.append((code, f, ov, xv))
        if bvps_diff:
            bvps_diffs.append((code, bvps_diff[0], bvps_diff[1]))
        if not dry_run:
            save_json(code, obj)
    # 逐季斷點：整季跑完才標記（中斷未標記 → 重跑該季，merge 冪等不會壞資料）。
    # 解析失敗檔已在統計列出，重跑也修不好 malformed 檔，故照常標記完成，需補跑用 --force。
    if not dry_run:
        mark_done(qk)

    # ---- 統計輸出 ----
    print('╔═ %s %s ═' % (qk, '(dry-run，未寫檔)' if dry_run else ''))
    print('║ 掃描檔數 %d｜選用 %d（cr %d / ir %d%s）｜解析成功 %d / 解析失敗 %d'
          % (scanned, len(chosen), cr, ir, (' / 其他 %d' % other) if other else '', parsed_ok, len(parse_fail)))
    print('║ 新建代號（上櫃等）%d 檔' % new_codes)
    print('║ 欄位比對：一致 %d｜補缺 %d｜出入 %d' % (tot_agree, tot_fill, tot_diff))
    # 金融/特殊業 rev 留空
    if fin_empty:
        by_ind = {}
        for _c, i in fin_empty:
            by_ind[i] = by_ind.get(i, 0) + 1
        brk = '，'.join('%s×%d' % (k, v) for k, v in sorted(by_ind.items()))
        print('║ rev/nonop 留空（金融/特殊業）：%d 檔  [%s]' % (len(fin_empty), brk))
    else:
        print('║ rev/nonop 留空（金融/特殊業）：0 檔')
    # 解析失敗檔名
    if parse_fail:
        print('║ ⚠ 解析失敗檔（%d）：' % len(parse_fail))
        for name, reason in parse_fail[:30]:
            print('║    %s  — %s' % (name, reason))
        if len(parse_fail) > 30:
            print('║    …另 %d 檔' % (len(parse_fail) - 30))
    # 出入前 N（非 bvps）
    if all_diffs:
        print('║ 出入明細前 %d（欄位｜舊爬蟲 → XBRL，XBRL 為準）：' % top)
        # 依相對差距排序，最大者先
        def relmag(r):
            _c, _f, ov, xv = r
            return abs(xv - ov) / (abs(ov) or 1)
        for code, f, ov, xv in sorted(all_diffs, key=relmag, reverse=True)[:top]:
            print('║    %-6s %-7s %s → %s' % (code, f, _fmt(f, ov), _fmt(f, xv)))
        if len(all_diffs) > top:
            print('║    …另 %d 筆出入' % (len(all_diffs) - top))
    # bvps 出入（單獨列，保留爬蟲值）
    if bvps_diffs:
        print('║ bvps 出入（保留爬蟲值，XBRL 計算值僅供抽查）共 %d 筆，前 %d：' % (len(bvps_diffs), top))
        for code, ov, xv in sorted(bvps_diffs, key=lambda r: abs(r[2] - r[1]), reverse=True)[:top]:
            print('║    %-6s 爬蟲 %.2f ｜ XBRL計算 %.2f' % (code, ov, xv))
    print('╚═')


def _fmt(f, v):
    if v is None:
        return '—'
    return '%.2f' % v if f in ('eps', 'bvps') else '%.0f' % v


def load_progress():
    if os.path.exists(PROGRESS):
        with open(PROGRESS, encoding='utf-8') as fh:
            return set(json.load(fh).get('done', []))
    return set()


def mark_done(qk):
    done = load_progress()
    done.add(qk)
    with open(PROGRESS, 'w', encoding='utf-8') as fh:
        json.dump({'done': sorted(done)}, fh, ensure_ascii=False)


def list_quarters():
    qs = []
    for d in sorted(glob.glob(os.path.join(XBRL_DIR, 'tifrs-*Q*'))):
        m = re.search(r'tifrs-(\d{4}Q\d)$', d)
        if m:
            qs.append(m.group(1))
    return qs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--quarter', help='單季，如 2026Q1')
    ap.add_argument('--all', action='store_true', help='2018Q1→2026Q1 逐季')
    ap.add_argument('--dry-run', action='store_true', help='只比對出統計，不寫檔')
    ap.add_argument('--top', type=int, default=10, help='出入明細列出筆數（預設 10）')
    ap.add_argument('--force', action='store_true',
                    help='--all 時忽略斷點，重跑已完成的季')
    args = ap.parse_args()

    if args.quarter:
        # 單季一律處理（明確指定），完成後也記入斷點
        process_quarter(args.quarter, dry_run=args.dry_run, top=args.top)
    elif args.all:
        done = set() if args.force else load_progress()
        for qk in list_quarters():
            if qk in done:
                print('⏭  %s 已完成（斷點跳過，--force 可重跑）\n' % qk)
                continue
            process_quarter(qk, dry_run=args.dry_run, top=args.top)
            print()
    else:
        ap.error('需指定 --quarter YYYYQn 或 --all')


if __name__ == '__main__':
    main()
