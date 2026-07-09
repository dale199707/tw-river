const ROUTES = {
  "/openapi/": "https://openapi.twse.com.tw/",
  "/rwd/": "https://www.twse.com.tw/rwd/",
};

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "*",
};

const UA_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
  "Accept": "application/json",
};

const TTL_LONG = 604800;
const TTL_SHORT = 21600;

async function fetchCached(target, maxAge) {
  const cache = caches.default;
  const key = new Request(target);
  let res = await cache.match(key);
  if (!res) {
    const upstream = await fetch(target, { headers: UA_HEADERS });
    res = new Response(upstream.body, upstream);
    res.headers.set("Cache-Control", "public, max-age=" + maxAge);
    if (upstream.ok) {
      await cache.put(key, res.clone());
    }
  }
  return res;
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8" },
  });
}

function taipeiYmd() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10).replace(/-/g, "");
}

// 最新收盤價（www.twse.com.tw 盤後統計 STOCK_DAY_ALL，永遠回「最近一個交易日」）。
// v3：不再過濾「資料日期＝今天」——STOCK_DAY_ALL 永不比 openapi 舊，一律回傳實際資料日期，
// 由前端無條件覆蓋。修正 v2 的午夜盲區（跨日後被過濾成空，退回 openapi 卻還停在兩天前）。
// 快取：資料日期＝今天（15:00 後發佈，已定案）快取 6h；資料日期＜今天（凌晨/上午）快取 30 分，
// 讓 15:00 後的新資料能在半小時內被撿到。
async function handleToday() {
  const cache = caches.default;
  const d = taipeiYmd();
  const key = new Request(`https://today.internal/v3/${d}`);
  const hit = await cache.match(key);
  if (hit) {
    const res = new Response(hit.body, hit);
    for (const [k, v] of Object.entries(CORS)) res.headers.set(k, v);
    return res;
  }
  const upstream = await fetch(
    "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json",
    { headers: UA_HEADERS }
  );
  const text = await upstream.text();
  if (!upstream.ok) {
    return jsonResponse({ error: "upstream " + upstream.status, body: text.slice(0, 200) }, 502);
  }
  const close = {};
  let dataDate = null;
  try {
    const j = JSON.parse(text);
    if (j && /^\d{8}$/.test(String(j.date || "")) && Array.isArray(j.data)) {
      dataDate = String(j.date);
      j.data.forEach((r) => {
        const code = String(r[0]).trim();
        const v = parseFloat(String(r[7]).replace(/,/g, ""));
        if (code && isFinite(v)) close[code] = v;
      });
    }
  } catch (e) {
    for (const line of text.split(/\r?\n/)) {
      const m = line.match(/"([^"]*)"/g);
      if (!m || m.length < 9) continue;
      const f = m.map((s) => s.slice(1, -1));
      const roc = f[0].replace(/\//g, "");
      if (!/^\d{7}$/.test(roc)) continue;
      const ymd = String(parseInt(roc.slice(0, 3), 10) + 1911) + roc.slice(3);
      if (dataDate === null) dataDate = ymd;
      if (ymd !== dataDate) continue;
      const code = f[1].trim();
      const v = parseFloat(f[8].replace(/,/g, ""));
      if (code && isFinite(v)) close[code] = v;
    }
  }
  const fresh = dataDate !== null && Object.keys(close).length > 0;
  const body = JSON.stringify({ date: dataDate || d, n: Object.keys(close).length, close });
  if (fresh) {
    const ttl = dataDate === d ? TTL_SHORT : 1800;
    const cacheRes = new Response(body, {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=" + ttl,
      },
    });
    await cache.put(key, cacheRes.clone());
  }
  return new Response(body, {
    headers: {
      ...CORS,
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": fresh ? "public, max-age=1800" : "no-store",
    },
  });
}


// 個股新聞（Google News RSS -> JSON）。q=關鍵字（前端帶「代號 OR 公司名」效果不佳，帶公司簡稱即可）。
// 來源黑名單：論壇/爆料類不入列。快取 30 分。RSS 標題格式「標題 - 媒體名」，去尾綴後以 <source> 為準。
const NEWS_BLOCKLIST = ["PTT", "批踢踢", "股市爆料同學會", "Dcard", "Mobile01", "巴哈姆特"];

function xmlDecode(s) {
  return String(s || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&apos;/g, "'");
}

async function handleNews(url) {
  const q = (url.searchParams.get("q") || "").trim();
  if (!q || q.length > 40) {
    return jsonResponse({ error: "bad q" }, 400);
  }
  const cache = caches.default;
  const key = new Request(`https://news.internal/v1/${encodeURIComponent(q)}`);
  const hit = await cache.match(key);
  if (hit) {
    const res = new Response(hit.body, hit);
    for (const [k, v] of Object.entries(CORS)) res.headers.set(k, v);
    return res;
  }
  const rssUrl =
    "https://news.google.com/rss/search?q=" + encodeURIComponent(q) +
    "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant";
  const upstream = await fetch(rssUrl, { headers: UA_HEADERS });
  const text = await upstream.text();
  if (!upstream.ok) {
    return jsonResponse({ error: "upstream " + upstream.status }, 502);
  }
  const items = [];
  const re = /<item>([\s\S]*?)<\/item>/g;
  let m;
  while ((m = re.exec(text)) !== null && items.length < 15) {
    const block = m[1];
    const pick = (tag) => {
      const mm = block.match(new RegExp("<" + tag + "[^>]*>([\\s\\S]*?)<\\/" + tag + ">"));
      return mm ? xmlDecode(mm[1]).trim() : "";
    };
    const source = pick("source");
    if (NEWS_BLOCKLIST.some((b) => source.includes(b))) continue;
    let title = pick("title");
    if (source && title.endsWith(" - " + source)) {
      title = title.slice(0, title.length - source.length - 3);
    }
    const link = pick("link");
    const pub = pick("pubDate");
    let d = "";
    if (pub) {
      const t = new Date(pub);
      if (!isNaN(t)) d = new Date(t.getTime() + 8 * 3600 * 1000).toISOString().slice(0, 16).replace("T", " ");
    }
    if (title && link) items.push({ t: title, u: link, d, s: source });
  }
  const body = JSON.stringify({ q, n: items.length, items });
  const ok = items.length > 0;
  if (ok) {
    const cacheRes = new Response(body, {
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "public, max-age=1800" },
    });
    await cache.put(key, cacheRes.clone());
  }
  return new Response(body, {
    headers: { ...CORS, "Content-Type": "application/json; charset=utf-8", "Cache-Control": ok ? "public, max-age=1800" : "no-store" },
  });
}

async function handleBundle(url) {
  const stockNo = url.searchParams.get("stockNo") || "";
  const from = parseInt(url.searchParams.get("from"), 10);
  const to = parseInt(url.searchParams.get("to"), 10);
  if (!/^[0-9A-Za-z]{3,6}$/.test(stockNo) || !from || !to || to < from || to - from > 11) {
    return jsonResponse({ error: "bad params" }, 400);
  }

  const cache = caches.default;
  const bundleKey = new Request(
    `https://bundle.internal/v2/${stockNo}/${from}/${to}/${new Date().toISOString().slice(0, 10)}`
  );
  const hit = await cache.match(bundleKey);
  if (hit) {
    const res = new Response(hit.body, hit);
    for (const [k, v] of Object.entries(CORS)) res.headers.set(k, v);
    return res;
  }

  const now = new Date();
  const curY = now.getFullYear();
  const curM = now.getMonth() + 1;

  const plan = [];
  for (let y = from; y <= to; y++) {
    plan.push({
      year: y,
      kind: "price",
      ttl: y < curY ? TTL_LONG : TTL_SHORT,
      url: `https://www.twse.com.tw/rwd/zh/afterTrading/FMSRFK?date=${y}0101&stockNo=${stockNo}&response=json`,
    });
    if (y < curY) {
      plan.push({
        year: y,
        kind: "ratio",
        ttl: TTL_LONG,
        url: `https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU?date=${y}1201&stockNo=${stockNo}&response=json`,
      });
    } else {
      const mm = String(curM).padStart(2, "0");
      plan.push({
        year: y,
        kind: "ratio",
        ttl: TTL_SHORT,
        url: `https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU?date=${y}${mm}01&stockNo=${stockNo}&response=json`,
      });
      if (curM > 1) {
        const pm = String(curM - 1).padStart(2, "0");
        plan.push({
          year: y,
          kind: "ratioPrev",
          ttl: TTL_SHORT,
          url: `https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU?date=${y}${pm}01&stockNo=${stockNo}&response=json`,
        });
      }
    }
  }

  const results = new Array(plan.length).fill(null);
  let anyFail = false;
  const CHUNK = 6;
  for (let i = 0; i < plan.length; i += CHUNK) {
    const slice = plan.slice(i, i + CHUNK);
    const settled = await Promise.all(
      slice.map((p) =>
        fetchCached(p.url, p.ttl)
          .then(async (r) => (r.ok ? { ok: true, j: await r.json() } : { ok: false, j: null }))
          .catch(() => ({ ok: false, j: null }))
      )
    );
    settled.forEach((v, j) => {
      results[i + j] = v.j;
      if (!v.ok) anyFail = true;
    });
    if (i + CHUNK < plan.length) {
      await new Promise((r) => setTimeout(r, 250));
    }
  }

  const years = {};
  plan.forEach((p, i) => {
    if (!years[p.year]) years[p.year] = {};
    years[p.year][p.kind] = results[i];
  });

  const body = JSON.stringify({ stockNo, from, to, years });
  if (!anyFail) {
    const cacheRes = new Response(body, {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=" + TTL_SHORT,
      },
    });
    await cache.put(bundleKey, cacheRes.clone());
  }
  const res = new Response(body, {
    headers: {
      ...CORS,
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": anyFail ? "no-store" : "public, max-age=" + TTL_SHORT,
    },
  });
  return res;
}

export default {
  async fetch(request) {
    try {
      return await route(request);
    } catch (e) {
      return jsonResponse({ error: "worker exception", message: String(e && e.message || e), stack: String(e && e.stack || "").slice(0, 300) }, 500);
    }
  },
};

async function route(request) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS });
    }
    if (request.method !== "GET") {
      return new Response("Method not allowed", { status: 405, headers: CORS });
    }

    const url = new URL(request.url);

    if (url.pathname === "/bundle") {
      return handleBundle(url);
    }
    if (url.pathname === "/news") {
      return handleNews(url);
    }
    if (url.pathname === "/today") {
      return handleToday();
    }

    let target = null;
    for (const [prefix, base] of Object.entries(ROUTES)) {
      if (url.pathname.startsWith(prefix)) {
        target = base + url.pathname.slice(prefix.length) + url.search;
        break;
      }
    }
    if (!target) {
      return new Response("Not found", { status: 404, headers: CORS });
    }

    let res = await fetchCached(target, TTL_SHORT);
    res = new Response(res.body, res);
    for (const [k, v] of Object.entries(CORS)) {
      res.headers.set(k, v);
    }
    return res;
}
