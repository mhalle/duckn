/**
 * Copy the compiled value-transform logic into the plain-JS packages.
 *
 * duckn-spatial and duckn-reader are deliberately free of any dependency on
 * this package, and a hand-maintained copy is how the same compose bug came
 * to exist in two places at once. Generating them from dist/ keeps one source.
 *
 *   node scripts/sync-spatial.mjs          write the twin
 *   node scripts/sync-spatial.mjs --check  fail if it has drifted
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../dist/valueTransforms.js");
const targets = [
  resolve(here, "../../duckn-spatial/src/valueTransforms.js"),
  resolve(here, "../../duckn-reader/src/valueTransforms.js"),
];

const header = `// GENERATED FILE — do not edit.
// Compiled from @duckn/cornerstone-loader src/valueTransforms.ts.
// Regenerate with: npm run build  (in js/duckn-cornerstone)
`;
const body = readFileSync(src, "utf8").replace(/\/\/# sourceMappingURL=.*\n?/g, "");
const want = header + body;

const check = process.argv.includes("--check");
let failed = false;

for (const dst of targets) {
  const name = dst.split("/").slice(-3, -2)[0];
  // When installed from git, `prepare` builds but the siblings are absent.
  // Nothing to sync, and not a failure.
  if (!existsSync(dirname(dst))) {
    console.log(`  (${name} not present — skipping)`);
    continue;
  }
  if (check) {
    let have = "";
    try { have = readFileSync(dst, "utf8"); } catch { /* missing */ }
    if (have !== want) {
      console.error(`${name}/src/valueTransforms.js is out of date or hand-edited.`);
      failed = true;
    } else {
      console.log(`  ok   ${name} twin matches the compiled source`);
    }
  } else {
    writeFileSync(dst, want);
    console.log(`wrote ${dst}`);
  }
}

if (failed) {
  console.error("Run `npm run build` in js/duckn-cornerstone.");
  process.exitCode = 1;
}
