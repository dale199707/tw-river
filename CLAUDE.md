# CLAUDE.md — tw-river 台股估價河流圖

> 交接檔（v10，2026-07-09）。新對話／Claude Code 請先完整讀完本檔再動手。
> **當前第一要務：歷史價格落地收尾（規格與部署順序見「歷史價格落地專案」章）——程式三件已實作驗證完，待 probe→抽測→部署→全量回補。**
> XBRL 建庫已完成（33 季全部入庫、上市＋上櫃、已 push 部署），規格章節保留精簡版供每季更新參考。

## 專案定位

取代 Dale 原本的兩檔 Excel 系統：screen.xls（快速瀏覽＋保留股＋篩選）與 Investment decision tool.xls（估價引擎＋六張檢驗圖）。網頁雙模式：「⚡ 快速瀏覽」＝一頁指標＋六檢驗圖、←→ 連發逛；「▦ 完整模式」＝五分頁完整分析。資料全自動更新，無後端資料庫（靜態 JSON on GitHub Pages）。

- 網站：https://dale199707.github.io/tw-river/
- Repo：github.com/dale199707/tw-river（本機 clone `~/Desktop/tw-river-repo`）
- Worker：https://tw-river-api.dale199707.workers.dev（原始碼備份於 repo 根目錄 worker.js；改動需手動貼到 Cloudflare Edit code → Deploy）

## ⚠️ 立即注意事項

1. **`~/Desktop/tw-river-repo/XBRL/` 放著 33 季的原始檔（數 GB），絕對不能 commit**。`.gitignore` 已含 `XBRL/`；任何 git 操作仍只 add 指定檔案、不用 `git add -A`。
2. findata workflow 目前 **Disabled**。建庫已完成，每 4 小時 detail cron 尚未從 findata.yml 移除（列在待辦 4）。
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

## 歷史價格落地專案（第一要務，2026-07-09 已實作待部署）

### 目標與設計
河流圖九年歷史資料改為同源靜態檔，前端不再為每檔股票打 ~19 個 TWSE 上游請求，根治快速瀏覽連逛觸發限流。當年資料仍走 `/bundle` 即時（縮為 `from=to=當年`，每檔僅 2–3 上游請求且邊緣快取）。

### 產出（本次交付，狀態見「交接時狀態」）
1. **`pipeline/price_ingest.py`**：FMSRFK 逐月高低均＋BWIBBU 年末（12 月）PE/PB/殖利率 → `data/price/{code}.json`
2. **`.github/workflows/pricedata.yml`**：每月 6 日 05:30 台北跑 `--update`
3. **`index.html` loadHistory 改造**：歷史年讀靜態檔、當年 /bundle 即時、靜態檔缺→整段落回原 /bundle 路徑（已 Babel 7.26.4 驗證）

### data/price/{code}.json 格式
```
{"code":"2330","updated":"YYYY-MM-DD","y":{
  "2018":{"hi":..,"lo":..,"avg":..,"pe":..,"pb":..,"yield":..,"ref":..},
  "2017":null,
  ...}}
```
- **只存已完結年度**（去年以前，視窗預設今年−10 起）；`null`＝已查證該年無資料（上市前），下次不重抓——**檔案本身即斷點**，中斷重跑冪等
- 欄位語意與前端既有解析完全對齊：hi/lo/avg＝parseYearPrice 結果；pe/pb/yield＝12 月逐日正值平均（parseMonthRatio）；ref＝12 月月均價（refPrice 語意，缺 avg 用 (hi+lo)/2，無 12 月列用年 avg）。前端以 `buildYearData(y,{hi,lo,avg},{pe,pb,yield},ref)` 直接重建，**公式零改動、數字不漂移**
- 有價無比率的年份照存（ratio 欄 null）；價抓到但比率被擋→**整年不記**（避免半套資料定型），下次重抓

### price_ingest.py 指令
```
python3 pipeline/price_ingest.py --probe 2330                  端點驗證（先跑，貼輸出）
python3 pipeline/price_ingest.py --backfill --delay 3          一次回補（~1000 檔 × ~19 請求 ≈ 17 小時，建議本機過夜；可 Ctrl-C 隨時中斷續跑）
python3 pipeline/price_ingest.py --backfill --codes 2330 2313  指定代號抽測
python3 pipeline/price_ingest.py --backfill --limit 200        分段跑（本次最多處理 200 個有缺年代號）
python3 pipeline/price_ingest.py --update                      每月增量（Actions；平時近乎 no-op，每年 1 月自動補前一完結年＋修剪視窗外舊年）
```
- 代號清單：預設抓 openapi `t187ap03_L`（上市，與前端公司清單同源）；FMSRFK/BWIBBU 為 TWSE 端點，**上櫃代號不適用**（TPEX 歷史價格屬待辦 2，落地進同一 data/price）
- 限流防護：每請求 delay（預設 3s＋抖動）、單請求重試 ×3、連續 5 次失敗降溫 90s、降溫後仍擋→中止（沿用 MOPS 三態教訓：empty 記 null 定型、blocked 不記錄待重試）
- 已通過：py_compile、解析函式合成資料單元測試（對齊前端三函式語意）、run_sync 斷點/續傳/修剪整合測試。**尚未實測 TWSE 直連**（本環境無法連），部署前先 --probe

### 前端 loadHistory 新流程
```
localStorage 全中 → 直接回
→ fetch data/price/{code}.json（priceMem 記憶體快取）
   ├─ 有檔：歷史年 buildYearData(靜態欄位) → 當年 /bundle from=to=nowY（2 次嘗試；quote 有 pe 用即時快照當 ratio，否則用 bundle ratio/ratioPrev）
   └─ 無檔（404，新上市/回補未完）：落回原完整 /bundle 路徑（含 3 次重試）——先部署前端也安全
```
localStorage 年快取鍵 `twri-y-{code}-{y}` 與行為完全不變。

### 部署順序
1. Dale 跑 `--probe 2330` 貼輸出確認端點直連 OK（格式已知＝Worker 同端點，但 Python 直連未驗證）
2. `--backfill --codes 2330 2313 2412` 抽測，開網站對照既有河流圖數字
3. 前端 index.html 先部署（fallback 安全）
4. `--backfill` 全量本機過夜跑（可分段），完成後 commit data/price（~1000 檔小 JSON）
5. 啟用 pricedata.yml 月更 workflow

---

## 個股消息分頁（2026-07-10 已部署，經 Dale 調整定版）

完整模式第六分頁「消息」（`tab==="news"`，NewsView）——**只有兩塊**：營運新聞＋法說會外連。今日重大訊息欄位做過又移除（Dale 決定不要；t187ap04_L 的知識留存：中文 key 有尾隨空格、日期民國 7 碼、僅當日僅上市）。
1. **營運新聞**：**瀏覽器直連** `ess.api.cnyes.com/ess/api/v1/news/keyword?q={公司簡稱}&limit=30`（cnyes 有 CORS；**Google News RSS 與部分站點會擋 Cloudflare 出口回 503**，直連用使用者 IP 避開），Worker `/news`（cnyes 版）為備援。過濾規則（`newsPick`，Dale 定版）：剝 `<mark>` 等 HTML 標籤；標題/摘要/keywordForTag 含公司名才收；排除 `^盤[中後]速報`；排除「公司名：」開頭（鉅亨自動轉載 MOPS 公告——可轉債、還本付息、子公司取得設備等）**但 `^鉅亨速報` 保留**（Factset 預估類 Dale 要）；排除加密貨幣類標籤；標題去重；新到舊取 10 則。文章連結 `news.cnyes.com/news/id/{newsId}`，publishAt=unix 秒
2. **法說會**：TWSE openapi 無法說會時程資料集（swagger grep「說明會」為空），以外連 MOPS `t100sb02_1` 按鈕呈現
法人目標價：Dale 決定**不做**（無免費官方來源）。

## 架構總覽

```
index.html（單檔 React 18 UMD + Babel 7.26.4 classic，繁中 UI，GitHub Pages）
├─ 快照（公司/PE/PB/殖利率/收盤）→ Worker /openapi/* → openapi.twse.com.tw（僅前一交易日）
├─ 當日收盤 ────→ Worker /today（CSV 解析正規化）→ www.twse.com.tw STOCK_DAY_ALL
├─ 歷年價格/本益比 → 同源靜態 data/price/{code}.json（歷史年）＋ Worker /bundle 僅當年（fallback 全包）
├─ 財報/股利 ───→ 同源靜態 data/fin/{code}.json（財報＝XBRL 產生；股利＝MOPS 爬蟲）
├─ 篩選彙總 ───→ 同源靜態 data/screen.json（fetch_mops.py --build-screen）
└─ 個股消息 ───→ 瀏覽器直連 cnyes 新聞（Worker /news 備援）＋ MOPS 法說會外連

pipeline/fetch_mops.py（股利爬蟲＋build_screen；bulk/detail/retry 已刪除）
pipeline/xbrl_ingest.py（XBRL 解析器，財報主要來源）
pipeline/price_ingest.py（歷史價格落地，已實作待部署）
.github/workflows/findata.yml（Disabled 中）
worker.js（Worker 原始碼備份）
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
- **篩選面板**：讀 data/screen.json；折舊/償債 ≤n（AND）、欄名升降冪、點列開股、>300 截斷。上櫃代號進 screen.json 後，快照查不到股名會顯示 —（TPEX 快照接入前的已知現象）
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

## 交接時狀態（2026-07-09）

- ✅ 前端 v8 全部部署（雙模式、六檢驗圖、篩選、當日收盤、河流圖分段/裁邊/負值防護、折線跨缺口、自動重試）
- ✅ **XBRL 建庫完成**：33 季（2018Q1–2026Q1）全部入庫、上市＋上櫃財報皆有、已 push 部署；`fin_progress.json`／`--retry-missing` 退役（實體清理列待辦 4）；每季更新方式見「XBRL 建庫」章
- ✅ **歷史價格落地已實作**（規格見專章）：`pipeline/price_ingest.py`＋`pricedata.yml`＋`index.html` loadHistory 改造三件產出完成、單元/整合測試與 Babel 驗證通過；**待 Dale：--probe 驗證直連 → 抽測 → 部署前端 → 全量回補 → 啟用月更**（順序見專章「部署順序」）
- ⏳ 待辦（依序）：
  1. **歷史價格落地收尾**：依專章部署順序執行（probe → 抽測 → 前端部署 → 全量回補 → 啟用 pricedata.yml）
  2. **上櫃（TPEX）前端支援**（端點已全數 probe 驗證，2026-07-09）：
     - (b) **河流圖歷史：已實作**於 price_ingest.py（`--tpex-probe`／`--tpex-backfill`／`--tpex-update`）。設計＝**逐日全市場掃描**：`www/zh-tw/afterTrading/dailyQuotes?date=YYYY/MM/DD&response=json`（新版 API、西元日期、歷史可查、非交易日回空表）一天一請求累計每檔每月 hi/lo/均（月均＝日收盤平均）；12 月交易日再打 `web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&o=json&d={民國}/{MM}/{DD}`（全市場單日 PE/每股股利/股利年度/殖利率/股價淨值比，105/12 仍有 712 檔）取正值平均。一年 ~283 請求涵蓋全部上櫃（vs 每檔每年打 8000+）。代號 universe＝openapi `mopsfin_t187ap03_O`（英文欄位 `SecuritiesCompanyCode`）過濾 4 位純數字。年度斷點 `data/tpex_price_progress.json`（**需加進 .gitignore**）；整年掃完才寫檔＋記斷點，被擋中止年度重掃。輸出寫進同一 `data/price/{code}.json`，前端零改動。單元測試全過、TWSE 回歸過；**待實跑：--tpex-probe（驗 2016 深度＋真實欄位）→ --tpex-backfill（~2830 請求 ≈ 1.2h @1.5s）**
     - (a) 公司清單/快照：openapi `tpex_mainboard_quotes`（含債券 ETF 如 00679B 需過濾）＋ `mopsfin_t187ap03_O`（公司基本資料，英文欄位、產業別為數字碼、需另找實收資本額欄位）；前端合併兩市場、公司加 market 欄——**未實作**
     - (c) 當日收盤：/today 加抓 TPEX（openapi quotes 或 dailyQuotes 當日）合併——**未實作**。注意舊端點 `stk_quote_result.php` 無 o=json 時會忽略日期參數回當日
     - 股利爬蟲加 TYPEK=otc 回補上櫃股利——未實作
  3. **保留股跨裝置同步／匯出匯入**：先做 JSON 匯出/匯入（零後端），同步選項（Gist token/URL 分享碼/GitHub API）先問使用情境
  4. **清理（已實作待部署，2026-07-09）**：fetch_mops.py 656→278 行（刪 bulk/detail/retry 整條，僅剩股利＋build_screen；`--update` 重定義＝近兩年股利＋build_screen）；findata.yml 移除 `0 */4` detail cron 與模式機關，僅剩季 cron（4/5/8/11 月 16 日）跑 `--update`、commit 縮窄為 `data/fin data/screen.json`；repo 需 `git rm data/fin_progress.json`。**部署後記得到 Actions 頁把 findata workflow 重新 Enable**（季報更新仍＝手動 XBRL，此 workflow 只管股利）
  5. 董監持股率（MOPS 董監持股資料集，新爬蟲）；淨值比/殖利率關注價與 Excel 小差異；股利圖雙 Y 軸

## Dale 的專案慣例（務必遵守）

- 單檔 HTML、CDN-only、**Babel 釘 @7.26.4 + classic runtime**、繁中 UI、無建置步驟
- JSON 單行 compact（`separators=(',',':')`, `ensure_ascii=False`）
- git 指令一律**單一連續 code block、不加行內 # 註解**；只 add 指定檔案、不用 `git add -A`（XBRL 原始檔在 repo 內）
- 循序處理、不開平行 agent；不做沒被要求的功能
- 爬蟲新端點先給 --probe 類驗證、請 Dale 貼輸出再繼續
- Dale 常在訊息結尾留下未打完的編號（「3.」「4.」），要主動追問
