import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {build} from "esbuild";

const result = await build({
  entryPoints: ["src/app.jsx"],
  bundle: true,
  minify: true,
  outfile: "app.js",
  target: "es2020",
  write: false,
});

const actual = await readFile("app.js");
const expected = Buffer.from(result.outputFiles[0].contents);
if (!actual.equals(expected)) {
  throw new Error("app.js 與 src/app.jsx 不一致，請先執行 npm run build");
}

const expectedVersion = createHash("sha256").update(actual).digest("hex").slice(0, 12);
const index = await readFile("index.html", "utf8");
const versionMatch = index.match(/<script src="app\.js\?v=([a-f0-9]{12})"><\/script>/);
if (!versionMatch || versionMatch[1] !== expectedVersion) {
  throw new Error("index.html 的 app.js 版本碼與編譯產物不一致，請先執行 npm run build");
}
if (/text\/babel|@babel\/standalone|unpkg\.com\/react/.test(index)) {
  throw new Error("index.html 不得載入瀏覽器 Babel 或外部 React CDN");
}

console.log(`frontend bundle is up to date (app.js?v=${expectedVersion})`);
