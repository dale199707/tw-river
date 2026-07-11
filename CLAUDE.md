# CLAUDE.md — tw-river 台股估價河流圖

> 交接檔（v16，2026-07-12）。新對話／Claude Code 請先完整讀完本檔再動手。
> **本檔含「維運手冊」專章**：所有例行更新、失敗處理、偵錯 SOP 都寫成可照抄執行的步驟，
> 任何模型（含較弱模型）都應嚴格照手冊執行、不自行變通、不跳步驟。
> **v16 重點**：前端全面「年化呈現」改版（圖表以完整年度＋近4季點呈現，QChart 大幅擴充）、
> 盤後選股批次保留與非上市櫃過濾、PSR 河流圖、pipeline 股票分割基準修正（_split_adjust）。
> **2026-07-12 白畫面事故教訓已寫入前端鐵律**：改 model/finView 等核心計算必須做執行期煙霧測試，
> Babel 編譯通過不代表不會執行期崩潰。

## 專案定位

取代 Dale 原本的兩檔 Excel 系統：screen.xls（快速瀏覽＋保留股＋篩選）與 Investment decision tool.xls（估價引擎＋檢驗圖）。網頁雙模式：「⚡ 快速瀏覽」＝一頁指標＋檢驗圖、←→ 連發逛；「▦ 完整模式」＝六分頁完整分析。資料全自動更新，無後端資料庫（靜態 JSON on GitHub Pages）。

- 網站：https://dale199707.github.io/tw-river/
- Repo：github.com/dale199707/tw-river（本機 clone `~/Desktop/tw-river-repo`）
- Worker：https://tw-river-api.dale199707.workers.dev（原始碼備份於 repo 根目錄 worker.js；改動需手動貼到 Cloudflare Edit code → Deploy）

## ⚠️ 立即注意事項

1. **`~/Desktop/tw-river-repo/XBRL/` 放著 33+ 季的原始檔（數 GB），絕對不能 commit**。`.gitignore` 已含 `XBRL/`；任何 git 操作仍只 add 指定檔案、不用 `git add -A`。
2. **TPEX 封鎖 Cloudflare Workers 出口 IP**（302 無限重導向至 /errors，openapi 與 www 端點一體封鎖），且全部端點**無 CORS**。任何上櫃「即時」需求都只能走 Actions 產生靜態檔，不要再嘗試 Worker 代理或瀏覽器直連。
3. `data/fin/*.json` 以 **XBRL 為主要來源**（33 季全數入庫，上市＋上櫃＋夾帶興櫃/公發）；爬蟲舊值僅在 XBRL 無值欄位保留。`div` 陣列仍為股利爬蟲來源（已回補 105 年起全市場），XBRL 不含股利。
4. **前端改動必做三驗證**：Babel 7.26.4 實際編譯＋括號平衡＋（改到 model/finView/loadHistory 時）執行期煙霧測試。詳見「前端修改 SOP」。

---

## XBRL 建庫（✅ 已完成；本節為每季更新參考）

### 完成狀態
- **33 季（2018Q1–2026Q1）全部入庫**：寫入 `data/fin/{code}.json` 的 `q` 欄位。解析器 `pipeline/xbrl_ingest.py`。
- XBRL 包含**興櫃/公開發行公司**，因此 data/fin 有 ~666 檔非上市櫃代號（多為年報 only 或已停止申報）——**屬正常，前端已過濾**（見前端重點「非上市櫃過濾」）。2026-07-12 全面清查結論：真正缺近季的上市櫃股僅 1589（缺 2025Q4＋2026Q1，待 Dale 查本機 XBRL 包內是否有其檔案）。
- 原始檔在 `~/Desktop/tw-river-repo/XBRL/`（gitignored，勿 commit）。

### 每季更新方式
Dale 每季手動下載新一季 XBRL 包解壓至 `XBRL/tifrs-{YYYY}Q{n}/`，執行：
```
python3 pipeline/xbrl_ingest.py --quarter 2026Q2
python3 pipeline/fetch_mops.py --build-screen
```
再 add 指定檔案 commit push（只 add `data/fin data/screen.json`，勿 `git add -A`）。

### xbrl_ingest.py 關鍵知識（除錯/擴充時參考）
- **指令**：`--quarter YYYYQn`（單季寫檔＋記斷點）、`--quarter ... --dry-run`（只比對）、`--all`（逐季、斷點自動跳過）、`--all --force`（全重跑）、`--top N`（出入清單筆數）。斷點檔 `data/xbrl_progress.json`（gitignored）；merge 冪等，中斷重跑不壞資料。
- **格式自動偵測**：2018=plain XBRL（`.xml`，完整元無 scale）、2019+=inline XBRL（`.html`，`ix:nonFraction`×10^scale）。context：期間類 `From{Y}0101To{季末}`（**YTD 累計**，勿取單季 context）、時點類 `AsOf{季末}`。
- **報表優先序 cr→ir**（合併優先）；代號含英數（如 `0009A0`）。
- **值處理**：去逗號 → `sign="-"` 取負 → ×10^scale → ÷1000 千元；**eps/bvps 單位「元」不除千**；capex 轉負；同名元素同 context 取第一個。
- **元素定案**：detail 11 欄照原對照表（inv/ar/ap/ppe/cash_bs/stb/ltb/lti=時點；dep/ocf/capex=期間）；op=`ifrs-full:ProfitLossFromOperatingActivities`；ni=`ifrs-full:ProfitLoss`（總額）；lti fallback 複數→單數（2018 舊 taxonomy）；bvps=歸屬母公司權益÷(實收資本額÷10)，**merge 採爬蟲值優先只補缺**（面額非 10 元/特別股會算錯）。
- **金融業**：ar/ap/inv 等元素不存在是正常，留空不硬湊。
- **合併策略**：`q` 逐欄位合併（XBRL 有值覆寫、無值保留爬蟲舊值）；**`div` 陣列絕對不動**。

---

## 歷史價格落地（✅ 全案完成；pricedata.yml 每月 6 日自動月更）

- **上市（TWSE）**：~1,086 檔 × 10 完結年度；**上櫃（TPEX）**：891 檔 × 10 完結年度
- `data/price/{code}.json` 格式（雙市場同一格式）：`{"code","updated","y":{"2018":{hi,lo,avg,pe,pb,yield,ref}|null,...}}`——只存完結年度；null=已查證無資料；ref=12 月月均價。檔案本身即斷點、merge 冪等。
- price_ingest.py 指令：`--probe/--backfill/--update`（上市）、`--tpex-probe/--tpex-backfill/--tpex-update`（上櫃）、`--tpex-snap`（每日快照）、`--codes/--from-year/--delay/--limit/--force`

### TPEX 端點與限流實戰知識（⚠️ 之後打 TPEX 必讀）
- **dailyQuotes**：`www/zh-tw/afterTrading/dailyQuotes?date=YYYY/MM/DD&response=json`，非交易日合法形狀＝**有 tables 鍵但無匹配表**（缺 tables 鍵＝限流頁，必須判 blocked 不可當空）
- **pera**：`web/stock/aftertrading/peratio_analysis/pera_result.php?l=zh-tw&o=json&d={民國}/{MM}/{DD}`
- **限流行為**：夜間可連跑 ~1,700 請求；白天約每 ~300 請求封 IP 10–30 分鐘且連 openapi 一起封。對策已內建：`COOLDOWNS=[120,300,600,1800]` 階梯降溫＋整年原子寫入＋年度斷點，`--delay 3` 放著跑即可自癒
- 上櫃公司清單：openapi `mopsfin_t187ap03_O`（`SecuritiesCompanyCode`），過濾 4 位純數字
- TWSE 側：delay 1.5 全程僅 3 blocked（寬鬆得多）

## 上櫃前端（TPEX）架構：Actions 每日靜態檔（✅ 完成）

- **封鎖實況**：Cloudflare 出口被封＋全端點無 CORS → 只能 GitHub Actions（可正常存取）產靜態檔
- `.github/workflows/tpexsnap.yml`：平日 16:40 台北跑 `--tpex-snap --delay 2`，commit `data/tpex_snap.json`（公司清單＋當日 pe/pb/yield/close）＋`data/tpex_ytd.json`（當年逐月累計）
- `--tpex-snap` 特性：斷點＝ytd last 日期；假日/未發佈不推進、隔日續補；blocked 中止但已累計先落檔；重跑冪等；公司清單失敗沿用舊清單
- **上櫃收盤時效＝每日 16:4x，非即時**；上市維持 Worker /today（約 15:00–15:30 反映當日收盤）
- 前端：公司清單 TWSE openapi＋tpex_snap concat（`market:"twse"|"tpex"`）；`loadHistory` 歷史年走 data/price、當年上市走 /bundle、上櫃讀 tpex_ytd；快照 localStorage 鍵 `twri-snap2-`
- Worker 內**禁止**任何 TPEX 抓取

### 股利（fetch_mops.py）
- `fetch_dividends_year(roc, typek)`：TYPEK=sii/otc；暖機 per-TYPEK（`_DIV_WARMED`）
- `--update`（findata.yml 季更 4/5/8/11 月 16 日）自動涵蓋雙市場；`--probe-div 113 --typek otc` 單測；已回補 105–115

## 個股消息分頁

完整模式「消息」分頁——只有兩塊：營運新聞（**瀏覽器直連** cnyes `ess.api.cnyes.com/ess/api/v1/news/keyword?q={簡稱}&limit=30`，Worker `/news` 備援；過濾規則 `newsPick` 為 Dale 定版勿改）＋法說會 MOPS `t100sb02_1` 外連。法人目標價不做。

## 架構總覽

```
index.html（單檔 React 18 UMD + Babel 7.26.4 classic，繁中 UI，GitHub Pages）
├─ 快照 → 上市：Worker /openapi/*（前一交易日）；上櫃：data/tpex_snap.json（每日）
├─ 當日收盤 → 上市：Worker /today（即時）；上櫃：tpex_snap 的 close（16:4x）
├─ 歷年價格 → data/price/{code}.json；當年：上市 /bundle、上櫃 data/tpex_ytd.json
├─ 財報/股利 → data/fin/{code}.json（財報＝XBRL；股利＝MOPS 爬蟲）
├─ 篩選彙總 → data/screen.json（--build-screen，含 _split_adjust 分割修正）
└─ 個股消息 → cnyes 直連＋MOPS 法說會外連

pipeline/fetch_mops.py（股利爬蟲＋build_screen）/ xbrl_ingest.py / price_ingest.py
.github/workflows/findata.yml（季更）/ pricedata.yml（月更）/ tpexsnap.yml（每日）
worker.js（備份；不含任何 TPEX）
```

## 資料來源重大踩坑（依重要度）

1. **openapi.twse.com.tw 只有前一交易日**——當日收盤必走 www.twse.com.tw rwd 盤後端點
2. **STOCK_DAY_ALL 實際回 CSV**（民國年、千分位）。Worker `/today` v3 **不過濾日期**（永遠回最近交易日、永不比 openapi 舊）；快取：資料日=今天 6h、<今天 30 分。v2 教訓：只接受「資料日=今天」造成午夜盲區
3. **TWSE 會對 Cloudflare 出口限流**。根治＝歷史價格落地（已完成）；/bundle 失敗不進快取、前端重試 3 次
4. MOPS 編碼：宣告 ISO-8859-1 實際 Big5，用 `r.encoding=r.apparent_encoding`；`t05st09sub` 必須先 POST `ajax_t05st09_new` 暖機＋帶 Referer
5. Worker 全域 try/catch，例外回 JSON `{error,message,stack}`

## data 檔案結構

- `data/fin/{code}.json`：`{"code","updated","q":{"2024Q4":{rev,gp,op,nonop,ni,eps,assets,liab,eq,ca,cl,bvps,inv,ar,ap,ppe,cash_bs,stb,ltb,lti,dep,ocf,capex}},"div":[{p,cash,stock}]}`。單位千元（eps/bvps/股利＝元）、損益/現金流量＝**YTD 累計**、capex 負值
- `data/screen.json`：`{"updated","rows":[{c,q,debt,dep,revG,niG,epsG,roe,navG,eps4,epsY,peLo,peLo2}]}`
- `data/price/{code}.json`：見歷史價格落地節

## pipeline：build_screen 與 _split_adjust（v16 新增，⚠️ 勿刪）

`fetch_mops.py` 的 `build_screen()` 內含 **`_split_adjust(qmap)`**：股票分割／面額變更防護（2026-07 國巨 2327 一拆四實例——XBRL 的 YTD eps 於分割生效季起改用新股本重編，直接跨季相減會得垃圾單季值，eps4 曾因此算出 4.64 而非正確的 12.72）。

- 原理：以 ni÷eps 反推各季隱含股數（**|eps|≥0.05 才可信**，防捨入雜訊）；相鄰可判定季比值跳出 **[0.67, 1.5]** 視為股本基準斷裂；斷點以前季別的 **eps 與 bvps** 除以累積斷裂係數，換算到最新股本基準後才做單季還原
- 影響欄位：eps4／epsY／epsG／navG 全部修正；ni/rev 等總額欄不受分割影響、不處理
- 支援多次斷裂鏈式換算；虧損連續季（eps 皆負）與微小 eps 不會誤判（已有單元測試驗證）
- **注意**：此修正只在 screen.json 生效。前端 finQuarters 未套用同邏輯——分割股在「每股盈餘」「EPS 年複合成長率」等圖仍會有基準斷裂鋸齒（已知未修，Dale 未要求）

## 前端（index.html）重點【v16 大改版，本章為現狀定版】

### 分頁順序（2026-07-12 定版）
基本資訊 → 財務指標 → 價格位階 → 河流圖 → 詳細數據 → 消息

### 年化呈現核心：finView.Y 子物件
- **finView 維持逐季計算不動**（供「詳細數據」分頁兩張逐季表與資訊格單值使用；現金週期＝單季×90 等原語意不變）
- **新增 `Y` 子物件（僅圖表用）**，JSX 以捷徑 `const FY=(finView&&finView.Y)||null;` 取用：
  - 年列＝各年 **Q4** 值（流量 rev/gp/op/nonop/ni/eps/ocf/dep/capex＝YTD 全年；資產負債＝年末）
  - 最新季非 Q4 時最右補「**近4季**」點（流量＝trailing4 單季合計；資產負債＝最新季）；最新季是 Q4 則不補
  - 天數類公式**年化 ×365**（逐季版 ×90）；防禦期日均＝全年支出÷365
  - 欄位：labels,gm,om,nm,epsA,revPS,roe,roa,invD,arD,apD,ccc,oe,depCF,revB,ocfB,niB,opB,nonopB,stbB,ltbB,borrowB,fixedTurn,invPct,arPct,apPct,defensive,outsideRet,stack,hasDetail,hasBorrow,hasNonop,epsCagr,cagrY0
  - **hasBorrow（2026-07-12 修正）**＝有 stb/ltb 值**或**有細項（inv/dep）——無借款公司（如 6231 系微）視為借款 0 照畫（藍海型敘事）；stbB/ltbB 的 null 補 0 條件同步為 `(inv!=null||dep!=null)`
- **epsCagr**：年度 EPS（Q4 YTD）以最新完整年度為終點，CAGR(N)=(終點÷N年前)^(1/N)−1，N=7→1 順序；終點或基期 ≤0 該點 null；**終點年虧損時整組 null，圖以說明框呈現原因**（不是消失）

### QChart 能力（v16 擴充，改圖表時先讀這段）
props：`title, labels, series, unit, decimals, hover, stacked, ylog, dots, endLabels`
- series 項：`{name, color, data, bar:true, vlabel}`
- **vlabel**（逐點常駐標籤）：折線 `"above"/"below"`（點上/下方）；柱狀 `true`（正值標柱頂上、負值標柱底下）；**stacked 柱另支援 `"top"/"bottom"`**＝標在整柱正向頂端上方／負向底端下方（賺錢嗎圖藍橘分層防重疊用）。有 vlabel 的系列不畫線尾標籤
- **stacked**：柱狀同一柱位，正值向上堆疊、負值向下堆疊；軸範圍以堆疊總和計算
- **ylog**：symlog（sign(v)×log10(1+|v|)），支援 0 與負值；標籤仍顯示原始值。勿與 stacked 併用
- **dots**：折線每個資料點畫實心圓（仿 Excel）
- **endLabels={false}**：關閉線尾數字
- **線尾標籤防碰撞**：所有無 vlabel 系列的線尾標籤自動由上而下推開 ≥11px

### 圖表定版排列（⚠️ Dale 指定順序，勿擅改）
**完整模式・財務指標「獲利檢驗」**（一列三張）：
1. 三率（年）｜2. 公司真的賺錢嗎（每股・年）｜3. 公司資金吃緊嗎（億・對數尺度）
4. EPS 年複合成長率（7〜1年）｜5. 公司獲利轉換成何種資產（億）｜6. 公司獲利來源（年・億）
7. ROE／ROA（年）｜8. 每股盈餘（年）｜9. 公司獲利能與股東分享嗎（年）
**快速瀏覽卡**：賺錢嗎／吃緊／CAGR｜獲利來源／資產／ROE-ROA｜股利（已去掉「檢驗（N）」編號前綴）
**隱藏中（`{false&&...}` gate，程式碼保留可隨時恢復）**：業外投資比本業好嗎（兩模式）、營收與現金流（fin 分頁）、殖利率河流圖（河流圖分頁）
（條件顯示圖：缺 detail 的股票會少圖並自動補位，列對齊跑掉屬 grid 天性）

### 各圖語意細節
- **賺錢嗎圖**：stacked＋藍柱 vlabel:"top"、橘柱 vlabel:"bottom"；灰線＝**每股營收**（Y.revPS＝全年營收×1000÷股數，Excel 原意是營收，單軸下以每股呈現）
- **獲利來源圖**：stacked（本業＋業外堆疊＝合計）
- **吃緊圖**：ylog＋dots＋endLabels={false}（仿 Excel：無數字、有節點圓點；查金額走詳細數據分頁）
- **股利圖（divY 年度彙總）**：年份範圍＝股利紀錄 ∪ 財報完整年度（民國）**聯集**、缺年補 0（爬蟲已回補 105+，無紀錄＝未發放）；**從未配息公司也出全 0 圖**；現金股利/股票股利柱＋配息率線全部 vlabel 常駐；配息率：無配息年顯示 0，「有配息但無 EPS 可除」（106 年前無財報或該年 EPS≤0）維持留白——0 與算不出來不混
- **ROE/ROA、EPS CAGR**：逐點 vlabel 常駐（ROE above、ROA below）
- 每股盈餘圖＝年度 EPS 單柱＋vlabel

### 河流圖分頁
- 四張：股價／本益比／股價淨值比／**股價營收比（PSR，v16 新增）**；殖利率已隱藏（false gate）
- **PSR**：model 內年度每股營收＝a.rev×1000÷sharesM（sharesM=stock.capital/10）、最新年用近4季 revPSTTM；r.psHi/psLo/psAvg；nowValue＝輸入股價÷model.revPSNow。財報 2018 起，PSR 圖最多 9 年（RiverChart 自動裁頭）
- **RiverChart 新增 `note` prop**（圖下說明文字）；殖利率圖有自動缺配息年說明（隱藏中一併保留）
- ⚠️ **2026-07-12 白畫面事故**：revPSTTM 曾以 const 宣告在 `if(finRows...)` 區塊內、return 在區塊外引用 → 搜任何股票即 ReferenceError 全頁白。已改 `let revPSTTM=null;` 提升至函式頂層。**教訓寫入前端鐵律第 4 條**

### 篩選／盤後選股
- **非上市櫃過濾（v16）**：scrRows 與 pickRows 皆以 `names[r.c]`（snap.companies 現行上市櫃清單）過濾——興櫃/公發/已下市（約 666 檔、股名「—」）一律排除，下市自動消失
- **盤後選股**：四條件（revG>0、niG>0、roe>0、debt∈[0,5)）**照舊生效但欄位已隱藏**（2026-07-11 Dale 指示，減少橫向寬度）；顯示欄＝股號/股名/最新季/現價/去年EPS/近4季EPS/本益比/EPS成長率/本益成長比/最低PE價/次低PE價/12倍PE價；**全數列出（300 截斷已移除）**；預設排序 EPS成長率降冪
  **⚠️ 所有選股與估價公式經 Dale 多輪調校定版，未經 Dale 明確要求絕對不可更動任何公式或條件。** 公式：本益比＝現價÷eps4；EPS成長率＝eps4÷epsY−1（epsY>0）；本益成長比＝本益比÷成長率（>0 才列；<1 便宜、≈1 合理、>2 貴）；最低/次低PE價＝peLo/peLo2×eps4；12倍PE價＝12×eps4。歷史演進備查（勿走回頭路）：EPS/NAV成長曾為條件後刪；便宜/合理價欄曾存在後刪；PEG 已由本益成長比取代
- **勾選批次保留（v16）**：兩表首欄 checkbox＋表頭全選（篩選表全選前 300、盤後全選全部）；勾選後出現操作列（群組下拉＋加入保留群組＋清除勾選）；`batchKeep(codes,g)`：已保留者附加群組、未保留者建檔——**批次建檔的估價快照欄位（EPS/償債年數/位階價等）為 null 顯示「—」**（表格情境無 model/finView），現價/股名/掛牌年會帶入；開啟個股按 ★ 重新保留可補快照。面板關閉自動清空勾選
- 全市場篩選面板維持 300 截斷；個股內容閘門 `{stock&&!scrOpen&&!pickOpen&&…}`；screen.json 載入 effect **依賴陣列必須含 pickOpen**（曾漏加致永遠載入中）

### 快取三層與自癒
1. localStorage 快照 `twri-snap2-*`（snapKey：台北 <14=a、14–18=h{hh} 每小時、≥18=b）
2. localStorage 年資料 `twri-y-{code}-{y}`（歷史年永久、當年當日）。**殘缺快取自癒（v16）**：歷史年若 eps/bvps/dps 全 null（/bundle 限流時代殘留），loadHistory 視為無效、改讀靜態檔重算覆寫
3. Worker/瀏覽器 HTTP 快取

清快取萬用指令（DevTools Console）：
```
Object.keys(localStorage).filter(k=>k.startsWith("twri-snap")||k.startsWith("twri-y-")).forEach(k=>localStorage.removeItem(k));location.reload();
```

### 其他既有機制（v15 沿用）
- 雙模式 `mode`（localStorage `twri-mode`）；⚠️ **positionOf 回傳 0–1，顯示要 ×100**
- 保留股多群組 `twri-watch`（`grps` 陣列、舊 grp 字串自動相容，grpsOf() 統一入口）；☆/★ 選單、面板 ±群組下拉、saveWatch 失敗→清 twri-y 重試→alert
- ←→ 全市場代號序瀏覽、背景預抓 [+1,+2,−1]
- 待辦既有：保留股跨裝置同步／匯出匯入（JSON 先行）

## 關鍵公式（與 2024 年版 Excel 驗收通過：2330）

- band（近3年）：高價上限/低限＝年度最高 max/min；關注價＝年度最高平均
- 位階＝價格換算指標在近9年 band 位置（殖利率反向）
- 業主盈餘＝(近4季ni+dep−|capex|)×1000/股數；股數＝實收資本額/10（**年化圖版本＝全年值同式**）
- 折舊利益＝近4季dep×1000/股數；折舊年數＝ppe/近4季dep；償債年數＝(stb+ltb)/近4季ni
- 防禦期＝(cash_bs+ar)/日均支出；現金週期＝存貨+應收−應付天數（**資訊格＝單季×90；年化圖＝年×365**）；業外報酬率＝nonop/(lti或eq)
- EPS 年複合成長率＝(終點年EPS÷基期年EPS)^(1/N)−1（年度＝Q4 YTD；兩端皆須 >0）
- PSR＝股價÷每股營收；每股營收＝全年營收×1000÷股數（最新年＝近4季）

## Pipeline 指令（fetch_mops.py）

```
--probe 2330 2024 4 / --probe-div 113 [--typek otc]   端點驗證
--dividends-backfill / --update                        股利爬蟲
--build-screen                                         重建篩選彙總（含 _split_adjust）
```

---

## 維運手冊（照著做即可；弱模型請逐字執行、不要變通）

### 資料更新總表

| 資料 | 頻率 | 機制 | 需要人工？ |
|---|---|---|---|
| 上市當日收盤 | 即時（15:00–15:30 反映） | Worker /today | 否 |
| 上市快照 PE/PB/殖利率 | 每日 | 前端打 openapi（前一交易日） | 否 |
| 上櫃快照＋收盤＋當年逐月 | 平日 16:40 | tpexsnap.yml | 否 |
| 歷史年價格（雙市場） | 每月 6 日 | pricedata.yml → data/price | 否 |
| 股利（雙市場） | 每季 4/5/8/11 月 16 日 | findata.yml → div | 否 |
| **財報（XBRL）** | **每季** | **人工下載＋跑 xbrl_ingest** | **是（唯一例行人工）** |

### A. 每季 XBRL 財報更新（唯一例行人工作業）

**時機**：Q1→5/15 後、Q2→8/14 後、Q3→11/14 後、年報(Q4)→隔年 3/31 後幾天。下一次＝**2026Q2，8/14 之後**。

**步驟**：
1. 公開資訊觀測站 XBRL 資料下載專區，下載該季申報檔案整包
2. 解壓到 `~/Desktop/tw-river-repo/XBRL/tifrs-{YYYY}Q{n}/`（內含數千個 .html）
3. 執行（季別自行代換）：
```
cd ~/Desktop/tw-river-repo
python3 pipeline/xbrl_ingest.py --quarter 2026Q2
python3 pipeline/fetch_mops.py --build-screen
git add data/fin data/screen.json
git commit -m "XBRL 2026Q2 入庫"
git pull --rebase -X theirs
git push
```
**檢查**：寫入約 1900+ 檔（上市櫃）＋數百興櫃屬正常；抽驗 `python3 -c "import json;d=json.load(open('data/fin/2330.json'));print(sorted(d['q'])[-1])"` 應印新季別；push 後開網站查 2330 財務指標最右應出現新的「近4季」值。**絕不 commit XBRL 原始檔、不可 git add -A**。若大量解析失敗先 `--dry-run` 比對。

### B. 自動化失敗處理（收到 Actions 失敗信時）

**tpexsnap（每平日）**：單日失敗＝正常（髒 runner IP），不用處理隔日自癒。連 3 天以上失敗→開 log 看公司清單 state/detail；HTML errors 頁＝IP 問題手動 Run workflow 重抽；其他帶 log 開新對話。
**pricedata（每月 6 日）**：失敗先手動 Run 重試；「被擋待重試年度 ≠ 0」再 Run 續補（檔案即斷點）。每年 1 月留意新增前一完結年，抽驗 data/price/2330.json 含新年鍵。
**findata（每季 16 日）**：多半 MOPS 偶發 502，手動 Run 重跑即可（merge 冪等），跑完抽一檔看股利圖最新期別。

### C. 前端偵錯 SOP（症狀 → 檢查 → 處置）

八成「資料怪怪的」清快取就好（指令見「快取三層」節）。

| 症狀 | 依序檢查 | 處置 |
|---|---|---|
| 上市收盤不對/停舊日 | `curl -s https://tw-river-api.dale199707.workers.dev/today` 看 date | date 舊＝未發佈或快取，30 分自癒；error 見 Worker 偵錯 |
| 上櫃收盤/快照全掛 | 開 data/tpex_snap.json 看 date | 舊＝tpexsnap 失敗照 B；新＝清 localStorage |
| 河流圖歷史年空白/形狀怪 | 開 data/price/{code}.json 是否含該年 | 無檔＝pipeline 未涵蓋；有檔仍怪＝清 localStorage（殘缺快取自癒 v16 起會自動處理，清了更快） |
| 財務指標/檢驗圖空白 | 開 data/fin/{code}.json 的 q 鍵 | 無新季＝XBRL 未入庫（A 節）；金融業 inv/ar/ap 空正常 |
| 某圖整張消失 | 先查本檔「隱藏中」清單是否本來就藏 | 非隱藏清單→查該圖 gate（hasDetail/hasBorrow/hasNonop/divY/epsCagr）對應資料欄 |
| CAGR 圖顯示說明框 | — | 正常：最新完整年度 EPS≤0 無法計算 |
| 股利圖某年配息率留白 | 該年民國 <107 或 EPS≤0 | 正常（0 與算不出來不混） |
| 盤後選股某檔數字可疑 | 開 data/fin/{code}.json 看 eps YTD 是否有基準跳動 | 疑似分割→確認 _split_adjust 是否涵蓋（隱含股數比值），帶數據開新對話 |
| 整頁白畫面 | DevTools Console 看紅字 | **第一動作 git revert index.html**，再查原因；常見＝執行期 ReferenceError（見鐵律 4） |

**Worker 偵錯**：curl 端點看 JSON `{error,message,stack}`。改 Worker＝改 repo worker.js → 貼 Cloudflare Deploy → curl 驗證 → commit 備份。**Worker 內禁止 TPEX**。

### D. 前端修改 SOP（⚠️ 鐵律，弱模型逐字執行）

1. **抓 repo 最新 index.html** 為基底（不可用舊對話殘留版本）
2. 改動（str_replace 精準替換；大範圍重排用括號配對抽取區塊再重組，改完立即抽驗順序）
3. **Babel 7.26.4 實際編譯＋括號平衡**。驗證腳本（node，@babel/standalone 需 npm install @babel/standalone@7.26.4）：
```
const fs=require("fs");const Babel=require("@babel/standalone");
const html=fs.readFileSync("index.html","utf8");
const m=html.match(/<script type="text\/babel"[^>]*>([\s\S]*?)<\/script>/);
Babel.transform(m[1],{presets:[["react",{runtime:"classic"}]]});
let b=0,p=0,k=0;for(const ch of m[1]){if(ch==="{")b++;if(ch==="}")b--;if(ch==="(")p++;if(ch===")")p--;if(ch==="[")k++;if(ch==="]")k--;}
console.log("OK 括號",b,p,k);
```
4. **執行期煙霧測試（2026-07-12 白畫面事故後新增，改到 model／finView／loadHistory／finQuarters 必做）**：以 regex 抽出該 useMemo 主體，補上常數（BAND_YEARS/YEARS_BACK/CUM_FIELDS）與 finQuarters/trailing4，用真實 fin JSON（curl raw.githubusercontent 抓 data/fin/2330.json）跑「有財報／無財報（finRows=null）」兩種情境，任何 throw 都不得交付。Babel 編譯抓不到作用域／未定義變數這類執行期錯誤
5. 交付檔案給 Dale（放 repo 根目錄），git 指令單一 code block 無行內註解
6. **改壞的第一動作是 revert 不是硬修**

### E. 給 Claude Code／新對話的執行守則

1. 先完整讀本檔再動手；不確定就先 --probe / --dry-run / 貼輸出等確認
2. 循序處理、不開平行 agent；不做沒被要求的功能；git 指令單一 code block、不加行內註解
3. 只 add 指定檔案（XBRL 原始檔在 repo 內，git add -A 會災難）
4. git 順序：**commit → pull --rebase -X theirs → push**。mv 蓋檔後若 git status 出現不在預期清單的 modified，先 git diff 再決定：新版就 add、誤蓋就 `git checkout -- 檔名`
5. Dale 的下載檔一律放 repo 根目錄；.py → pipeline/、.yml → .github/workflows/
6. TPEX 三禁：禁 Worker 代理、禁瀏覽器直連、禁白天高頻爬
7. 資料檔一律單行 compact JSON（`separators=(',',':')`, `ensure_ascii=False`）
8. **隱藏功能一律 `{false&&...}` gate 保留程式碼並註記日期**，不刪除
9. **Dale 定版的公式與圖表排列不可擅改**（盤後選股公式、獲利檢驗排列、newsPick 過濾）；發現公式疑似有錯時先以真實資料驗算、把數字攤開給 Dale 確認，再動手
10. Dale 常在訊息結尾留未打完的編號（「3.」「4.」），要主動追問

## 交接時狀態（2026-07-12 凌晨，v16）

- ✅ **前端年化改版全案完成並多輪修正**：finView.Y、QChart 六項新能力、圖表定版排列、疊加柱、逐點標籤、對數尺度、缺值說明框、股利零配息全 0 圖、PSR 河流圖、快取自癒、批次保留、非上市櫃過濾（細節全在「前端重點」章）
- ✅ pipeline `_split_adjust` 分割修正上線（國巨 2327 驗證通過）
- ✅ 缺 2026Q1 清查結案：666 檔興櫃/公發/下市屬正常（前端已過濾）
- ⏳ 待辦（依序）：
  1. **1589（永冠-KY）缺 2025Q4＋2026Q1**：Dale 本機 `ls XBRL/tifrs-2025Q4 | grep 1589` 確認包內有無檔案；有→dry-run 查解析、無→公司未申報結案
  2. 前端 finQuarters 未套 _split_adjust——分割股的每股盈餘/CAGR 圖有基準鋸齒（已知，等 Dale 要求）
  3. 保留股跨裝置同步／匯出匯入（JSON 先行，零後端）
  4. QChart 雙 Y 軸擴充（股利圖柱偏矮、吃緊圖對數不直觀時的替代方案，Dale 提出再做）
  5. 觀察：8/6 pricedata 月更、tpexsnap 日常穩定度

## Dale 的專案慣例（務必遵守）

- 單檔 HTML、CDN-only、**Babel 釘 @7.26.4 + classic runtime**、繁中 UI、無建置步驟
- JSON 單行 compact；git 單一 code block 無 # 註解；只 add 指定檔案
- 循序處理、不開平行 agent；不做沒被要求的功能
- 爬蟲新端點先 --probe 請 Dale 貼輸出再繼續（開發環境無法直連 TWSE/MOPS/TPEX）
- 溝通簡潔直接；公式疑義先驗算攤數字再改
