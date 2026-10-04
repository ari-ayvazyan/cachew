// Bundle the UI into dist/: one JS file, the stylesheet and index.html.
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";

rmSync("dist", { recursive: true, force: true });
mkdirSync("dist");
const out = await Bun.build({ entrypoints: ["src/main.ts"], outdir: "dist", minify: true, target: "browser" });
if (!out.success) {
  for (const log of out.logs) console.error(log);
  process.exit(1);
}
// Version the asset URLs so a rebuilt UI is never served from a stale browser cache.
const version = Date.now().toString(36);
const html = readFileSync("src/index.html", "utf8").replace(/\/assets\/(main\.js|styles\.css)/g, `/assets/$1?v=${version}`);
writeFileSync("dist/index.html", html);
cpSync("src/styles.css", "dist/styles.css");
console.log("built dist/");
