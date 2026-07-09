# CLAUDE.md — tw-river 台股估價河流圖

> 交接檔（v9，2026-07-09 03:00）。新對話／Claude Code 請先完整讀完本檔再動手。
> **當前第一要務：用 XBRL 整批檔建立完整財報資料庫（見「XBRL 建庫專案」一節，規格已寫全，可直接開工）。**

## 專案定位

取代 Dale 原本的兩檔 Excel 系統：screen.xls（快速瀏覽＋保留股＋篩選）與 Investment decision tool.xls（估價引擎＋六張檢驗圖）。網頁雙模式：「⚡ 快速瀏覽」＝一頁指標＋六檢驗圖、←→ 連發逛；「▦ 完整模式」＝五分頁完整分析。資料全自動更新，無後端資料庫（靜態 JSON on GitHub Pages）。

- 網站：https://dale199707.github.io/tw-river/
- Repo：github.com/dale199707/tw-river（本機 clone `~/Desktop/tw-river-repo`）
- Worker：https://tw-river-api.dale199707.workers.dev（原始碼備份於 repo 根目錄 worker.js；改動需手動貼到 Cloudflare Edit code → Deploy）

## ⚠️ 立即注意事項

1. **`~/Desktop/tw-river-repo/XBRL/` 放著 33 季的原始檔（數 GB），絕對不能 commit**。任何 git 操作前先確認 `.gitignore` 含 `XBRL/`（若還沒有，第一件事就是加上並 commit .gitignore）。
2. detail 爬蟲路線已終止（Dale 決定改走 XBRL）。findata workflow 目前 **Disabled**，維持不動；每 4 小時 detail cron 於建庫後移除。
3. 既有 `data/fin/*.json` 是爬蟲抓的（上市約 60–70% detail 覆蓋），**保留作 XBRL 交叉驗證**，出入以 XBRL 為準。

---

## XBRL 建庫專案（Claude Code 第一要務）

### 目標
用本機 XBRL 檔解析出**全部申報公司（上市＋上櫃同包）**的季報資料，寫入/覆寫 `data/fin/{code}.json` 的 `q` 欄位，一次到位取代 bulk＋detail 爬蟲，並涵蓋上櫃財報（為之後 TPEX 前端支援鋪路）。

### 輸入
- 位置：`~/Desktop/tw-river-repo/XBRL/tifrs-{YYYY}Q{n}/`，2018Q1–2026Q1 共 33 個資料夾（Dale 已下載解壓完成）
- **先 `ls` 檢查資料夾內部結構**（可能直接是 html、也可能有子層），再寫掃描邏輯
- 檔名格式：`tifrs-fr1-m1-{行業碼}-{cr|er}-{代號}-{YYYY}Q{n}.html`。**優先取 cr（合併報表），該公司無 cr 才用 er（個別）**。行業碼如 ci（一般）、fh（金控）、bk（銀行）等
- 檔案格式：inline XBRL（XHTML＋`ix:nonFraction` 標籤，XML 宣告開頭，UTF-8）

### 解析規則（已用 2313 華通 2026Q1 實測，10/10 欄位與爬蟲完全一致）
1. **期間 context（損益/現金流量類）：必取 `From{年}0101To{季末日}`（YTD 累計）**。Q2/Q3 檔內同時存在單季 context（如 From0401To0630），只按結束日取 max 會選錯——data/fin 慣例存 YTD，前端 `finQuarters()` 負責拆單季
2. **時點 context（資產負債類）：`AsOf{期間結束日}`**（檔內有多個 AsOf——股本異動日等，必須精確匹配季末日）
3. **值處理**：去千分位逗號 → `sign="-"` 屬性則取負 → `×10^scale`（scale 屬性）→ **÷1000 換千元**（與 data/fin 單位一致）。**例外：eps、bvps 單位是「元」，不除 1000**
4. capex 取得後轉負（慣例：取得不動產廠房設備＝現金流出＝負值）
5. 同名元素同 context 出現多次時取第一個（正文表格），注意排除附註中的重複

### 欄位對照
**detail 11 欄（一般業，已驗證）**：
| 欄位 | 元素名 | context |
|---|---|---|
| inv | `ifrs-full:Inventories` | 時點 |
| ar | `tifrs-bsci-ci:AccountsReceivableNet` | 時點 |
| ap | `ifrs-full:TradeAndOtherCurrentPayablesToTradeSuppliers` | 時點 |
| ppe | `ifrs-full:PropertyPlantAndEquipment` | 時點 |
| cash_bs | `ifrs-full:CashAndCashEquivalents` | 時點 |
| stb | `ifrs-full:ShorttermBorrowings` | 時點 |
| ltb | `ifrs-full:LongtermBorrowings` | 時點 |
| lti | `ifrs-full:InvestmentsAccountedForUsingEquityMethod` | 時點 |
| dep | `ifrs-full:AdjustmentsForDepreciationExpense` | 期間 |
| ocf | `ifrs-full:CashFlowsFromUsedInOperatingActivities` | 期間 |
| capex | `ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities` | 期間 |

**彙總 12 欄（元素名為候選，開工時先從實際檔案 grep 確認再定案）**：
rev=`ifrs-full:Revenue`、gp=`ifrs-full:GrossProfit`、op=營業利益（候選 `tifrs-bsci-ci:...OperatingIncomeLoss` 系列）、nonop=營業外收入及支出（tifrs 元素，grep「NonoperatingIncomeAndExpenses」類）、ni=`ifrs-full:ProfitLoss`、eps=`ifrs-full:BasicEarningsLossPerShare`（**元，不除千**）、assets=`ifrs-full:Assets`、liab=`ifrs-full:Liabilities`、eq=`ifrs-full:Equity`、ca=`ifrs-full:CurrentAssets`、cl=`ifrs-full:CurrentLiabilities`、bvps=每股淨值（tifrs 元素待 grep 確認；**元，不除千**）。損益類用期間 context、資產負債類用時點 context。

**確認方法**：拿 2330 或 2313 的檔案，grep 已知數值（對照 data/fin 現有 json）反查元素名，逐欄定案後才批次跑。

### 行業變體
金控/銀行/保險/證券的 taxonomy 前綴不同（如 `tifrs-bsci-ci` 是一般業專用）：ar/ap/inv 等在金融業可能不存在或名稱不同——**金融業缺這些欄位是正常的**（本來就沒有存貨），元素找不到就留空，不要硬湊。dep/ocf/capex/ppe 的 ifrs-full 元素多數行業通用。107–108 年檔案 taxonomy 版本較舊，先抽 2–3 檔驗證元素名是否一致再批次跑。

### 輸出與合併策略
- 寫入 `data/fin/{code}.json`：`q` 內**逐欄位合併**（XBRL 有值→覆寫；XBRL 無值→保留爬蟲舊值）；**`div` 陣列（股利）絕對不動**——那是另一個爬蟲來源，XBRL 沒有股利分派資料
- 新代號（上櫃公司）直接建新檔，格式同現有：`{"code","updated","q":{...}}`
- JSON 一律單行 compact：`json.dumps(..., separators=(',',':'), ensure_ascii=False)`
- 解析器存 `pipeline/xbrl_ingest.py` 進 repo；支援 `--quarter 2024Q1` 單季與 `--all`，含進度輸出與斷點（逐季處理、每季結束存檔）
- 每季輸出統計：`解析 N 檔（cr X / er Y）｜與爬蟲比對：一致 A、補缺 B、出入 C（列出出入前 10 筆供抽查）`
- 全部跑完：`python3 fetch_mops.py --build-screen` 重建篩選彙總
- **驗收**：2330 對照網站財務指標分頁既有數字；2313 2026Q1 已知全對；隨機抽 3 檔金融股確認彙總欄位有值、detail 欄位合理留空
- commit 時只 add `data/fin data/screen.json pipeline/xbrl_ingest.py .gitignore`，**不要 `git add -A`**（XBRL 原始檔在 repo 資料夾內！）

### 執行指令與續傳（`xbrl_ingest.py` 已實作＋驗證，2026-07-09）

**現況**：`pipeline/xbrl_ingest.py` 已完成並驗證。**2026Q1 已實際寫入**（2044 檔全解析 0 失敗、966 上櫃新代號、欄位一致率 99.77%），並記進斷點檔。**其餘 32 季（2018Q1–2025Q4）尚未跑**。

**指令**：
```
python3 pipeline/xbrl_ingest.py --quarter 2026Q1 --dry-run   單季只比對不寫檔
python3 pipeline/xbrl_ingest.py --quarter 2024Q3             單季寫檔（並記斷點）
python3 pipeline/xbrl_ingest.py --all                        逐季跑，斷點自動跳過已完成季
python3 pipeline/xbrl_ingest.py --all --force                忽略斷點全部重跑
```
- **續傳**：斷點檔 `data/xbrl_progress.json`（`{"done":[...]}`，已 gitignore、機器本地）。整季跑完才記完成；中斷未記 → 重跑該季（merge 冪等，不會壞資料，達成「中斷重跑不重工」）。`--all` 預設跳過已完成季，`--force` 全重跑。
- **格式自動偵測**：2018=plain XBRL（`.xml`，完整元無 scale）、2019+=inline XBRL（`.html`，`ix:nonFraction`×10^scale），同一 `build()` 內偵測 `ix:nonFraction` 有無自動切換；context 皆語意字串 `From{Y}0101To{季末}`／`AsOf{季末}`。
- **報表優先序 cr→ir**（無「er」，CLAUDE 舊字誤）；代號含英數（如 `0009A0`）。
- **每季統計**：掃描/選用（cr/ir）/解析成功/失敗（列失敗檔名）｜新建代號數｜一致·補缺·出入｜金融/特殊業 rev 留空檔數（含行業分佈）｜出入前 N（`--top`，預設 10）｜bvps 出入清單（保留爬蟲值，單獨列供抽查）。
- **元素校正紀錄**：op=`ifrs-full:ProfitLossFromOperatingActivities`（非舊候選）；ni=`ifrs-full:ProfitLoss`（總額，clean 股與爬蟲定義一致，2330 驗證同值）；lti fallback 複數→單數（2018 舊 taxonomy 用單數 `Investment...`）；bvps=歸屬母公司權益÷(實收資本額÷10)，**merge 採爬蟲值優先、只補缺**（面額非10元/特別股會算錯）。

**跑完全部 33 季後的收尾**：
```
python3 pipeline/fetch_mops.py --build-screen
git add data/fin data/screen.json pipeline/xbrl_ingest.py .gitignore
git commit -m "XBRL 建庫：全 33 季財報入庫（上市＋上櫃）"
git pull --rebase -X theirs
git push
```
（`data/xbrl_progress.json` 已 gitignore 不會進 commit；務必別 `git add -A`，XBRL 原始檔在 repo 內。）

### 建庫後續（同一專案內或另開）
- 移除 findata.yml 的 `0 */4` detail cron；每季更新方式：Dale 每季手動下載新一季 XBRL 包跑 `xbrl_ingest.py --quarter`，或保留 `--update` 爬單季（擇一，問 Dale）
- 股利爬蟲保留（`--dividends-backfill`，一年一請求；上櫃需 pipeline 加 TYPEK=otc 跑一輪）
- `fin_progress.json`、`--retry-missing` 退役可刪

---

## 架構總覽

```
index.html（單檔 React 18 UMD + Babel 7.26.4 classic，繁中 UI，GitHub Pages）
├─ 快照（公司/PE/PB/殖利率/收盤）→ Worker /openapi/* → openapi.twse.com.tw（僅前一交易日）
├─ 當日收盤 ────→ Worker /today（CSV 解析正規化）→ www.twse.com.tw STOCK_DAY_ALL
├─ 歷年價格/本益比 → Worker /bundle（9年打包，邊緣快取，失敗不快取）→ www.twse.com.tw/rwd
├─ 財報/股利 ───→ 同源靜態 data/fin/{code}.json（★ 建庫後改由 XBRL 產生）
└─ 篩選彙總 ───→ 同源靜態 data/screen.json（fetch_mops.py --build-screen）

pipeline/fetch_mops.py（MOPS 爬蟲＋build_screen＋retry_missing[將退役]）
pipeline/xbrl_ingest.py（★ 待建，本次專案產出）
.github/workflows/findata.yml（Disabled 中）
worker.js（Worker 原始碼備份）
```

## 資料來源與端點（重大踩坑，依重要度）

1. **openapi.twse.com.tw 只有前一交易日資料**——當日收盤必走 www.twse.com.tw rwd 盤後端點
2. **rwd `STOCK_DAY_ALL?response=json` 實際回 CSV**（民國年、收盤=第9欄、千分位）。Worker `/today` 伺服器端解析正規化為 `{date:"YYYYMMDD",n,close:{代號:收盤}}`，資料日期＝今天（台北）才進邊緣快取。前端 `loadToday()` 覆蓋 quotes[].close。收盤後約 15–17 時起顯示當日，之前為前一日（盤中即時需另接 mis.twse.com.tw，未做）
3. **TWSE 會對 Cloudflare 出口限流**（連逛數百檔觸發）。`/bundle` 修正：任一子請求失敗→不進當日快取、回 no-store；bundleKey 已升 v2。前端 loadHistory 自動重試 3 次（1.5s/3s）。**根治方案＝歷史價格落地（待辦 3）**
4. **MOPS 個別報表偶發失敗曾被永久記成「無資料」**。fetch_detail 已改三態（ok/empty/blocked，blocked 不記進度＋連擋自動降溫/中止）——但整條路線已被 XBRL 取代
5. Worker 有全域 try/catch，例外回 JSON `{error,message,stack}` 而非 1101
6. MOPS 編碼：宣告 ISO-8859-1 實際 Big5，用 `r.encoding=r.apparent_encoding`；股利端點 `t05st09sub` **必須先 POST `ajax_t05st09_new` 暖機＋帶 Referer**，資料散在 ~87 張小表

## data 檔案結構

- `data/fin/{code}.json`：`{"code","updated","q":{"2024Q4":{rev,gp,op,nonop,ni,eps,assets,liab,eq,ca,cl,bvps,inv,ar,ap,ppe,cash_bs,stb,ltb,lti,dep,ocf,capex}},"div":[{p,cash,stock}]}`。單位千元（eps/bvps/股利＝元）、損益/現金流量＝**YTD 累計**、capex 負值
- `data/screen.json`：`{"updated","rows":[{c,q,debt,dep}]}`。償債年數=(最新季stb+ltb)/近4季單季ni；折舊年數=最新季ppe/近4季單季dep；近4季任一季算不出單季值→null
- `data/fin_progress.json`：detail 爬蟲進度（將退役）

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
--bulk-backfill / --detail-backfill / --detail-codes / --dividends-backfill / --update
--build-screen                               重建篩選彙總（純本機計算）
--retry-missing 2018 2026                    [將退役]
```
git 順序教訓：**先 commit → `git pull --rebase -X theirs` → push**。

## 開發流程慣例（本專案）

- 改前端先抓 repo 最新檔，改完 **@babel/standalone@7.26.4 實際編譯驗證**＋括號平衡再交付
- Worker 改動：改 repo worker.js → Dale 貼 Cloudflare Deploy → 驗證端點回應 → commit 備份
- pipeline 改動：py_compile＋合成資料單元測試；新爬蟲端點先 --probe 請 Dale 貼輸出（本環境無法直連 TWSE/MOPS）
- 部署：Dale 下載檔案 → cp 到 `~/Desktop/tw-river-repo` → add（指定檔案）/commit/pull --rebase/push

## 交接時狀態（2026-07-09 03:00）

- ✅ 前端 v8 全部部署（雙模式、六檢驗圖、篩選、當日收盤、河流圖分段/裁邊/負值防護、折線跨缺口、自動重試）
- 🛑 detail 爬蟲路線終止（Dale 決定改走 XBRL）；已回補資料保留作交叉驗證；workflow **Disabled**
- ✅ **XBRL 33 季（2018Q1–2026Q1）已下載解壓於 `~/Desktop/tw-river-repo/XBRL/tifrs-{YYYY}Q{n}/`**，解析規則已用 2313 實測驗證
- ⏳ 待辦（依序）：
  1. **XBRL 建庫（Claude Code，白天第一要務，規格見本檔專章）**——完成即同時解決：detail 補全、假性無資料、上櫃財報
  2. **歷史價格落地（高優先）**：河流圖九年資料改靜態檔，根治快速瀏覽被 TWSE 限流。設計：pipeline 價格模式（FMSRFK 逐月高低均＋BWIBBU 年末 PE/PB/殖利率，精簡欄位）→ `data/price/{code}.json`（全市場數十 MB）；Actions 每月更新；前端 loadHistory 歷史年讀靜態檔、當年當月即時、/bundle 降 fallback；一次回補 1078 檔×~19 請求
  3. **上櫃（TPEX）前端支援**：財報已由 XBRL 涵蓋，剩三塊——(a) 公司清單/快照：TPEX openapi（www.tpex.org.tw/openapi/v1/），前端合併兩市場、公司加 market 欄；(b) 河流圖歷史：TPEX 盤後端點（民國年、格式異於 TWSE），若待辦 2 已完成則直接落地進 data/price；(c) 當日收盤：/today 加抓 TPEX 盤後合併。每個端點先請 Dale 跑 probe 貼輸出：
     ```
     curl -s "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes" | head -c 600
     curl -s "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O" | head -c 600
     ```
     （路徑為推測，404 也是有用資訊）；股利爬蟲加 TYPEK=otc 回補上櫃股利
  4. **保留股跨裝置同步／匯出匯入**：先做 JSON 匯出/匯入（零後端），同步選項（Gist token/URL 分享碼/GitHub API）先問使用情境
  5. 董監持股率（MOPS 董監持股資料集，新爬蟲）；淨值比/殖利率關注價與 Excel 小差異；股利圖雙 Y 軸；findata.yml 整理（移除 detail cron、定每季更新方式）

## Dale 的專案慣例（務必遵守）

- 單檔 HTML、CDN-only、**Babel 釘 @7.26.4 + classic runtime**、繁中 UI、無建置步驟
- JSON 單行 compact（`separators=(',',':')`, `ensure_ascii=False`）
- git 指令一律**單一連續 code block、不加行內 # 註解**；只 add 指定檔案、不用 `git add -A`（XBRL 原始檔在 repo 內）
- 循序處理、不開平行 agent；不做沒被要求的功能
- 爬蟲新端點先給 --probe 類驗證、請 Dale 貼輸出再繼續
- Dale 常在訊息結尾留下未打完的編號（「3.」「4.」），要主動追問
