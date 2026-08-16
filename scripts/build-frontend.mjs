import {createHash} from "node:crypto";
import {readFile, writeFile} from "node:fs/promises";
import {build} from "esbuild";

const result = await build({
  entryPoints: ["src/app.jsx"],
  bundle: true,
  minify: true,
  outfile: "app.js",
  target: "es2020",
  write: false,
});

const bundle = Buffer.from(result.outputFiles[0].contents);
const version = createHash("sha256").update(bundle).digest("hex").slice(0, 12);
await writeFile("app.js", bundle);

const index = await readFile("index.html", "utf8");
const scriptPattern = /<script src="app\.js(?:\?v=[a-f0-9]{12})?"><\/script>/;
if (!scriptPattern.test(index)) {
  throw new Error("index.html 找不到 app.js script 標籤");
}
const nextIndex = index.replace(scriptPattern, `<script src="app.js?v=${version}"></script>`);
if (nextIndex !== index) await writeFile("index.html", nextIndex);

console.log(`built app.js?v=${version}`);
