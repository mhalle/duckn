# Presentation Extension for duckn

**Extension name:** `presentation`
**Version:** 0.1 (proposed)
**Status:** Draft — not implemented.

---

## 1. Purpose

This document defines the `presentation` extension for the duckn convention. It carries the **recommended grayscale presentation** of a scalar image: the display windows its producer suggested, and whether it is meant to be shown inverted.

The convention itself carries no display hints, and this extension does not change that principle: a window is not a property of the data, and nothing about what a sample *means* depends on it. It exists for the second of the two reasons a field earns a place in a duckn file — a standard imaging format carries its equivalent. DICOM images carry `WindowCenter`/`WindowWidth`, often several pairs with names; NIfTI carries `cal_min`/`cal_max`. A radiologist opening a CT expects it to come up in the window the scanner recommended, and a file that has lost that opens as a gray smear.

Today those values survive only inside format-specific provenance (`dicom` tags, the `nifti` header). Two things follow, and both are defects. A reader has to understand DICOM tag semantics to find a default window. And the provenance extensions are dropped when an array is derived (§4.5 of the convention), so a resampled CT arrives with no window at all, although the window — chosen for Hounsfield units — is exactly as valid as it was. This extension gives the recommendation a format-neutral home that survives derivation for as long as the quantity does (§5).

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

A per-sample entry overrides the array-level `windows` for a view of that position. The array-level `windows` remains the recommendation for any view that is not one position along that axis — a reformatted plane, a volume rendering — so a writer with per-sample windows should still provide one, and says how it was chosen in its `name` when it was not the source's own. At most one axis carries per-sample windows.

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

It differs from `linear-exact` by less than one unit of the quantity at either end, which no one will see in a CT and which matters only for an exact round trip. A DICOM window whose `VOILUTFunction` is absent is `linear`, because that is what DICOM says its absence means.

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
| `PhotometricInterpretation` `MONOCHROME1` | `invert: true` |
| `PresentationLUTShape` `INVERSE` | `invert: true` (it accompanies `MONOCHROME1`; the two together are one inversion, not two) |
| per-instance window values that differ across a series | per-sample `windows` on the slice axis (§2.3) |
| Frame VOI LUT Sequence of an enhanced multi-frame object | the same, per frame |

The numbers carry over unchanged, because DICOM's window and this extension's are both in the Modality LUT's output units.

When every instance of a series has the same windows, a converter writes them once at the array level and writes no per-sample entries. When they differ, it writes the per-sample entries and an array-level default; absent anything better, the windows of the middle instance, with a `name` that says so.

**Not mapped.** A `VOILUTSequence` — an explicit non-linear display table — has no field here. It is rare, it is defined over a specific stored representation, and whether it still applies to a derived array is not something a converter can decide; it stays in the `dicom` extension's tags, as `dicom-spec.md` describes, and is lost on derivation. `ICCProfile` likewise. Palette color images are not scalar images and are out of scope. Softcopy presentation states are separate DICOM objects, and their counterpart is an external document, not this extension.

On export to DICOM, `windows` and `invert` are written back to the attributes above. `invert: true` on an array whose stored values came from a `MONOCHROME2` source is exported as `PresentationLUTShape` `INVERSE`.

### 4.2 NIfTI

`cal_min` and `cal_max`, when not both zero, become one window: `center = (cal_min + cal_max) / 2`, `width = cal_max − cal_min`, `function` `"linear-exact"`. They are in the scaled units NIfTI's `scl_slope`/`scl_inter` produce, which is the quantity. On export the first `linear-exact` or `linear` window is written back as `cal_min = center − width/2`, `cal_max = center + width/2`.

### 4.3 NRRD

NRRD has no window field. A converter may preserve `windows` as a key/value pair of its own choosing; this extension defines none.

---

## 5. Derived Arrays

A window is chosen for a **quantity**, so it remains valid exactly as long as the quantity does. Unlike the provenance extensions, `presentation` is not dropped on derivation by default (§4.5 of the convention):

- An operation that preserves the quantity — resampling, cropping, reorienting, re-encoding to another storage type, materializing `value_transforms` — **keeps** `windows` and `invert` unchanged. The values in `sample_units` did not change meaning, so neither did the window.
- An operation that changes what the values mean — normalization, filtering that changes scale, any arithmetic that alters `sample_units` — **drops** the extension. A 40/400 window applied to z-scored data is not a recommendation, it is an error.
- Per-sample windows (§2.3) are dropped whenever the sample count of their axis changes or the axis is resampled, since they no longer correspond to positions. The array-level `windows` survives.
- An array derived *from* an image but not itself that image — a segmentation, a probability map — does not inherit the extension.

---

## 6. Consistency Rules

1. `version` is present and is a string.
2. `windows`, when present, is a non-empty array. Every window has a numeric `center` and a `width` greater than 0; a window whose `function` is `"linear"` has a `width` of at least 1.
3. `function`, when present, is one of `"linear-exact"`, `"linear"`, `"sigmoid"`.
4. `invert`, when present, is `true`.
5. The extension does not appear on an array whose `intent` is `"label-map"`, nor on an array with more than one component per sample.
6. Per-sample `windows` appears on at most one axis, and its length equals that axis's size in `shape`. Each entry is `null` or a non-empty array of windows.

All six are checkable from metadata and shape alone.

---

## 7. Design Notes

**Why this is in the file when renditions are not.** The test is whether a standard imaging format carries the equivalent, and DICOM and NIfTI both carry a recommended window. Several named renditions with rules and colormaps are carried by neither; they are a viewer's or a site's, and they live outside. This extension is the imaging formats' recommendation and nothing more, which is why it is a list of plain objects and not a rule system.

**Why not leave it in the `dicom` tags.** Because it was being lost. Provenance extensions describe a source object and are rightly dropped when an array stops re-encoding that object, but a window describes how to look at Hounsfield units, and a resampled CT is still in Hounsfield units. The tags also require every viewer to speak DICOM to find a default window, and they give a NIfTI-sourced array and a DICOM-sourced one two different places to look. The `dicom` extension may still carry the original attributes for an undisturbed round trip; this extension is what a reader uses.

**Why the quantity, not stored values.** A window is a statement about the thing measured. Placing it after `value_transforms` makes it independent of encoding: the same 40/400 applies whether the array holds `uint16` with an intercept, materialized `float32`, or a requantized copy. It is also simply where DICOM puts it.

**Why DICOM's three functions, verbatim.** `linear` and `linear-exact` differ by half a unit, which is invisible — and is also the difference between transcribing a window and approximating it. Carrying the function name costs one short string and makes the DICOM round trip exact. The default is `linear-exact` because it is the formula anyone would write down, and a converter from DICOM writes `"linear"` explicitly, since that is what DICOM's silence means.

**Why `invert` is a flag and not a window property.** `MONOCHROME1` and an inverse presentation shape describe the image, not one of its windows: every window of an inverted image is inverted. The stored values are left as the source had them — the converter does not flip them — so the flag is what tells a format-neutral reader that low values are meant to be bright.

**Why per-sample windows use the per-axis mechanism.** MR series routinely recommend a window per image, and collapsing them to one loses what the scanner said. The convention already has a place for data that parallels positions along an axis, used by the diffusion extension for gradients; using it here adds no new structure. The array-level default stays mandatory in spirit because most views of a volume are not a single source slice.

**Why non-linear display tables are left behind.** A `VOILUTSequence` is indexed by specific input values at a specific bit depth, and it is unclear what it means after resampling or re-encoding. Modeling it would buy a faithful rendering of a rare attribute on underived arrays, where the `dicom` tags already preserve it.

**What is deliberately absent.** Colormaps, opacity transfer functions, several named renditions, per-user choices, and anything for labelmaps — a segment's recommended color lives on the segment, in the `seg` extension. These are external, and this extension's values are the floor they build on.
