/**
 * Copy the compiled value-transform logic into @duckn/spatial.
 *
 * duckn-spatial is deliberately dependency-free, so it cannot import from
 * this package; and a hand-maintained copy is how the same compose bug came
 * to exist in both at once. Generating it from dist/ keeps one source.
 *
 *   node scripts/sync-spatial.mjs          write the twin
 *   node scripts/sync-spatial.mjs --check  fail if it has drifted
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../dist/valueTransforms.js");
const dst = resolve(here, "../../duckn-spatial/src/valueTransforms.js");

const header = `// GENERATED FILE — do not edit.
// Compiled from @duckn/cornerstone-loader src/valueTransforms.ts.
// Regenerate with: npm run build  (in js/duckn-cornerstone)
`;
const body = readFileSync(src, "utf8").replace(/\/\/# sourceMappingURL=.*\n?/g, "");
const want = header + body;

if (process.argv.includes("--check")) {
  let have = "";
  try { have = readFileSync(dst, "utf8"); } catch { /* missing */ }
  if (have !== want) {
    console.error("duckn-spatial/src/valueTransforms.js is out of date or edited by hand.");
    console.error("Run `npm run build` in js/duckn-cornerstone.");
    process.exitCode = 1;
  } else {
    console.log("  ok   duckn-spatial twin matches the compiled source");
  }
} else {
  writeFileSync(dst, want);
  console.log(`wrote ${dst}`);
}
