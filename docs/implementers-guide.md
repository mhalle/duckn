# Implementer's Guide

**Status:** Draft
**Applies to:** duckn convention 1.1

This guide is for people writing code against duckn — readers, writers,
converters. It does not define anything; [duckn-spec.md](duckn-spec.md) and
the extension specifications are authoritative. What it collects are the
rules that are easy to get wrong, each of which has been a real bug.

The theme running through all of them: **metadata must agree with the bytes
it describes.** Metadata that is merely missing costs a reader information.
Metadata that is confidently wrong costs them their result.

---

## 1. Reading values

### Stored values and calibrated values are different things

`value_transforms` describes how a physical quantity is *encoded* in the
stored values; `sample_units` names the quantity. A CT array stored as
`uint16` with `{slope: 1, intercept: -1024}` holds `1000` where the
quantity is `-24 HU`. Both are legitimate views:

```python
vol.raw    # stored values: uint16, 1000
vol.data   # calibrated:    float32, -24.0   ← the quantity, in sample_units
```

Offer both, and make which one a caller is getting unmistakable. duckn
applies transforms by default, matching nibabel, SimpleITK, and xarray;
pydicom and GDAL default to raw. Either is defensible — silently varying
between them is not.

### Never present a partly transformed array as calibrated

If any transform in the chain cannot be applied — an unrecognized `name`, a
malformed parameter set — the value mapping is undefined. Returning what
you managed to apply produces numbers that look plausible and are in no
defined units.

```python
# Right: fail the calibrated path, keep raw access working
raise ValueError(f"unsupported value_transform {name!r}: ...")
```

This matters more as the vocabulary grows. A reader implementing 1.1 will
eventually meet a file written against 1.2, and "skip what I don't know"
is the wrong instinct here.

### Treat an unknown transform name as non-affine

An all-affine chain can be collapsed into one `(slope, intercept)` pair and
applied in a single pass. That optimization must be gated on the names you
actually recognize, not on "is it a `lut`" — otherwise a future transform
type silently takes the affine path and is dropped.

---

## 2. Writing values

Writing is choosing an encoding for a known quantity. Three policies are
defined (§4.3), and the choice is forced by what the destination can carry.

| Policy | Write | `value_transforms` | Use when |
|---|---|---|---|
| Preserve | stored values | carried forward | the quantity is unmodified and the format can carry the transform |
| Materialize | calibrated values | **dropped** | the quantity changed, or the format cannot carry a transform |
| Re-encode | requantized values | newly derived | you want a specific storage type; affine transforms only |

**Prefer preserve.** It is the only reversible policy — you can materialize
later from preserved data, but you cannot recover the original stored
values from materialized data.

### The one hazard to design against

Writing calibrated values while keeping the transforms that produced them
applies the chain twice on the next read. It is silent, and the result
looks plausible.

```python
# WRONG — vol.data is already calibrated
write(vol.data, vol.metadata)          # metadata still says intercept=-1024

# Right
meta = vol.metadata.model_copy(update={"value_transforms": None})
write(vol.data, meta)                  # sample_units is unchanged; the
                                       # quantity did not change, only its encoding
```

This is why an interface that takes an array and metadata as unrelated
arguments is a poor one: it permits the mismatch. Prefer passing something
that knows which encoding its array is in.

### What each format can carry

| Format | Value mapping | Consequence |
|---|---|---|
| Zarr (duckn) | any chain | preserve |
| NIfTI | one affine, via `scl_slope`/`scl_inter` | preserve a single `linear`; materialize anything else |
| DICOM | affine (`RescaleSlope`/`Intercept`) or one table (Modality LUT Sequence) | preserve either; refuse what neither expresses |
| NRRD | none | always materialize |

NRRD's `old min`/`old max` is not a substitute. It records the range the
data spanned *before* a quantization step rather than stating a mapping —
the forward direction lives in the storage type's range, not the metadata —
so writing a transform into it would mean inventing an original range the
data never had.

Where a format genuinely cannot represent a mapping, **refuse rather than
approximate**. DICOM's LUT Data is unsigned 16-bit, so a table of negative
Hounsfield units has no Modality LUT form; rounding or wrapping it would
write different numbers than the array means.

---

## 3. Resampling and filtering

### Affine transforms commute with interpolation; nothing else does

Interpolation is a weighted average whose weights sum to one, so scaling
before or after gives the same answer. You may resample stored values
directly and carry an affine chain forward unchanged.

A `lut` does not commute. The table applied to an interpolated stored value
is not the interpolation of the looked-up values, and the difference is not
small — a steep table turns `[0, 429, 857, …]` into `[0, 0, 1000, 0, …]`.
Apply a non-affine transform **before** resampling, which makes the result
materialized.

Nearest-neighbor is exempt: it selects an existing sample rather than
averaging, so it commutes with any transform. That matters, since label
maps are the arrays most likely to carry a table and be resampled that way.

### Weights that do not sum to one

Commutation for affine transforms holds for *interpolation* specifically. A
gradient, difference, or edge operator does not carry the intercept
correctly — apply those to calibrated values.

---

## 4. Derived arrays

An array is derived **with respect to a given source** when it no longer
faithfully re-encodes that source. Derivation is a property of a
relationship, not of an array: a segmentation is derived with respect to
the image it segments, but a duckn array converted from a segmentation
*file* faithfully re-encodes that file.

Metadata inherited from a source does not survive derivation with respect
to it:

```python
# after resampling
new_meta.extensions.pop("dicom", None)   # describes a source this array
                                         # no longer re-encodes
```

The reason is that inheritance has no defined semantics — no general rule
says which of a source's attributes survive an arbitrary operation, and an
array derived from several sources can inherit contradictions. Dropping
asserts nothing, which is what makes it safe.

Fields that describe *this* array — `axes`, `space`, `space_origin`,
`sample_units`, and the `value_transforms` matching what was written — are
computed for the derived array and remain authoritative.

Recording what an array was derived *from* is the
[`provenance`](provenance-extension.md) extension's job, not the
convention's. That extension is drafted but not yet implemented, so a
derived array currently carries no record of its origin — if your pipeline
needs one, you have to keep it yourself for now.

---

## 5. Axis order

duckn arrays are C-ordered: `axes[0]` describes the slowest-varying
dimension, matching `shape[0]`. NRRD is the opposite — its header lists the
fastest-varying axis first.

Converting between them means reversing per-axis fields *and* reading or
writing the array in matching order. Getting one right and not the other
pairs a header with a transposed array:

```python
nrrd.read(path, index_order="C")            # not pynrrd's Fortran default
nrrd.write(path, data, header, index_order="C")
```

This class of bug hides from round-trip tests, because a reader and writer
that are both wrong cancel out. **Check against an independent
implementation** — SimpleITK reading a file your writer produced, for
instance — not just against your own reader.

---

## 6. Geometry

`space_origin` is the world position of the first sample — the one at the
lowest memory address — independent of centering and of axis order.

`space_direction` on each axis is a full vector, not a scalar spacing: its
direction is the axis's orientation in world space and its magnitude is the
sample spacing. Anisotropic and oblique volumes are the normal case.

`measurement_frame` maps vector and tensor *component values* into world
space. It is not a coordinate space, and not a valid target in
`space_transforms`.

Reversing axis order reverses which `space_direction` belongs to which
dimension. It does not change the vectors themselves.

---

## 7. Extensions

Read an extension you recognize; ignore one you do not. Every extension in
use must be declared in the top-level `extensions` object with at least a
`version`, even when its data lives on individual axes.

Extension fields never override convention fields. Where a `dicom` tag and
a convention field appear to describe the same thing, the convention field
is authoritative — the tag describes the source object.

---

## 8. Validation worth doing

Cheap, metadata-only, and worth running before you write:

- `len(axes) == len(shape)`, and any `kind` with a required size matches
- a `lut` is first in its chain and has a non-empty table
- for segmentations: `validate_seg_extension()` — unique ids, no label 0,
  no dangling or circular references, no duplicate effective label sets

```python
from duckn.models import duckn_attrs          # re-validates on the way out
from duckn.models import validate_seg_extension, effective_label_values
```

Re-validate metadata at write time. Pydantic validates at construction, so
a model mutated afterwards can hold a state no reader will accept — better
to fail the write than to produce a store nobody can open.

---

## 9. Things that have actually gone wrong

Each of these shipped at some point. They are the reason for the rules
above.

| Symptom | Cause |
|---|---|
| CT values off by exactly 1024 HU after a round trip | adapter wrote calibrated values, kept the transforms |
| One slice of a series wrong by the intercept | rescale read from the first instance, not checked across the series |
| Values silently uncalibrated in an exported file | writer emitted stored values under a `sample units` header |
| Geometry attached to the wrong axes | `index_order` omitted on one side of a NRRD round trip |
| Array read as 0–4095 instead of Hounsfield units | transforms never applied — the classic |
| Resampled label map full of nonsense | a `lut` interpolated as indices instead of values |
| Store nobody can open | metadata mutated after construction, never re-validated |
