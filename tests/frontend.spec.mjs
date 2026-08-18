import {expect, test} from "@playwright/test";

const companies = [
  {
    公司代號: "2330",
    公司簡稱: "台積電",
    公司名稱: "台灣積體電路製造股份有限公司",
    產業別: "24",
    董事長: "魏哲家",
    實收資本額: "259323680000",
    成立日期: "19870221",
    上市日期: "19940905",
  },
  {
    公司代號: "1589",
    公司簡稱: "永冠-KY",
    公司名稱: "永冠能源國際有限公司",
    產業別: "05",
    董事長: "—",
    實收資本額: "1100000000",
    成立日期: "—",
    上市日期: "20120427",
  },
];

const tpexSnapshot = {
  updated: "2026-08-18 09:00",
  date: "20260818",
  ratioDate: "20260813",
  companies: [{
    c: "3265",
    n: "台星科",
    f: "台星科股份有限公司",
    i: "24",
    ch: "黃興陽",
    cap: 1362616590,
    est: "20000426",
    ipo: "20050802",
    b: "積體電路、IC及其測試機組之研發及測試",
  }],
  q: {3265: {pe: 25.39, pb: 3.9, yield: 2.27, close: 171}},
};

async function mockMarketApis(page) {
  const problems = [];
  page.on("console", message => {
    if (["warning", "error"].includes(message.type())) problems.push(message.text());
  });
  page.on("pageerror", error => problems.push(error.message));

  await page.route("**/openapi/v1/opendata/t187ap03_L", route => route.fulfill({json: companies}));
  await page.route("**/openapi/v1/exchangeReport/BWIBBU_ALL", route => route.fulfill({
    json: [{Code: "2330", PEratio: "27.8", PBratio: "9.7", DividendYield: "1.0"}],
  }));
  await page.route("**/openapi/v1/exchangeReport/STOCK_DAY_AVG_ALL", route => route.fulfill({
    json: [{Code: "2330", ClosingPrice: "2395", MonthlyAveragePrice: "2300"}],
  }));
  await page.route("**/data/tpex_snap.json", route => route.fulfill({json: tpexSnapshot}));
  await page.route("**/data/tpex_ytd.json", route => route.fulfill({
    json: {year: 2026, last: "2026-08-18", m: {3265: {8: {hi: 190.5, lo: 141, sum: 1969, n: 12}}}},
  }));
  await page.route("**/today", route => route.fulfill({
    json: {date: "20260814", n: 1, close: {2330: 2395}},
  }));
  await page.route("**/bundle?*", route => route.fulfill({json: {years: {}}}));
  return problems;
}

test("核心查詢、資料狀態與完整模式正常", async ({page}) => {
  const problems = await mockMarketApis(page);
  await page.goto("/");

  const search = page.getByPlaceholder("輸入股號或股名（例：2330 或 台積電）");
  await expect(page.getByRole("button", {name: "查詢"})).toBeEnabled();
  await search.fill("2330");
  await page.getByRole("button", {name: "查詢"}).click();

  await expect(page.getByText("財報截至 2026Q2", {exact: true})).toBeVisible();
  await expect(page.getByText("2,593.2", {exact: true})).toBeVisible();
  await expect(page.locator('script[src^="app.js?v="]')).toHaveCount(1);

  await page.getByRole("button", {name: "▦ 完整模式"}).click();
  await page.getByRole("button", {name: "河流圖"}).click();
  await expect(page.getByText("本益比河流圖", {exact: true})).toBeVisible();

  await search.fill("1589");
  await page.getByRole("button", {name: "查詢"}).click();
  await expect(page.getByText("無最近收盤價", {exact: true})).toBeVisible();
  await expect(page.getByText("財報截至 2025Q3", {exact: true})).toBeVisible();
  await expect(page.getByText("較市場最新季落後 3 季・以最近申報為準", {exact: true})).toBeVisible();
  expect(problems).toEqual([]);
});

test("選股表可一鍵回到頂端", async ({page}) => {
  const problems = await mockMarketApis(page);
  await page.goto("/");
  await expect(page.getByRole("button", {name: "查詢"})).toBeEnabled();
  await page.getByRole("button", {name: "◎ 選股"}).click();
  await expect(page.getByText(/符合勾選條件共 \d+ 檔/)).toBeVisible();

  const topButton = page.getByRole("button", {name: "↑ 回到表格頂端"});
  await expect(topButton).toBeVisible();
  await page.evaluate(() => {
    document.body.style.paddingBottom = "2000px";
    window.scrollTo(0, document.body.scrollHeight);
  });
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(500);
  await topButton.evaluate(button => button.click());
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeLessThan(400);
  expect(problems).toEqual([]);
});

test("上櫃股票顯示最新快照與正確市場類別", async ({page}) => {
  const problems = await mockMarketApis(page);
  await page.goto("/");

  const search = page.getByPlaceholder("輸入股號或股名（例：2330 或 台積電）");
  await expect(page.getByRole("button", {name: "查詢"})).toBeEnabled();
  await search.fill("3265");
  await page.getByRole("button", {name: "查詢"}).click();

  await expect(page.getByText("171.00", {exact: true})).toBeVisible();
  await expect(page.getByText("股價 2026/08/18", {exact: true})).toBeVisible();
  await expect(page.getByText("上櫃公司", {exact: true})).toBeVisible();
  expect(problems).toEqual([]);
});
