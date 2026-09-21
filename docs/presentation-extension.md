# Presentation Extension for duckn

**Extension name:** `presentation`
**Version:** 0.1 (proposed)
**Status:** Draft — not implemented.

---

## 1. Purpose

This document defines the `presentation` extension for the duckn convention. It carries the **recommended grayscale presentation** of a scalar image: the display windows its producer suggested, and whether it is meant to be shown inverted.

The convention itself carries no display hints, and this extension does not change that principle: a window is not a property of the data, and nothing about what a sample *means* depends on it. It exists for the second of the two reasons a field earns a place in a duckn file — a standard imaging format carries its equivalent. DICOM images carry `WindowCenter`/`WindowWidth`, often several pairs with names; NIfTI carries `cal_min`/`cal_max`. A radiologist opening a CT expects it to come up in the window the scanner recommended, and a file that has lost that opens as a gray smear.

Today those values survive only inside format-specific provenance (`dicom` tags, the `nifti` header). Two things follow, and both are defects. A reader has to understand DICOM tag semantics to find a default window. And the provenance extensions are dropped when an array is derived (§4.5 of the convention), rightly, since they describe a source object; so a resampled CT arrives with no window at all, although the window — chosen for Hounsfield units — is exactly as valid as it was. This extension gives the recommendation a format-neutral home. It describes *this array's quantity*, not a source, and so travels with the quantity (§5).

It is deliberately small. It holds what the source formats hold, as plain data. Several named *renditions*, rules, colormaps, opacity transfer functions, and anything a viewer's user chooses are presentation too, but they are not carried by the imaging formats this convention exchanges with, and they belong to documents outside the file. Where such a document is applied, this extension's values are the floor beneath it: viewer default, then the file's recommendation, then the external choice.

---

## 2. Extension Fields

The extension is declared under the `"duckn"` object's `"extensions"` key. It applies to an array with one scalar component per sample, and not to a labelmap (an array whose `intent` is `"label-map"`), whose values name segments rather than measure anything.

```json
{
  "duckn": {
    "version": "1.1",
    "sample_units": "[hnsf'U]",
    "extensions": {
      "presentation": {
        "version": "0.1",
        "windows": [
          { "name": "Soft tissue", "center": 40, "width": 400, "function": "linear" },
          { "name": "Lung", "center": -600, "width": 1500, "function": "linear" }
        ]
      }
    }
  }
}
```

### 2.1 Top-Level Fields

#### `version`

Required. The version of this extension specification, as a string. While the major version is `0`, a minor version may introduce breaking changes.

#### `windows`

An array of window objects (§2.2): the windows the producer recommends, **the first being the default**. A viewer shows the first when the user has not chosen, and may offer the rest by `name`. Omit when there are none; do not include an empty array.

#### `invert`

Optional boolean. `true` recommends that the image be displayed inverted: the low end of the window bright, the high end dark. Omit when not inverted; never write `false`.

Inversion is applied to the output of the window. It does not change the stored values, the quantity, or the window's numbers.

### 2.2 Window Object Fields

| Field | Required | Description |
|-------|----------|-------------|
| `center` | yes | The window center, in `sample_units` |
| `width` | yes | The window width, in `sample_units`. Greater than 0; at least 1 when `function` is `"linear"` |
| `function` | no | How the window maps values to display intensity (§3). One of `"linear-exact"`, `"linear"`, `"sigmoid"`. When absent, `"linear-exact"` |
| `name` | no | A human-readable name for the window, as a viewer would list it |

`center` and `width` are in the array's `sample_units`, and apply to values **after** `value_transforms` — the quantity, not the stored integers. A soft-tissue window is centered at 40 HU whatever encoding holds the Hounsfield units. This is DICOM's own arrangement: its window applies to the output of the Modality LUT, which is the stage `value_transforms` represents.

An array with no `sample_units` and no `value_transforms` has windows in its stored values, which is all there is.

### 2.3 Per-Sample Windows

Some series recommend a different window for every image: MR acquisitions commonly do, since their intensities have no absolute scale. The convention's per-axis extension mechanism carries these. On the axis along which the recommendation varies, `extensions.presentation.windows` is an array with **one entry per position** along that axis, each entry an array of window objects as in §2.1, or `null` where a position has none.

```json
{
  "kind": "space",
  "extensions": {
    "presentation": {
      "windows": [
        [{ "center": 412, "width": 824, "function": "linear" }],
        [{ "center": 398, "width": 796, "function": "linear" }],
        null
      ]
    }
  }
}
```

A per-sample entry overrides the array-level `windows` for a view of that position. The array-level `windows` is the recommendation for any view that is not one position along that axis — a reformatted plane, a volume rendering. It is optional when per-sample windows are present; §4.1 says how a converter chooses one. At most one axis carries per-sample windows.

As the convention requires of any extension used on an axis, the extension is also declared at the top level. A file with per-sample windows only has a top-level entry holding just `version`.

---

## 3. Window Functions

Each function maps a value `x` of the quantity to a display intensity `y` in `[0, 1]`, from darkest to brightest, given center `c` and width `w`. They are DICOM's three VOI LUT functions (PS3.3 C.11.2.1), so that a DICOM window is transcribed rather than approximated.

**`linear-exact`** — the plain window:

- `y = (x − c) / w + 0.5`, clamped to `[0, 1]`.

The window spans `c − w/2` to `c + w/2`. This is the default, and the right choice for continuous data and for any window not taken from DICOM.

**`linear`** — DICOM's original window, defined for integer data, with half-unit offsets:

- if `x ≤ c − 0.5 − (w − 1)/2`, then `y = 0`;
- if `x > c − 0.5 + (w − 1)/2`, then `y = 1`;
- otherwise `y = (x − (c − 0.5)) / (w − 1) + 0.5`.

Its lower bound is the same as `linear-exact`'s — `c − 0.5 − (w − 1)/2` is `c − w/2` — and its ramp ends one unit lower, at `c + w/2 − 1`. No one will see that in a CT; it matters only for an exact round trip. With `w = 1` the third branch is never reached, so the function is a step at `c − 0.5` and nothing is divided by zero. A DICOM window whose `VOILUTFunction` is absent is `linear`, because that is what DICOM says its absence means.

**`sigmoid`**:

- `y = 1 / (1 + exp(−4 (x − c) / w))`.

A reader that does not support a function should fall back to `linear-exact` with the same `center` and `width`, which is always a reasonable picture.

---

## 4. Mapping to Other Formats

### 4.1 DICOM

| DICOM attribute | `presentation` field |
|---|---|
| `WindowCenter`, `WindowWidth` (one or more pairs) | `windows[n].center`, `windows[n].width`, in order; the first pair is the default |
| `WindowCenterWidthExplanation` | `windows[n].name` |
| `VOILUTFunction`: `LINEAR` or absent / `LINEAR_EXACT` / `SIGMOID` | `windows[n].function`: `"linear"` / `"linear-exact"` / `"sigmoid"`, written explicitly |
| `PresentationLUTShape`, `PhotometricInterpretation` | `invert`, as computed below |
| per-instance window values that differ across a series | per-sample `windows` on the slice axis (§2.3) |
| Frame VOI LUT Sequence of an enhanced multi-frame object | the same, per frame |

DICOM applies its window to stored values "after any Modality LUT or Rescale Slope and Intercept specified in the IOD have been applied" (PS3.3 C.11.2.1.2.1), which is the quantity `value_transforms` computes. So the numbers carry over unchanged — **on one condition**: the array's `value_transforms` must reproduce the source's Modality stage for every instance the windows belong to. A converter that could not record the rescale — because it varies from instance to instance, as in PET and some MR, and the converter neither materialized it nor found a uniform value — has written an array of stored values, and windows in the quantity would be wrong for it by a per-slice factor. In that case it writes no `windows`; they remain in the `dicom` extension's tags. Materializing a per-instance rescale into the values is the way to keep both. The same applies to the per-frame pixel value transformation of an enhanced multi-frame object.

**Inversion.** `MONOCHROME1` declares that the minimum value is displayed white; `PresentationLUTShape` `INVERSE`, in the image types that have the attribute, is how that is carried out, and DICOM requires the two to agree (PS3.3 C.8.11.3). They are one inversion, not two:

- if `PresentationLUTShape` is present, `invert` is `true` exactly when it is `INVERSE`;
- otherwise `invert` is `true` exactly when `PhotometricInterpretation` is `MONOCHROME1`.

A file in which the two disagree is self-contradictory; the converter follows the rule above and reports it. The stored values are not flipped. `PixelIntensityRelationshipSign` is not a display inversion and is not mapped.

**An unrecognized `VOILUTFunction`.** The attribute's values are defined terms, not a closed list. A window whose function the converter does not recognize is left out of `windows` — writing it without a `function` would wrongly claim `linear-exact` — and stays in the `dicom` tags. An image may also have a `VOILUTSequence` and no window at all, and then has no `windows`.

**Windows that differ across a series.** When every instance has the same windows, a converter writes them once at the array level and writes no per-sample entries. When they differ, it writes the per-sample entries (§2.3) and takes the array-level default from the **middle instance in the array's own slice order** — all of that instance's windows, in order, each with `" (middle slice)"` appended to its `name`, or named `"Middle slice"` if it had none. This is what 3D Slicer, Cornerstone3D, and Weasis do for a volume, for the reason Slicer's source gives: a frame in the middle of a series is more likely to contain the object of interest than the first or last. DICOM is silent on the question for images; its own answer for a reformatted view, the Planar MPR Volumetric Presentation State, applies one window per input volume, which is this design. Pre-scaled PET is commonly shown in a fixed SUV range whatever the file says; a missing PET window is not a defect.

A per-frame VOI in an enhanced object is a single window per frame, so its per-sample entries have one element each.

**Not mapped.** A `VOILUTSequence` — an explicit non-linear display table — has no field here. It is rare, it is defined over a specific stored representation, and whether it still applies to a derived array is not something a converter can decide; it stays in the `dicom` extension's tags, as `dicom-spec.md` describes, and is lost on derivation. `ICCProfile` likewise. Palette color images are not scalar images and are out of scope. Softcopy presentation states are separate DICOM objects, and their counterpart is an external document, not this extension.

On export to DICOM, `windows` are written back to the attributes above. `invert: true` is written as `PhotometricInterpretation` `MONOCHROME1`, which the CT, MR, PET, CR, DX, and MG image types all permit and which requires no change to the pixel data: it is a statement about display. `PresentationLUTShape` `INVERSE` is written **as well** only in the image types that define the attribute (the DX and MG family), where the two must agree; it is not an attribute of a CT or MR image and is not written into one. An image type that permits only `MONOCHROME2` cannot express `invert`, and the exporter reports the loss.

### 4.2 NIfTI

`cal_min` and `cal_max` become one window when `cal_max > cal_min`: `center = (cal_min + cal_max) / 2`, `width = cal_max − cal_min`, `function` `"linear-exact"`. The header says the fields are used "if nonzero" and apply to "(possibly scaled) dataset values" — the units `scl_slope`/`scl_inter` produce, which is the quantity. This mapping treats the pair jointly rather than field by field, so that a genuine window from 0 to 500 is kept: both zero means unset, and `cal_max ≤ cal_min` otherwise is not a window and yields none. On export the first `linear-exact` window is written back as `cal_min = center − width/2`, `cal_max = center + width/2`; a `linear` window's ramp ends one unit lower (§3), so its `cal_max` is `center + width/2 − 1`. NIfTI has no field for `invert`, which is dropped and reported.

### 4.3 NRRD

NRRD has no window field. A converter may preserve `windows` as a key/value pair of its own choosing; this extension defines none.

---

## 5. Derived Arrays

The convention drops metadata that describes a *source* when an array is derived from it, and keeps the fields that describe *the array itself* — `sample_units` among them (§4.5 of the convention). A window is of the second kind: it is a statement about the array's quantity, and it stays true exactly as long as the quantity does. So `presentation` is governed by what an operation does to the values, which the implementation of an operation always knows:

- **Kept** by an operation that re-samples or re-encodes the same quantity: resampling, cropping, reorienting, rechunking, changing the storage type, and the convention's preserve, materialize, and re-encode write policies (§4.3 of the convention). A linear filter whose weights sum to one — smoothing, the anti-aliasing a resample applies — leaves values in the same units (§4.4 of the convention) and keeps it too.
- **Dropped** by an operation that maps values through anything else: normalization, rescaling to a storage range, a gradient or difference operator, any nonlinear remapping.

A change to `sample_units` is therefore *sufficient* reason to drop the extension, but it is not necessary, and its absence proves nothing: an MR image has no `sample_units` before or after it is normalized, and its window is wrong afterward all the same. The decision belongs to the operation, not to a comparison of metadata.

Per-sample windows (§2.3) follow the lifecycle of the per-position data the convention already has in `axes[].samples`: a crop or a subset of positions slices the array of entries with the data; an operation that interpolates along that axis, or reorders it, drops them. The array-level `windows` is unaffected.

A validator with the data can catch a stale window after the fact, whatever produced it: a window whose interval `[center − width/2, center + width/2]` does not intersect the array's range of values is worth a warning.

**What this asks of the convention.** §4.5 of the convention lists the fields that survive derivation and mentions extensions only as things to drop. It should distinguish extensions that describe a source (`dicom`, `nifti`, `fits` — dropped) from extensions that describe the array (kept, subject to their own rules), and name this one as the first of the second kind. The convention's note that window and level belong in application-specific attributes should likewise say that an extension may carry a *producer's recommendation* where an exchange format carries its equivalent. The released resampler already behaves this way — it drops only the three provenance extensions — but normalization in `cast` clears `sample_units` and leaves extensions in place, which this rule requires it to change.

---
## 6. Consistency Rules

1. `version` is present and is a string.
2. `windows`, when present, is a non-empty array. Every window has a numeric `center` and a `width` greater than 0; a window whose `function` is `"linear"` has a `width` of at least 1.
3. `function`, when present, is one of `"linear-exact"`, `"linear"`, `"sigmoid"`.
4. `invert`, when present, is `true`.
5. The extension does not appear on an array whose `intent` is `"label-map"`, nor on an array with more than one component per sample.
6. Per-sample `windows` appears on at most one axis, and its length equals that axis's size in `shape`. Each entry is `null` or a non-empty array of windows. The extension is declared at the top level whenever it appears on an axis.

All six are checkable from metadata and shape alone.

---

## 7. Design Notes

**Why this is in the file when renditions are not.** The test is whether a standard imaging format carries the equivalent, and DICOM and NIfTI both carry a recommended window. Several named renditions with rules and colormaps are carried by neither; they are a viewer's or a site's, and they live outside. This extension is the imaging formats' recommendation and nothing more, which is why it is a list of plain objects and not a rule system.

**Why not leave it in the `dicom` tags.** Because it was being lost. Provenance extensions describe a source object and are rightly dropped when an array stops re-encoding that object, but a window describes how to look at Hounsfield units, and a resampled CT is still in Hounsfield units. The tags also require every viewer to speak DICOM to find a default window, and they give a NIfTI-sourced array and a DICOM-sourced one two different places to look. The `dicom` extension may still carry the original attributes for an undisturbed round trip; this extension is what a reader uses.

**Why the quantity, not stored values.** A window is a statement about the thing measured. Placing it after `value_transforms` makes it independent of encoding: the same 40/400 applies whether the array holds `uint16` with an intercept, materialized `float32`, or a requantized copy. It is also simply where DICOM puts it.

**Why DICOM's three functions, verbatim.** `linear` and `linear-exact` differ by half a unit, which is invisible — and is also the difference between transcribing a window and approximating it. Carrying the function name costs one short string and makes the DICOM round trip exact. The default is `linear-exact` because it is the formula anyone would write down, and a converter from DICOM writes `"linear"` explicitly, since that is what DICOM's silence means.

**Why `invert` is a flag and not a window property.** `MONOCHROME1` and an inverse presentation shape describe the image, not one of its windows: every window of an inverted image is inverted. The stored values are left as the source had them — the converter does not flip them — so the flag is what tells a format-neutral reader that low values are meant to be bright.

**Why per-sample windows use the per-axis mechanism.** MR series routinely recommend a window per image, and collapsing them to one loses what the scanner said. The convention already has a place for data that parallels positions along an axis, used by the diffusion extension for gradients; using it here adds no new structure. The array-level default comes from the middle instance because that is what volume viewers already do, and a converter that names it as such lets a reader tell a scanner's series-wide recommendation from a converter's choice.

**Why non-linear display tables are left behind.** A `VOILUTSequence` is indexed by specific input values at a specific bit depth, and it is unclear what it means after resampling or re-encoding. Modeling it would buy a faithful rendering of a rare attribute on underived arrays, where the `dicom` tags already preserve it.

**What is deliberately absent.** Colormaps, opacity transfer functions, several named renditions, per-user choices, and anything for labelmaps — a segment's recommended color lives on the segment, in the `seg` extension. These are external, and this extension's values are the floor they build on.
