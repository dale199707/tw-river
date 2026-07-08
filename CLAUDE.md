# CLAUDE.md — tw-river 台股估價河流圖

> 交接檔。新對話請先完整讀完本檔再動手。2026-07-08 凌晨由 Excel 工具網頁化（v1–v6），同日白天 v7（保留股＋鍵盤瀏覽＋預抓），晚間 v8（快速瀏覽模式＋全市場篩選＋當日收盤＋多項資料源修正）。

## 專案定位

取代 Dale 原本的兩檔 Excel 系統：screen.xls（快速瀏覽＋保留股＋篩選總表）與 Investment decision tool.xls（估價引擎＋六張檢驗圖）。網頁以**兩種模式**呈現：「⚡ 快速瀏覽」＝screen.xls 體驗（一頁指標＋六檢驗圖、←→ 連發逛）；「▦ 完整模式」＝五分頁完整分析。資料全自動更新，無本地資料庫。

- 網站：https://dale199707.github.io/tw-river/
- Repo：github.com/dale199707/tw-river（本機 clone `~/Desktop/tw-river-repo`）
- Worker：https://tw-river-api.dale199707.workers.dev（**worker.js 已備份於 repo 根目錄**，改動需手動貼到 Cloudflare Edit code → Deploy）

## 架構總覽

```
index.html（單檔 React 18 UMD + Babel 7.26.4 classic，繁中 UI，GitHub Pages）
├─ 快照（公司/PE/PB/殖利率/收盤）→ Worker /openapi/* → openapi.twse.com.tw（僅前一交易日）
├─ 當日收盤 ────→ Worker /today（CSV 解析正規化）→ www.twse.com.tw STOCK_DAY_ALL
├─ 歷年價格/本益比 → Worker /bundle（9年打包，邊緣快取，失敗不快取）→ www.twse.com.tw/rwd
├─ 財報/股利 ───→ 同源靜態 data/fin/{code}.json
└─ 篩選彙總 ───→ 同源靜態 data/screen.json（pipeline build_screen 產生）

pipeline/fetch_mops.py（MOPS 爬蟲 + build_screen + retry_missing）
.github/workflows/findata.yml（每季更新 + 每4小時 detail 回補）
worker.js（Worker 原始碼備份）
```

## 資料來源與端點（含踩坑，依重要度排序）

### 重大踩坑（v8 修過的）
1. **openapi.twse.com.tw 只有前一交易日資料**——「今日收盤」必須走 www.twse.com.tw 的 rwd 盤後端點。
2. **rwd `STOCK_DAY_ALL?response=json` 實際回 CSV**（民國年日期、收盤=第9欄、千分位、response 參數被無視）。Worker `/today` 在伺服器端解析正規化為 `{date:"YYYYMMDD",n,close:{代號:收盤}}`，**資料日期＝今天（台北）才進邊緣快取**，未發佈時 no-store。前端 `loadToday()` 在快照載入後覆蓋 `quotes[].close`。行為：收盤後約 15–17 時起顯示當日收盤，之前為前一交易日（盤中即時價需另接 mis.twse.com.tw，未做）。
3. **TWSE 會對 Cloudflare 出口限流**（快速連逛數百檔觸發）。`/bundle` 原本不分成敗都快取 6 小時 → 空包中毒整天。已修：任一子請求失敗 → **不進當日快取**、回 no-store（成功的子 URL 各自保留快取，重試只補失敗）；bundleKey 已升 `v2`。前端 `loadHistory` 另有**自動重試 3 次**（1.5s/3s 退避）。
4. **MOPS 個別報表偶發失敗會被永久記成「無資料」**（連鴻海都有零星缺季）。pipeline `--retry-missing Y1 Y2` 掃「進度標完成但 fin json 該季無任何 detail 欄位」者，自進度移除重抓。**須等 detail 全部回補完、且 MOPS 冷卻後再跑**。
5. Worker 有全域 try/catch，任何例外回 JSON `{error:"worker exception",message,stack}` 而非 1101，除錯直接看回應。

### TWSE（經 Worker）
- `/openapi/v1/opendata/t187ap03_L` 公司基本資料、`BWIBBU_ALL` PE/PB/殖利率、`STOCK_DAY_AVG_ALL` 收盤/月均（皆 D-1）
- Worker `/bundle?stockNo=&from=&to=`：FMSRFK×9年＋BWIBBU，6個一批間隔250ms
- Worker `/today`：當日全市場收盤（見上）
- Worker 通用轉發 `/openapi/`、`/rwd/` 邊緣快取 6h（TTL_SHORT）

### MOPS（pipeline，mopsov.twse.com.tw）
- 彙總表 `ajax_t163sb04`（損益，YTD）/`ajax_t163sb05`（資產負債，時點）：payload `...TYPEK=sii&year={民國}&season={01-04}`
- 個別報表 `server-java/t164sb01?step=1&CO_ID=&SYEAR=&SSEASON=&REPORT_ID=C`：inv/ar/ap/ppe/cash_bs/stb/ltb/lti/dep/ocf/capex（capex 負值；現金流量為累計）
- 股利 `t05st09sub?step=1&TYPEK=sii&YEAR={民國}`：**必須先 POST `ajax_t05st09_new` 暖機**＋帶 Referer，資料散在 ~87 張小表
- 編碼：宣告 ISO-8859-1 實際 Big5，`r.encoding=r.apparent_encoding`；`pd.read_html` 需 html5lib+beautifulsoup4；項目名稱整列掃描+startswith+EXCLUDE_ROW；損益/現金流量 YTD 由前端 `finQuarters()` de-cumulate

### 做不到：營業項目占比、員工數（付費源/年報）；**董監持股率**（Dale 想要，需接 MOPS 董監持股資料集，pipeline 未做，列待辦）

## data 檔案結構

- `data/fin/{code}.json`：`{"code","updated","q":{"2024Q4":{rev,gp,op,nonop,ni,eps,assets,liab,eq,ca,cl,bvps,inv,ar,ap,ppe,cash_bs,stb,ltb,lti,dep,ocf,capex}},"div":[{p,cash,stock}]}`（千元、eps/股利=元、損益/現金流量=YTD）
- `data/screen.json`：`{"updated","rows":[{c,q,debt,dep}]}`。償債年數=(最新季stb+ltb)/近4季單季ni合計（ni 來自彙總表全市場都有）；折舊年數=最新季ppe/近4季單季dep合計（需 detail）。近4季任一季算不出單季值→null。`--detail-backfill`/`--update`/`--retry-missing` 結尾自動重建。
- `data/fin_progress.json`：detail 進度 [code,year,season]，斷點續傳

## 前端（index.html）重點

- **模式**：`mode` state（localStorage `twri-mode` 記住），topbar 兩顆獨立按鈕（active 金色）。detail=五分頁；screen=快速瀏覽卡（見下）。五個分頁內容條件都有 `mode==="detail"&&` 閘。
- **快速瀏覽卡**：右上「第 N / 總數 檔」；svband 四大格（高價上限/關注價/低價低限/目前位階）；infogrid 指標（EPS、淨值、PE、PB、殖利率、去年/七年配息率、業主盈餘、折舊利益、折舊年數、償債年數、防禦期、現金週期、掛牌年數、產業、資本額）；六張檢驗圖（一~六，複用 finView/model 同 scope 的 QChart/StackChart 設定）。
- ⚠️ **positionOf 回傳 0–1 不是 0–100**！顯示要 ×100（svPos 曾因此把 97% 顯示成 1%，已修）。位階顏色 posColor/標籤 posLabel 吃 0–100。
- **股利圖**（財指分頁＋檢驗六共用設計）：「每次發放」柱狀＋**年度配息率折線**（payoutByRocY：該年 cash 合計÷該年 eps，同年各次發放共用值）。原「(年)」圖已移除。配息率與股利共用 Y 軸（已知柱偏矮，Dale 未再反應；要雙軸需擴充 QChart）。
- **資金吃緊圖**：短借/長借為折線（不是柱）。
- **QChart 折線跨 null 缺口相連**（connectNulls 行為）——detail 缺季不再讓圖碎裂。`hover` 屬性=滑鼠/點擊顯示逐柱數值（股利發放圖啟用）。
- **保留股**：`twri-watch`（含 grp 群組），☆ 按下一律彈群組選單（未分類/自訂/＋新增）；面板群組 chips 篩選＋每列 select 改組；saveWatch 失敗→清 twri-y 快取重試→再失敗 alert。
- **← → 瀏覽**：全市場代號序循環；「僅在保留股間切換」跟著群組篩選；input 聚焦不觸發；背景預抓 [+1,+2,−1] 檔。
- **篩選面板**：載 data/screen.json；折舊/償債年數 ≤n 條件（AND，null 自動排除）、欄名點擊升降冪、點列開股票、>300 檔截斷提示。
- **快照快取鍵 `snapKey()`**：台北時間，<14時=a、14–18時=h{hh}（每小時重抓等當日收盤）、≥18時=b；舊鍵自動清。
- **loadFin 有 finMem 記憶體快取**（404 也快取 null，網路錯誤不快取）。
- 搜尋框 ✕ 清除鈕；`loading||finLoading` 時保留鈕反灰。

## 關鍵公式（與 2024 年版 Excel 驗收通過：2330 現金週期/業主盈餘/配息率/防禦期）

- band（近3年）：高價上限/低限＝年度最高的 max/min；關注價＝年度最高平均（淨值比/殖利率列與 Excel 有已知小差異）
- 位階＝價格換算指標在近9年 band 的位置（殖利率反向）
- 業主盈餘＝(近4季ni+dep−|capex|)×1000/股數；股數＝實收資本額/10
- 折舊利益＝近4季dep×1000/股數；折舊年數＝ppe/近4季dep；償債年數＝(stb+ltb)/近4季ni
- 防禦期＝(cash_bs+ar)/日均支出；日均＝(近4季營業成本+營業費用−折舊)/365
- 現金週期＝存貨+應收−應付天數（單季×90）；業外報酬率＝近4季nonop/(lti或eq)

## Pipeline 指令

```
--probe 2330 2024 4 / --probe-div 113        端點驗證
--bulk-backfill 2018 2026                    彙總表
--detail-backfill 2018 2026 --limit N        細項（斷點續傳，結尾自動 build_screen）
--detail-codes 2330,2317 --from-year 2018    指定股票
--dividends-backfill 2018 2026               股利
--update                                     最新一季（排程用）
--build-screen                               重建篩選彙總（純本機計算，可與回補並行）
--retry-missing 2018 2026 --limit N          重試假性無資料（等全部回補完再跑）
```

## GitHub Actions（findata.yml）

- 每季 `--update`；每 4 小時 detail 回補（完成後自動 no-op）
- ⚠️ 本機跑 detail 前 **Disable workflow**，跑完 push 後 **Enable 回來**
- git 順序：**先 commit → `git pull --rebase -X theirs` → push**（-X theirs 在雙方都動過 data 時採本機版）

## 開發流程慣例（本專案）

- 改前端先抓 repo 最新檔，改完 **@babel/standalone@7.26.4 實際編譯驗證**＋括號平衡再交付
- Worker 改動：改 repo 的 worker.js → Dale 貼到 Cloudflare Deploy → 驗證（開 /today 或相關端點看回應）→ commit 備份
- pipeline 改動：py_compile + 合成資料單元測試；新端點先 --probe 請 Dale 貼輸出
- 部署：Dale 下載檔案 → cp 到 `~/Desktop/tw-river-repo` → add/commit/pull --rebase/push

## 交接時狀態（2026-07-09 00:05）

- ✅ v8 全部部署：快速瀏覽/完整雙模式、六檢驗圖、全市場篩選、當日收盤（Worker /today）、bundle 失敗不快取＋前端自動重試、折線跨缺口、位階修正、股利圖合併、借款折線
- 🔄 **detail 回補進行中**：本機 caffeinate 過夜跑（約至 2496+，剩 ~2萬筆 ≈ 11hr），workflow 目前 **Disabled**
- ⏳ 待辦（依序）：
  1. **明早收尾**：`--build-screen` → add data → commit → `pull --rebase -X theirs` → push → **Enable workflow**（若沒跑完由 Actions 接力）
  2. **全部回補完成後跑 `--retry-missing 2018 2026 --limit 30000`**（跑前 Disable、跑完 push + Enable）——撈回假性無資料（pipeline 已有三態判定：ok/empty/blocked，blocked 不記錄進度＋連擋自動降溫/中止，之後不再產生新誤記）
  3. **歷史價格落地（Dale 已同意，高優先，在上櫃支援之前做）**：河流圖九年資料改為靜態檔，根治快速瀏覽被 TWSE 限流問題。設計：pipeline 新增價格模式（抓 FMSRFK 逐月高低均＋BWIBBU 年末 PE/PB/殖利率，精簡欄位）→ `data/price/{code}.json`（全市場約數十 MB）；Actions 每月更新當月；前端 loadHistory 歷史年讀靜態檔、當年當月仍即時（quote/bundle）、/bundle 降級為 fallback。一次回補約 1078 檔×~19 請求，本機或 Actions 跑數小時。
  4. **XBRL 財報稽核層（已驗證可行，2026-07-09 凌晨用 2313 華通 2026Q1 原型對帳：10/10 欄位與爬蟲完全一致）**：MOPS「案例文件整批下載」有 102 年起每季全公司包（Dale 已確認頁面存在）。檔案為 inline XBRL（XHTML＋`ix:nonFraction`），檔名 `tifrs-fr1-m1-{行業}-{cr|er}-{代號}-{YYYYQn}.html`，取 cr（合併）。**已驗證的解析規則**：期間 context＝檔內最大 `From..To..`；時點 context＝`AsOf{期間結束日}`；值處理＝去逗號、`sign="-"` 取負、×10^scale、**÷1000 換千元**；capex 取正值後轉負。欄位對照（一般業）：inv=`ifrs-full:Inventories`、ar=`tifrs-bsci-ci:AccountsReceivableNet`、ap=`ifrs-full:TradeAndOtherCurrentPayablesToTradeSuppliers`、ppe=`ifrs-full:PropertyPlantAndEquipment`、cash_bs=`ifrs-full:CashAndCashEquivalents`、stb=`ifrs-full:ShorttermBorrowings`、ltb=`ifrs-full:LongtermBorrowings`、lti=`ifrs-full:InvestmentsAccountedForUsingEquityMethod`、dep=`ifrs-full:AdjustmentsForDepreciationExpense`、ocf=`ifrs-full:CashFlowsFromUsedInOperatingActivities`、capex=`ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities`。待辦（適合 Claude Code 專案）：整包 zip 掃描、金融/證券行業 taxonomy 變體、107–108 舊版 taxonomy 抽驗、與 data/fin 逐季比對報告（缺漏補、出入以 XBRL 為準）。
  5. **上櫃（TPEX）支援（高優先）**。分四階段，每階段端點先請 Dale 跑 probe 貼輸出：
     - (a) MOPS 財報/股利：pipeline 的 bulk/dividends 加 `TYPEK=otc` 跑一輪（t164sb01 個別報表不分市場，detail 直接沿用）；上櫃約 800 檔 ×33 季 ≈ 2.6 萬季 detail，**等上市 retry 完再開跑**
     - (b) 公司清單/每日快照：TPEX openapi（www.tpex.org.tw/openapi/v1/，如 tpex_mainboard_quotes、公司基本資料集），欄位名與 TWSE 不同，前端 loadSnapshots 合併兩市場、公司物件加 market 欄位
     - (c) 歷年月價/PE 河流圖：TPEX 盤後端點（www.tpex.org.tw/www/zh-tw/afterTrading/ 下，對應 FMSRFK/BWIBBU 的月成交資訊與本益比表），**日期用民國年、格式與 TWSE 不同**，Worker /bundle 需依代號市場分流
     - (d) 當日收盤：TPEX 盤後每日行情端點，Worker /today 抓兩市場合併
     - 探測指令已提供給 Dale（見下方「TPEX 探測」），輸出貼回後照格式實作
  6. **保留股跨裝置同步／匯出匯入**：先做 JSON 匯出/匯入（零後端），同步選項先問使用情境
  7. 董監持股率：需接 MOPS 董監持股資料集（pipeline 新資料源）
  8. 淨值比/殖利率關注價與 Excel 小差異；股利圖雙 Y 軸（若 Dale 反應柱太矮）；detail 完成後可移除 `0 */4` cron

### TPEX 探測（實作 5. 前請 Dale 執行，每條輸出貼回對話）

```
curl -s "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes" | head -c 600
curl -s "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O" | head -c 600
curl -s "https://www.tpex.org.tw/www/zh-tw/afterTrading/stkAvgPriceInfo?code=5274&date=2024/01/01&response=json" | head -c 800
curl -s "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate?date=2024/12/02&code=5274&response=json" | head -c 800
```

（端點路徑為推測值，探測目的就是確認實際路徑/參數/格式；404 或空回應也是有用資訊，一併貼回。）

## Dale 的專案慣例（務必遵守）

- 單檔 HTML、CDN-only、**Babel 釘 @7.26.4 + classic runtime**、繁中 UI、無建置步驟
- JSON 單行 compact（`separators=(',',':')`, `ensure_ascii=False`）
- git 指令一律**單一連續 code block、不加行內 # 註解**
- 循序處理、不開平行 agent；不做沒被要求的功能
- MOPS/TWSE 端點改動先給 --probe 類驗證、請 Dale 貼輸出再繼續（本環境無法直連測試）
- Dale 常在訊息結尾留下未打完的編號（「3.」「4.」），要主動追問
