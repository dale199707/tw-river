# CLAUDE.md — tw-river 台股估價河流圖

> 交接檔。新對話請先完整讀完本檔再動手。本專案於 2026-07-08 凌晨由 Excel 工具（Investment decision tool.xls）網頁化而來，一夜完成 v1–v6。

## 專案定位

取代 Dale 原本的 Excel 股票估價工具（該工具依賴付費資料源、需每季手動更新數千個 .xls，已無法更新）。核心功能：股號/股名搜尋 → 公司基本資訊 → 河流圖（股價/本益比/淨值比/殖利率）＋輸入股價判斷位階 → 財報檢驗圖 → 詳細數據表。資料全自動更新，無本地資料庫。

- 網站：https://dale199707.github.io/tw-river/
- Repo：github.com/dale199707/tw-river（本機 clone 在 `~/Desktop/tw-river-repo`）
- Worker：https://tw-river-api.dale199707.workers.dev（Cloudflare，帳號同 abx-guide 的 abx-api）

## 架構總覽

```
index.html（單檔 React 18 UMD + Babel 7.26.4 classic，繁中 UI，GitHub Pages）
├─ 即時報價/清單 ──→ Cloudflare Worker /openapi/* ──→ openapi.twse.com.tw
├─ 歷年價格/本益比 ─→ Worker /bundle（一次打包9年，邊緣快取）─→ www.twse.com.tw/rwd
└─ 財報/股利 ──────→ 同源靜態 data/fin/{code}.json（GitHub Actions 每季更新）

pipeline/fetch_mops.py（Python，MOPS 爬蟲）
.github/workflows/findata.yml（每季更新 + 每4小時 detail 回補直到完成）
worker.js（Worker 原始碼備份，實際部署在 Cloudflare dashboard 手動貼上）
```

## 檔案結構

```
index.html                      前端全部（~50KB，唯一前端檔）
worker.js                       Worker 程式碼（改動需手動貼到 Cloudflare Edit code → Deploy）
pipeline/fetch_mops.py          MOPS 資料管線
.github/workflows/findata.yml   排程
data/fin/{code}.json            每檔股票財報+股利（單行 compact JSON）
data/fin_progress.json          detail 回補進度（[code, year, season] 陣列，斷點續傳）
```

## 資料來源與端點（含踩坑紀錄）

### TWSE（經 Worker 代理，瀏覽器端使用）
- `openapi.twse.com.tw` **沒有開 CORS**（曾誤判，已用 Worker 解決）
- `/openapi/v1/opendata/t187ap03_L` 公司基本資料（每日快取 localStorage）
- `/openapi/v1/exchangeReport/BWIBBU_ALL` 當日本益比/淨值比/殖利率
- `/openapi/v1/exchangeReport/STOCK_DAY_AVG_ALL` 收盤/月均價
- `/rwd/zh/afterTrading/FMSRFK?date={Y}0101&stockNo={code}` 單一年度逐月最高/最低/均價
- `/rwd/zh/afterTrading/BWIBBU?date={Y}{MM}01&stockNo={code}` 單月逐日 PE/PB/殖利率
- Worker `/bundle?stockNo=&from=&to=`：伺服器端分批並行抓 FMSRFK×9年＋BWIBBU（過去年度12月、當年度最近兩月），邊緣快取（歷史7天/當年6h），瀏覽器一個請求搞定。TWSE 有流量限制，Worker 內 6 個一批、間隔 250ms。

### MOPS（pipeline 使用，`mopsov.twse.com.tw` 舊版域名，2026 仍有效）
- **彙總表**（POST，一請求=全上市公司一季）：
  - `mops/web/ajax_t163sb04` 損益彙總 → rev/gp/op/nonop/ni/eps（**年初至當季累計**）
  - `mops/web/ajax_t163sb05` 資產負債彙總 → assets/liab/eq/ca/cl/bvps（期末時點）
  - payload：`encodeURIComponent=1&step=1&firstin=1&off=1&isQuery=Y&TYPEK=sii&year={民國}&season={01-04}`
- **個別公司三大報表**（GET，一請求=一檔一季）：
  - `server-java/t164sb01?step=1&CO_ID={code}&SYEAR={西元}&SSEASON={1-4}&REPORT_ID=C`
  - 抓取項目：inv 存貨、ar 應收帳款、ap 應付帳款、ppe 不動產廠房設備、cash_bs 現金、stb 短期借款、ltb 長期借款、lti 採用權益法之投資、dep 折舊費用、ocf 營業活動現金流、capex 取得不動產廠房設備（**負值=流出**；現金流量項目為累計）
- **股利分派**（GET，一請求=全上市公司一年度）：
  - `server-java/t05st09sub?step=1&TYPEK=sii&YEAR={民國}`
  - ⚠️ **必須先暖機**：先 POST 一次 `ajax_t05st09_new` 建立 session cookie，且請求要帶 `Referer: {BASE}/mops/web/t05st09_new`，否則只回 3KB 查詢表單頁
  - 資料分散在 ~87 張小表，需全部掃描；代號與名稱同一欄（"2330 台積電"）

### MOPS 踩坑（重要）
1. **編碼**：頁面宣告 ISO-8859-1 實際 Big5，一律用 `r.encoding = r.apparent_encoding`
2. `pd.read_html` 需要 `html5lib` + `beautifulsoup4`（lxml 對 MOPS 頁面會 fallback）；workflow 已含
3. 財報項目名稱不一定在第一欄（會計代碼欄在前），解析採整列掃描 + `startswith` 關鍵字 + `EXCLUDE_ROW`（排除 合計/總計/週轉）
4. 損益/現金流量為 YTD 累計，**單季值由前端 de-cumulate**（`finQuarters()`，Q1 原值、Qn = YTD(n)−YTD(n−1)）；資產負債為時點值不用處理

### 做不到的資料（已明確告知 Dale）
- 營業項目占比（晶圓88.53% 那種產品營收結構）— 付費資料源才有
- 員工人數/生產力 — 年報資料，MOPS ESG 資料集只有近年，未做

## data/fin/{code}.json 結構

```json
{"code":"2330","updated":"2026-07-08",
 "q":{"2024Q4":{"rev":…,"gp":…,"op":…,"nonop":…,"ni":…,"eps":…,
      "assets":…,"liab":…,"eq":…,"ca":…,"cl":…,"bvps":…,
      "inv":…,"ar":…,"ap":…,"ppe":…,"cash_bs":…,"stb":…,"ltb":…,"lti":…,
      "dep":…,"ocf":…,"capex":…}},
 "div":[{"p":"113年 第4季","cash":4.50002,"stock":0},…]}
```
金額單位千元、eps/股利為元、capex 為負。損益/現金流量欄位是 YTD。

## 前端（index.html）重點

- 分頁：基本資訊｜價格位階｜河流圖｜財務指標｜詳細數據
- localStorage 快取：`twri-snap-{date}` 全市場快照（每日）、`twri-y-{code}-{year}` 年度資料（歷史永久、當年當日）
- **財報優先原則**：`data/fin/{code}.json` 存在時，河流圖與估價表的 EPS/淨值自動改用財報真值（`model.finUsed` 旗標，位階分頁註記「EPS／淨值採用財報實際值」）；否則退回「收盤價÷本益比」回推
- 圖表元件：`RiverChart`（河流圖，殖利率圖 `invert` 軸反轉=低殖利率在上）、`QChart`（季線圖，series 加 `bar:true` 畫柱狀，支援負柱）、`StackChart`（資產組成堆疊面積）
- 股價輸入（judgePrice）在「價格位階」與「河流圖」兩分頁共用同一 state，即時連動

## 關鍵公式（與 Excel 核對過）

- 估價表 band（近3年）：高價上限/低限＝3年「年度最高X」max/min；**關注價＝3年年度最高值的平均**（股價、本益比列與 Excel 完全吻合；淨值比/殖利率列的關注價 Excel 演算法未完全反推出來，現用同邏輯近似——已知差異）
- 位階 %：輸入價換算之 PE/PB/殖利率在近9年 band 內的位置（殖利率反向），<20% 低檔…>80% 高檔
- 業主盈餘（真實盈餘）＝(近4季 ni＋dep−|capex|)×1000/股數；股數＝實收資本額/10
- 折舊利益＝近4季 dep×1000/股數（Excel 16.9 ✓）；折舊年數＝ppe/近4季dep（6.16 ✓）；償債年數＝(stb+ltb)/近4季ni（1.1 ✓）
- 防禦期＝(cash_bs+ar)/日均營運支出，日均支出＝(近4季營業成本+營業費用−折舊)/365；營業成本=rev−gp、營業費用=gp−op
- 現金週期＝存貨天數+應收天數−應付天數（天數用單季×90）
- 業外投資報酬率＝近4季 nonop/(lti，無則 eq)
- 歷年 EPS/BVPS/DPS（無財報檔時）＝該年12月均價÷月均 PE/PB、×殖利率 回推

## Pipeline 指令

```
python3 fetch_mops.py --probe 2330 2024 4          驗證財報解析
python3 fetch_mops.py --probe-div 113              驗證股利解析
python3 fetch_mops.py --bulk-backfill 2018 2026    彙總表回補（~72請求，跳過已完成）
python3 fetch_mops.py --detail-backfill 2018 2026 --limit N   細項回補（斷點續傳）
python3 fetch_mops.py --detail-codes 2330,2317 --from-year 2018   指定股票
python3 fetch_mops.py --dividends-backfill 2018 2026   股利回補（9請求）
python3 fetch_mops.py --update                     最新一季（排程用，含股利）
```
bulk 跳過條件檢查 2330 的 `rev` 和 `ca` 欄位——若再加新彙總欄位，改這個檢查讓它重抓。detail 加新項目時：`rm data/fin_progress.json` 讓全部重抓。

## GitHub Actions（findata.yml）

- `30 2 16 4,5,8,11 *`：每季財報截止隔日跑 `--update`
- `0 */4 * * *`：detail 回補（每批1200，`fin_progress` 筆數 ≥ 檔數×33 即自動 no-op）
- workflow_dispatch 可手動選 mode
- ⚠️ 本機長時間跑 detail 前**先 Disable workflow**（避免雙方 commit data 衝突），跑完 `git pull --rebase` 再 push，然後 **Enable 回來**（每季自動更新靠它）

## 交接時狀態（2026-07-08 02:20）

- ✅ v6 前端、Worker v2、pipeline v3 全部部署
- ✅ bulk 回補完成（2018–2026Q1，~1078檔）、股利 probe 驗證成功（回補指令已給，可能已跑）
- 🔄 detail 全市場回補進行中：Dale 準備本機 `caffeinate` 過夜跑（~33,000 季、約19hr，一晚跑不完），剩餘由 Actions 4hr 排程接手
- ⏳ 待辦：
  1. Dale 有一個「4.」需求沒打完，要追問
  2. 驗收：2330 對 Excel（現金週期 88 天、業主盈餘 14.31 元、去年配息率 28.1%、防禦期曲線形狀）
  3. 上櫃（TPEX）未支援——搜尋不到的代號會提示；要做需接 TPEX 端點（www.tpex.org.tw，格式不同）＋ MOPS TYPEK=otc
  4. 淨值比/殖利率列的「關注價」與 Excel 有小差異（演算法未完全反推）
  5. detail 回補完成後可考慮把 workflow 的 `0 */4` cron 移除（雖然會自動 no-op）

## Dale 的專案慣例（務必遵守）

- 單檔 HTML、CDN-only、**Babel 釘 @7.26.4 + classic runtime**（Babel v8 會炸 eval）、繁中 UI、無建置步驟
- JSON 單行 compact（`separators=(',',':')`, `ensure_ascii=False`）
- git 指令一律**單一連續 code block、不加行內 # 註解**
- 循序處理、不開平行 agent；不做沒被要求的功能
- 部署流程 Dale 已熟：改檔 → cp 到 `~/Desktop/tw-river-repo` → add/commit/push；Worker 改動要提醒他去 Cloudflare 貼
- MOPS/TWSE 端點改動一律先給 `--probe` 類驗證指令、請 Dale 貼輸出再繼續（本環境無法直連 TWSE/MOPS 測試）
