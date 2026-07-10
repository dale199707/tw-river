# CLAUDE.md — tw-river 台股估價河流圖

> 交接檔（v12，2026-07-10）。新對話／Claude Code 請先完整讀完本檔再動手。
> **上櫃（TPEX）前端支援全案已完成**（2026-07-10 晚）：公司清單/快照/當日收盤/當年價格/股利全通，
> 網站已可查上市＋上櫃普通股。架構走「Actions 每日靜態檔」路線——因 **TPEX 封鎖 Cloudflare 出口 IP
> 且所有端點無 CORS**（Worker 代理與瀏覽器直連皆不可行），細節見「上櫃前端（TPEX）」專章，之後任何
> TPEX 相關工作必讀。下一件事見「交接時狀態」待辦。

## 專案定位

取代 Dale 原本的兩檔 Excel 系統：screen.xls（快速瀏覽＋保留股＋篩選）與 Investment decision tool.xls（估價引擎＋六張檢驗圖）。網頁雙模式：「⚡ 快速瀏覽」＝一頁指標＋六檢驗圖、←→ 連發逛；「▦ 完整模式」＝五分頁完整分析。資料全自動更新，無後端資料庫（靜態 JSON on GitHub Pages）。

- 網站：https://dale199707.github.io/tw-river/
- Repo：github.com/dale199707/tw-river（本機 clone `~/Desktop/tw-river-repo`）
- Worker：https://tw-river-api.dale199707.workers.dev（原始碼備份於 repo 根目錄 worker.js；改動需手動貼到 Cloudflare Edit code → Deploy）

## ⚠️ 立即注意事項

1. **`~/Desktop/tw-river-repo/XBRL/` 放著 33 季的原始檔（數 GB），絕對不能 commit**。`.gitignore` 已含 `XBRL/`；任何 git 操作仍只 add 指定檔案、不用 `git add -A`。
2. **TPEX 封鎖 Cloudflare Workers 出口 IP**（302 無限重導向至 /errors，openapi 與 www 端點一體封鎖），且全部端點**無 CORS**。任何上櫃「即時」需求都只能走 Actions 產生靜態檔（Actions/住宅 IP 可正常存取），不要再嘗試 Worker 代理或瀏覽器直連。
3. `data/fin/*.json` 現以 **XBRL 為主要來源**（33 季全數入庫，上市＋上櫃）；爬蟲舊值僅在 XBRL 無值欄位保留。`div` 陣列仍為股利爬蟲來源，XBRL 不含股利。

---

## XBRL 建庫（✅ 已完成，2026-07-09；本節為每季更新參考）

### 完成狀態
- **33 季（2018Q1–2026Q1）全部入庫**：上市＋上櫃財報皆寫入 `data/fin/{code}.json` 的 `q` 欄位，已 commit push、GitHub Pages 部署生效。
- 上櫃公司已建新檔（格式同上市：`{"code","updated","q":{...}}`），為待辦 2（TPEX 前端支援）鋪路完成。
- 解析器：`pipeline/xbrl_ingest.py`（已進 repo）。`fin_progress.json` 與 `--retry-missing` 正式退役，可自 repo/程式碼刪除（列入待辦 4 清理）。
- 原始檔仍在 `~/Desktop/tw-river-repo/XBRL/`（gitignored，勿 commit）。

### 每季更新方式
Dale 每季手動下載新一季 XBRL 包解壓至 `XBRL/tifrs-{YYYY}Q{n}/`，執行：
```
python3 pipeline/xbrl_ingest.py --quarter 2026Q2
python3 pipeline/fetch_mops.py --build-screen
```
再 add 指定檔案 commit push（只 add `data/fin data/screen.json`，勿 `git add -A`）。

### xbrl_ingest.py 關鍵知識（除錯/擴充時參考）
- **指令**：`--quarter YYYYQn`（單季寫檔＋記斷點）、`--quarter ... --dry-run`（只比對）、`--all`（逐季、斷點自動跳過）、`--all --force`（全重跑）、`--top N`（出入清單筆數）。斷點檔 `data/xbrl_progress.json`（gitignored、機器本地）；merge 冪等，中斷重跑不壞資料。
- **格式自動偵測**：2018=plain XBRL（`.xml`，完整元無 scale）、2019+=inline XBRL（`.html`，`ix:nonFraction`×10^scale）。context 為語意字串：期間類 `From{Y}0101To{季末}`（**YTD 累計**，勿取單季 context），時點類 `AsOf{季末}`（精確匹配季末日）。
- **報表優先序 cr→ir**（合併優先，無 cr 才用個別；無「er」）；代號含英數（如 `0009A0`）。
- **值處理**：去逗號 → `sign="-"` 取負 → ×10^scale → ÷1000 千元；**eps/bvps 單位「元」不除千**；capex 轉負；同名元素同 context 取第一個（正文表格，排除附註）。
- **元素定案**：detail 11 欄照原對照表（inv/ar/ap/ppe/cash_bs/stb/ltb/lti=時點；dep/ocf/capex=期間）；op=`ifrs-full:ProfitLossFromOperatingActivities`；ni=`ifrs-full:ProfitLoss`（總額）；lti fallback 複數→單數（2018 舊 taxonomy）；bvps=歸屬母公司權益÷(實收資本額÷10)，**merge 採爬蟲值優先只補缺**（面額非 10 元/特別股會算錯）。
- **金融業**：ar/ap/inv 等元素不存在是正常，留空不硬湊；dep/ocf/capex/ppe 多數行業通用。
- **合併策略**：`q` 逐欄位合併（XBRL 有值覆寫、無值保留爬蟲舊值）；**`div` 陣列絕對不動**（股利另有爬蟲來源）。

---

## 歷史價格落地（✅ 全案完成，2026-07-10）

### 完成狀態
- **上市（TWSE）**：~1,086 檔 × 10 完結年度（FMSRFK＋BWIBBU 逐檔逐年），被擋待重試 0
- **上櫃（TPEX）**：891 檔 × 10 完結年度（2016–2025，逐日全市場掃描），未完成年度空
- 前端歷史年讀 `data/price/{code}.json`、當年走 /bundle（from=to=當年）、缺檔 fallback 原路徑——已部署驗證
- `pricedata.yml` 每月 6 日 05:30 台北跑 `--update`（上市）＋`--tpex-update`（上櫃）；Actions runner 無本地斷點檔時以 `year_on_disk()`（抽 8 檔含該年鍵）判定年度完成，避免全重掃；每年 1 月自動補前一完結年並修剪視窗外舊年
- 清理已完成：fetch_mops.py 656→278 行（僅剩股利＋build_screen，`--update`=近兩年股利＋build_screen）、findata.yml 改股利季更（4/5/8/11 月 16 日）並已重新 Enable、`data/fin_progress.json` 已自 repo 移除

### data/price/{code}.json 格式（上市上櫃同一格式，前端零區分）
`{"code","updated","y":{"2018":{hi,lo,avg,pe,pb,yield,ref}|null,...}}`——只存完結年度；null=已查證無資料；
ref=12 月月均價；欄位對齊 `buildYearData(y,{hi,lo,avg},{pe,pb,yield},ref)`。檔案本身即斷點、merge 冪等。

### price_ingest.py 指令備忘
```
--probe 2330 / --backfill / --update            上市（FMSRFK＋BWIBBU 逐檔）
--tpex-probe / --tpex-backfill / --tpex-update  上櫃（逐日全市場掃描）
--codes / --from-year / --delay / --limit / --force
```

### TPEX 端點與限流實戰知識（⚠️ 之後打 TPEX 必讀）
- **dailyQuotes（新版 API）**：`www/zh-tw/afterTrading/dailyQuotes?date=YYYY/MM/DD&response=json`，歷史可查到 2016+，全市場單日 OHLC；非交易日合法形狀＝**有 tables 鍵但無匹配表**（缺 tables 鍵＝限流頁，必須判 blocked 不可當空，否則月資料默默少算）
- **pera（舊版 API）**：`web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&o=json&d={民國}/{MM}/{DD}`，全市場單日 PE/每股股利/股利年度/殖利率/淨值比，105/12 仍有 712 檔
- **限流行為（2026-07-10 實測）**：夜間/清晨可連跑 ~1,700 請求；白天約每 ~300 請求封一輪 IP、封鎖期 10–30 分鐘，且**連 openapi 公司清單都會一起被封**。對策已內建：`COOLDOWNS=[120,300,600,1800]` 階梯降溫（成功即重置）＋整年原子寫入＋年度斷點，`--delay 3` 放著跑即可自癒
- 上櫃公司清單：openapi `mopsfin_t187ap03_O`（英文欄位 `SecuritiesCompanyCode`），過濾 4 位純數字＝普通股
- TWSE 側教訓：delay 1.5 全程僅 3 blocked（比 TPEX 寬鬆得多）

## 上櫃前端（TPEX）（✅ 全案完成，2026-07-10 晚）

### 封鎖實況（決定架構的關鍵，勿重蹈）
- **Cloudflare Workers 出口被 TPEX 封**：openapi 與 www.tpex.org.tw 一體 302 → /errors 無限迴圈（"Too many redirects"）。與 Google News 擋 Cloudflare 同類
- **全端點無 CORS**（openapi 三支＋dailyQuotes＋tradingStock 皆無 access-control-allow-origin）→ 瀏覽器直連也不可行
- **GitHub Actions 出口可正常存取**（已以 tpexprobe workflow 實證，五端點全通）；住宅 IP 也可。Actions runner IP 品質不一（Azure 共用池偶有髒 IP），pipeline 已容錯

### 架構：Actions 每日靜態檔
- `.github/workflows/tpexsnap.yml`：平日 16:40 台北跑 `price_ingest.py --tpex-snap --delay 2`，commit 兩檔：
  - `data/tpex_snap.json`：`{"updated","date","companies":[{c,n,f,i,ch,cap,est,ipo}],"q":{code:{pe,pb,yield,close}}}`（891 檔；來源 openapi 公司清單＋pera 當日；close 來自 dailyQuotes）
  - `data/tpex_ytd.json`：`{"year","last","m":{code:{"月":{hi,lo,sum,n}}}}` 當年逐月累計，月均=sum/n（同 pipeline mean(closes) 語意）
- `--tpex-snap` 特性：斷點＝ytd 的 last 日期；「今天」empty（假日/未發佈）**不推進** last、隔日自動續補；blocked 中止但已累計日先落檔；重跑冪等；跨年自動重建（完結年由 pricedata.yml 落地 data/price）；公司清單失敗沿用舊 snap 清單續跑（僅首次且無舊檔才中止）
- **上櫃收盤時效性＝每日 Actions 跑完後（約 16:4x），非即時**；上市維持 Worker /today 即時。此為封鎖下的必要取捨
- 首跑實績：回補 2026 全年 130 交易日 19 分（含降溫自癒），資料與端點 probe 交叉驗證全對

### 前端接法（index.html）
- 公司清單：TWSE openapi ＋ `data/tpex_snap.json` concat，公司物件有 `market:"twse"|"tpex"` 欄；quotes 併入 snap 的 q。snap 抓取失敗退化為僅上市
- `loadHistory(code,years,quote,market)`：歷史年走 data/price（雙市場同格式）；**當年**上市走 /bundle、上櫃讀 `data/tpex_ytd.json`（模組級記憶體快取，全站僅載一次 ~290KB，`tpexYearPrice()` 組月資料）；上櫃當年 ratio/ref 一律用快照。上櫃無靜態檔 fallback（新掛牌）只補當年
- 快照 localStorage 鍵已升版 `twri-snap2-`（v1 無上櫃需失效）
- Worker **完全沒動**（repo 原版），v11 期間短暫部署過的 TPEX 版已作廢撤回

### 股利（fetch_mops.py）
- `fetch_dividends_year(roc, typek)`：TYPEK=sii 上市／otc 上櫃；暖機 per-TYPEK（`_DIV_WARMED` set——v10 清理時誤刪宣告曾致 NameError，已修）
- `run_dividends` 每年雙市場都抓（merge 冪等）；`--update`（findata.yml 季更）自動涵蓋雙市場
- `--probe-div 113 --typek otc` 可單測；已回補 105–115 雙市場、寫入 1779 檔

## 個股消息分頁（2026-07-10 已部署，經 Dale 調整定版）

完整模式第六分頁「消息」（`tab==="news"`，NewsView）——**只有兩塊**：營運新聞＋法說會外連。今日重大訊息欄位做過又移除（Dale 決定不要；t187ap04_L 的知識留存：中文 key 有尾隨空格、日期民國 7 碼、僅當日僅上市）。
1. **營運新聞**：**瀏覽器直連** `ess.api.cnyes.com/ess/api/v1/news/keyword?q={公司簡稱}&limit=30`（cnyes 有 CORS；**Google News RSS 與部分站點會擋 Cloudflare 出口回 503**，直連用使用者 IP 避開），Worker `/news`（cnyes 版）為備援。過濾規則（`newsPick`，Dale 定版）：剝 `<mark>` 等 HTML 標籤；標題/摘要/keywordForTag 含公司名才收；排除 `^盤[中後]速報`；排除「公司名：」開頭（鉅亨自動轉載 MOPS 公告——可轉債、還本付息、子公司取得設備等）**但 `^鉅亨速報` 保留**（Factset 預估類 Dale 要）；排除加密貨幣類標籤；標題去重；新到舊取 10 則。文章連結 `news.cnyes.com/news/id/{newsId}`，publishAt=unix 秒
2. **法說會**：TWSE openapi 無法說會時程資料集（swagger grep「說明會」為空），以外連 MOPS `t100sb02_1` 按鈕呈現
法人目標價：Dale 決定**不做**（無免費官方來源）。

## 架構總覽

```
index.html（單檔 React 18 UMD + Babel 7.26.4 classic，繁中 UI，GitHub Pages）
├─ 快照（公司/PE/PB/殖利率/收盤）→ 上市：Worker /openapi/* → openapi.twse.com.tw（僅前一交易日）
│                                  上櫃：同源靜態 data/tpex_snap.json（Actions 每日）
├─ 當日收盤 ────→ 上市：Worker /today → www.twse.com.tw STOCK_DAY_ALL（即時）
│                 上櫃：tpex_snap.json 的 close（每日 16:4x 更新，非即時）
├─ 歷年價格/本益比 → 同源靜態 data/price/{code}.json（雙市場同格式）
│                    當年：上市走 Worker /bundle、上櫃讀同源靜態 data/tpex_ytd.json
├─ 財報/股利 ───→ 同源靜態 data/fin/{code}.json（財報＝XBRL；股利＝MOPS 爬蟲，雙市場）
├─ 篩選彙總 ───→ 同源靜態 data/screen.json（fetch_mops.py --build-screen）
└─ 個股消息 ───→ 瀏覽器直連 cnyes 新聞（Worker /news 備援）＋ MOPS 法說會外連

pipeline/fetch_mops.py（股利爬蟲 sii+otc＋build_screen）
pipeline/xbrl_ingest.py（XBRL 解析器，財報主要來源）
pipeline/price_ingest.py（歷史價格落地＋--tpex-snap 每日快照）
.github/workflows/findata.yml（股利季更）/ pricedata.yml（價格月更）/ tpexsnap.yml（上櫃每日）
worker.js（Worker 原始碼備份；不含任何 TPEX——TPEX 擋 Cloudflare）
```

## 資料來源與端點（重大踩坑，依重要度）

1. **openapi.twse.com.tw 只有前一交易日資料**——當日收盤必走 www.twse.com.tw rwd 盤後端點
2. **rwd `STOCK_DAY_ALL?response=json` 實際回 CSV**（民國年、收盤=第9欄、千分位）。Worker `/today` **v3**：解析正規化為 `{date:實際資料日期,n,close:{代號:收盤}}`，**不過濾日期**（STOCK_DAY_ALL 永遠回最近一個交易日、永不比 openapi 舊），前端 `loadToday()` 有資料就無條件覆蓋 quotes[].close。快取：資料日=今天 6h、資料日<今天 30 分（讓 15:00 後發佈能在半小時內被撿到）。v2 教訓：只接受「資料日=今天」造成**午夜盲區**——跨日後 /today 回空、openapi 又要清晨才更新，凌晨會顯示兩天前收盤（2026-07-10 實際踩到）。盤中即時需另接 mis.twse.com.tw，未做
3. **TWSE 會對 Cloudflare 出口限流**（連逛數百檔觸發）。`/bundle` 修正：任一子請求失敗→不進當日快取、回 no-store；bundleKey 已升 v2。前端 loadHistory 自動重試 3 次（1.5s/3s）。**根治方案＝歷史價格落地（待辦 3）**
4. **MOPS 個別報表偶發失敗曾被永久記成「無資料」**。fetch_detail 已改三態（ok/empty/blocked，blocked 不記進度＋連擋自動降溫/中止）——但整條路線已被 XBRL 取代
5. Worker 有全域 try/catch，例外回 JSON `{error,message,stack}` 而非 1101
6. MOPS 編碼：宣告 ISO-8859-1 實際 Big5，用 `r.encoding=r.apparent_encoding`；股利端點 `t05st09sub` **必須先 POST `ajax_t05st09_new` 暖機＋帶 Referer**，資料散在 ~87 張小表

## data 檔案結構

- `data/fin/{code}.json`：`{"code","updated","q":{"2024Q4":{rev,gp,op,nonop,ni,eps,assets,liab,eq,ca,cl,bvps,inv,ar,ap,ppe,cash_bs,stb,ltb,lti,dep,ocf,capex}},"div":[{p,cash,stock}]}`。單位千元（eps/bvps/股利＝元）、損益/現金流量＝**YTD 累計**、capex 負值
- `data/screen.json`：`{"updated","rows":[{c,q,debt,dep}]}`。償債年數=(最新季stb+ltb)/近4季單季ni；折舊年數=最新季ppe/近4季單季dep；近4季任一季算不出單季值→null
- `data/price/{code}.json`：歷史價格靜態檔（已完結年度 hi/lo/avg/pe/pb/yield/ref、null=無資料），規格見「歷史價格落地專案」

## 前端（index.html）重點

- **雙模式**：`mode`（localStorage `twri-mode`），topbar 兩顆獨立按鈕（active 金色）。五個分頁內容條件都有 `mode==="detail"&&` 閘
- **快速瀏覽卡**：右上「第 N / 總數 檔」；四大格（高價上限/關注價/低價低限/目前位階）；指標格（EPS/淨值/PE/PB/殖利率/去年七年配息率/業主盈餘/折舊利益/折舊年數/償債年數/防禦期/現金週期/掛牌年數/產業/資本額）；六張檢驗圖（複用 finView/model 同 scope）
- ⚠️ **positionOf 回傳 0–1**！顯示要 ×100（曾把 97% 顯示成 1%）
- **股利圖**：「每次發放」柱＋年度配息率折線（payoutByRocY＝該年 cash 合計÷該年 eps，同年共用）。原「(年)」圖已移除。配息率與股利共用 Y 軸（柱偏矮為已知，要雙軸需擴充 QChart）
- **資金吃緊圖**：短借/長借為折線
- **QChart**：折線跨 null 缺口相連；`hover` 屬性＝逐柱數值（股利發放圖啟用）
- **RiverChart**：填色依連續資料段分段（缺口不橋接）；自動裁掉頭尾全無資料年份（新上市股不留空白）；PE/PB 為負時「目前」虛線不畫（EPS≤0 防護在呼叫端 `judge.pe>0`）
- **保留股**：`twri-watch`（含 grp 群組），☆ 一律彈群組選單；面板群組 chips＋每列 select；saveWatch 失敗→清 twri-y 快取重試→再失敗 alert
- **← → 瀏覽**：全市場代號序；「僅在保留股間切換」跟群組篩選；input 聚焦不觸發；背景預抓 [+1,+2,−1]
- **篩選面板**：讀 data/screen.json；折舊/償債 ≤n（AND）、欄名升降冪、點列開股、>300 截斷。上櫃股名已由 tpex_snap 快照解析（v11「顯示 —」現象已解）
- **快照快取鍵 snapKey()**：台北時間 <14=a、14–18=h{hh}（每小時重抓等當日收盤）、≥18=b
- loadFin 有 finMem 記憶體快取；搜尋框 ✕ 清除鈕；載入中保留鈕反灰

## 關鍵公式（與 2024 年版 Excel 驗收通過：2330）

- band（近3年）：高價上限/低限＝年度最高 max/min；關注價＝年度最高平均（淨值比/殖利率列與 Excel 有已知小差異）
- 位階＝價格換算指標在近9年 band 位置（殖利率反向）
- 業主盈餘＝(近4季ni+dep−|capex|)×1000/股數；股數＝實收資本額/10
- 折舊利益＝近4季dep×1000/股數；折舊年數＝ppe/近4季dep；償債年數＝(stb+ltb)/近4季ni
- 防禦期＝(cash_bs+ar)/日均支出；日均＝(近4季營業成本+營業費用−折舊)/365
- 現金週期＝存貨+應收−應付天數（單季×90）；業外報酬率＝近4季nonop/(lti或eq)

## Pipeline 指令（fetch_mops.py）

```
--probe 2330 2024 4 / --probe-div 113        端點驗證
--dividends-backfill / --update              股利爬蟲保留；bulk/detail 系列已被 XBRL 取代
--build-screen                               重建篩選彙總（純本機計算）
```
git 順序教訓：**先 commit → `git pull --rebase -X theirs` → push**。

## 開發流程慣例（本專案）

- 改前端先抓 repo 最新檔，改完 **@babel/standalone@7.26.4 實際編譯驗證**＋括號平衡再交付
- Worker 改動：改 repo worker.js → Dale 貼 Cloudflare Deploy → 驗證端點回應 → commit 備份
- pipeline 改動：py_compile＋合成資料單元測試；新爬蟲端點先 --probe 請 Dale 貼輸出（本環境無法直連 TWSE/MOPS）
- 部署：Dale 下載檔案 → cp 到 `~/Desktop/tw-river-repo` → add（指定檔案）/commit/pull --rebase/push

## 交接時狀態（2026-07-10 晚，v12）

- ✅ **上櫃（TPEX）前端支援全案完成**（見專章）：公司清單/快照/當日收盤/當年價格/股利雙市場全通並部署驗證（5483 完整模式抽測通過）
- ✅ 歷史價格落地全案（雙市場 data/price）＋pricedata.yml 月更＋findata.yml 股利季更（現含 otc）＋tpexsnap.yml 上櫃每日快照，四條自動化全部就緒
- ✅ XBRL 建庫完成（33 季、上市＋上櫃財報）；每季更新方式見 XBRL 章
- ✅ 前端 v9：雙市場快照、loadHistory 分流、消息分頁；Worker 維持原版（無 TPEX）
- ⏳ 待辦（依序）：
  1. **保留股跨裝置同步／匯出匯入**：先做 JSON 匯出/匯入（零後端），同步選項先問使用情境
  2. 董監持股率（MOPS 新爬蟲）；淨值比/殖利率關注價與 Excel 小差異；股利圖雙 Y 軸
  3. 觀察項：tpexsnap 平日自動跑幾天確認穩定（偶發髒 runner IP 會自我修復，連續多日失敗才需介入）；8/6 pricedata 首次月更順帶驗證

## Dale 的專案慣例（務必遵守）

- 單檔 HTML、CDN-only、**Babel 釘 @7.26.4 + classic runtime**、繁中 UI、無建置步驟
- JSON 單行 compact（`separators=(',',':')`, `ensure_ascii=False`）
- git 指令一律**單一連續 code block、不加行內 # 註解**；只 add 指定檔案、不用 `git add -A`（XBRL 原始檔在 repo 內）
- 循序處理、不開平行 agent；不做沒被要求的功能
- 爬蟲新端點先給 --probe 類驗證、請 Dale 貼輸出再繼續
- Dale 常在訊息結尾留下未打完的編號（「3.」「4.」），要主動追問
