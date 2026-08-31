/**
 * Behaviour of the duckn value-transform chain, mirroring the Python tests
 * in tests/test_value_transforms.py so the two implementations cannot drift.
 *
 * Run: npm test  (in js/duckn-cornerstone)
 */
import { planCalibration } from "../src/valueTransforms.js";

const lin = (s: number, i: number) => ({ name: "linear", parameters: { slope: s, intercept: i } });
const lut = { name: "lut", parameters: { first_value: 10, values: [0, 100, 200, 300] } };
let pass = 0, fail = 0;
const eq = (label: string, got: unknown, want: unknown) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log(`  ${ok ? "ok  " : "FAIL"} ${label}: ${JSON.stringify(got)}${ok ? "" : " want " + JSON.stringify(want)}`);
  ok ? pass++ : fail++;
};
const throws = (label: string, fn: () => unknown, re: RegExp) => {
  try { fn(); console.log(`  FAIL ${label}: did not throw`); fail++; }
  catch (e) { const m = re.test(String(e)); console.log(`  ${m ? "ok  " : "FAIL"} ${label}`); m ? pass++ : fail++; }
};

console.log("affine composition (must match Python _compose_linear_transforms)");
eq("none", planCalibration([]), { slope: 1, intercept: 0 });
eq("single", (({slope,intercept}) => ({slope,intercept}))(planCalibration([lin(1, -1024)])), { slope: 1, intercept: -1024 });
// y = 3*(2x+1)+4 = 6x+7  — the case the old first-and-break code got wrong
eq("two composed", (({slope,intercept}) => ({slope,intercept}))(planCalibration([lin(2, 1), lin(3, 4)])), { slope: 6, intercept: 7 });

console.log("\nlut");
const c = planCalibration([lut]);
eq("reports identity affine", { slope: c.slope, intercept: c.intercept }, { slope: 1, intercept: 0 });
eq("maps through table", Array.from(c.materialize!([10, 11, 12, 13])), [0, 100, 200, 300]);
eq("clamps outside", Array.from(c.materialize!([0, 9, 14, 9999])), [0, 0, 300, 300]);
const c2 = planCalibration([lut, lin(2, 1)]);
eq("lut then linear", Array.from(c2.materialize!([10, 11])), [1, 201]);

console.log("\nparity with Python (these had drifted)");
// Python's validator rejects a lut at any index != 0; checking only the
// first one let [lut, lut] through, and the second silently replaced it.
throws("second lut in chain", () => planCalibration([lut, lut]), /must be the first/);
// Python raises for float stored values rather than rounding an index.
throws("float stored values", () => planCalibration([lut]).materialize!(new Float32Array([10.5])), /integer stored values/);
throws("non-integer in plain array", () => planCalibration([lut]).materialize!([10.5]), /integer stored values/);
eq("identity is frozen", Object.isFrozen(planCalibration([])), true);

console.log("\nrefusals (spec section 4.2)");
throws("unknown name", () => planCalibration([{ name: "gamma" }]), /unsupported/);
throws("unknown after linear", () => planCalibration([lin(2, 0), { name: "gamma" }]), /unsupported/);
throws("lut not first", () => planCalibration([lin(2, 0), lut]), /first transform/);
throws("empty table", () => planCalibration([{ name: "lut", parameters: { values: [] } }]), /empty/);

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) throw new Error(`${fail} failed`);
