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

// 當日全市場收盤價（www.twse.com.tw 盤後統計；openapi 只有前一交易日）。
// 只有回應日期＝今天（台北）才進邊緣快取；TWSE 尚未發佈時不快取，每次去源站問。
async function handleToday() {
  const cache = caches.default;
  const d = taipeiYmd();
  const key = new Request(`https://today.internal/${d}`);
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
  if (!upstream.ok) {
    return jsonResponse({ error: "upstream " + upstream.status }, 502);
  }
  const j = await upstream.json();
  const fresh = j && j.date === d;
  const body = JSON.stringify(j);
  if (fresh) {
    const cacheRes = new Response(body, {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=" + TTL_SHORT,
      },
    });
    await cache.put(key, cacheRes.clone());
  }
  return new Response(body, {
    headers: {
      ...CORS,
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": fresh ? "public, max-age=3600" : "no-store",
    },
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
    `https://bundle.internal/${stockNo}/${from}/${to}/${new Date().toISOString().slice(0, 10)}`
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
  const CHUNK = 6;
  for (let i = 0; i < plan.length; i += CHUNK) {
    const slice = plan.slice(i, i + CHUNK);
    const settled = await Promise.all(
      slice.map((p) =>
        fetchCached(p.url, p.ttl)
          .then((r) => (r.ok ? r.json() : null))
          .catch(() => null)
      )
    );
    settled.forEach((v, j) => {
      results[i + j] = v;
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
  const cacheRes = new Response(body, {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=" + TTL_SHORT,
    },
  });
  await cache.put(bundleKey, cacheRes.clone());

  const res = new Response(body, cacheRes);
  for (const [k, v] of Object.entries(CORS)) res.headers.set(k, v);
  return res;
}

export default {
  async fetch(request) {
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
  },
};
