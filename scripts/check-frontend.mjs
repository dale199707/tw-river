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

console.log("frontend bundle is up to date");
