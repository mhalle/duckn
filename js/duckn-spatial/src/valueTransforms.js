/**
 * duckn value transforms (convention 1.1, spec section 4).
 *
 * Twin of duckn-cornerstone/src/valueTransforms.ts — keep the two in step.
 * That the two packages carried the same compose bug is the argument for
 * eventually sharing this rather than copying it.
 *
 * `value_transforms` describes how a physical quantity is *encoded* in the
 * stored values; `sample_units` names the quantity. Two rules from the
 * specification drive everything here:
 *
 *  - A reader must never present a partially applied chain as calibrated.
 *    If any transform cannot be applied the value mapping is undefined, and
 *    returning what you managed to apply yields numbers that look plausible
 *    and are in no defined units. Fail instead.
 *
 *  - Where a destination can carry only one affine mapping, a chain it
 *    cannot express must be *materialized*: apply it to the values and drop
 *    the transform. Cornerstone's image model has exactly one
 *    slope/intercept, so a `lut` is materialized here.
 */

const IDENTITY = { slope: 1, intercept: 0 };

/**
 * Apply a lookup table: values[clamp(stored - first_value, 0, n-1)].
 *
 * Stored values outside the table clamp to its ends, which is the DICOM
 * Modality LUT rule and the reason the table's first entry must mean
 * "at or below the low end" rather than "unknown".
 */
function applyLut(stored, values, firstValue) {
  const out = new Float32Array(stored.length);
  const last = values.length - 1;
  for (let i = 0; i < stored.length; i++) {
    let idx = Math.round(stored[i]) - firstValue;
    if (idx < 0) idx = 0;
    else if (idx > last) idx = last;
    out[i] = values[idx];
  }
  return out;
}

/**
 * Reduce a transform chain to something a consumer can apply.
 *
 * Throws on an unrecognized transform name rather than skipping it — see
 * the module comment. Callers wanting the stored values should not call
 * this at all.
 */
export function planCalibration(transforms) {
  if (!transforms || transforms.length === 0) return IDENTITY;

  // A lut indexes stored values, so it can only be first; anything before
  // it would hand it already-rescaled, typically fractional, values.
  const lutIndex = transforms.findIndex((t) => t.name === "lut");
  if (lutIndex > 0) {
    throw new Error(
      `duckn: value_transforms[${lutIndex}] is a 'lut', which must be the ` +
        `first transform in the chain`,
    );
  }

  let slope = 1;
  let intercept = 0;
  let lut = null;

  for (const t of transforms) {
    if (t.name === "linear") {
      const s = t.parameters?.slope ?? 1;
      const b = t.parameters?.intercept ?? 0;
      // Compose in order: y = s * (slope * x + intercept) + b
      slope = s * slope;
      intercept = s * intercept + b;
    } else if (t.name === "lut") {
      const values = t.parameters?.values;
      if (!values || values.length === 0) {
        throw new Error("duckn: lut transform has an empty 'values' table");
      }
      lut = { values, firstValue: t.parameters?.first_value ?? 0 };
    } else {
      throw new Error(
        `duckn: unsupported value_transform '${t.name}': the value mapping ` +
          `is undefined, so calibrated values cannot be produced`,
      );
    }
  }

  if (!lut) return { slope, intercept };

  // Non-affine: materialize. The lut runs first, then whatever affine
  // transforms followed it, and the caller is handed the identity.
  const { values, firstValue } = lut;
  const postSlope = slope;
  const postIntercept = intercept;
  return {
    slope: 1,
    intercept: 0,
    materialize: (stored) => {
      const out = applyLut(stored, values, firstValue);
      if (postSlope !== 1 || postIntercept !== 0) {
        for (let i = 0; i < out.length; i++) {
          out[i] = postSlope * out[i] + postIntercept;
        }
      }
      return out;
    },
  };
}
