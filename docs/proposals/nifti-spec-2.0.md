# NIfTI Extension for duckn, 2.0

**Extension name:** `nifti`
**Version:** 2.0 (a new major: slice timing moves from this block into the geometry, and the
affine records go; a 1.x reader ignores and reports a 2.0 block, duckn 2.0 §2.3)
**For:** duckn convention 2.0 (draft, `docs/proposals/duckn-2.0.md`). 1.x files keep 1.1
(`docs/nifti-spec.md`).
**Status:** Draft, 2026-09-30. Written by `duckn.convention2_write` (`nifti_to_zarr(convention="2.0")`,
experimental); the scenario file S21 converts to the review's reference header, and eight NIfTI
files agree with nibabel's affine and scaling (`scripts/convention2_corpus.py`).

This document states what 2.0 changes in nifti 1.1. Everything it does not mention - the
extension-level fields (1.1 §4.1), the `tags` it keeps (`sform_code`, `qform_code`, `dim_info`,
`intent`, `cal`, `descrip`, `aux_file`), and the fields excluded (§5) - is as 1.1 states it, read
with 2.0's names.

---

## 1. The world

NIfTI's world is RAS+ for every transform code (NIfTI-1, `nifti1.h`).

- **With a transform** (`sform_code` > 0, else `qform_code` > 0): three axes of `type` `space`,
  `positive` `right`, `anterior`, `superior`, ids `x`, `y`, `z`, in the spatial unit of
  `xyzt_units`. The affine's translation is `origin`; its columns are the steps of `i`, `j`, `k`.
- **A shared frame** only where the code names one: 3 (Talairach) -> `world.reference`
  `nifti:talairach`, 4 (MNI 152) -> `nifti:mni152`. Codes 1 (scanner), 2 (aligned to another
  file) and 5 (some template) name no frame another file can share, and write no reference.
  `nifti:mni152` means only "the header says an MNI 152 template"; a writer that knows the variant
  may write a finer name (`nifti:mni152nlin2009casym`).
- **A qform that differs from the sform** is a `world.transforms` entry to the bare reference
  `qform` (local to the array, duckn 2.0 §1): the affine from the sform's world to the qform's.
- **Neither** (both codes 0, NIfTI's "method 1"): three axes of `type` `space` with no `positive`
  and no reference, `pixdim` along each axis, origin 0 - method 1's own statement, never the
  fall-back affine some readers build.

## 2. Dimensions and units

duckn's NIfTI import keeps NIfTI's dimension order (`i`, `j`, `k`, then the 4th and 5th). The
spatial dimensions are `cell` (a voxel). The 4th dimension:

- **time** when its unit (`xyzt_units`, temporal bits) is a time: a world axis `t` in that unit,
  stepped by `pixdim[4]`; volumes are instants (`node`), and slice times add to them (§3).
  `toffset` is the origin's time.
- **a spectrum** when its unit is not a time: `ppm` -> a `chemical-shift` axis only when `toffset`
  is set, placing the first bin (`origin` = `toffset`); with `toffset` unset (0), an axis of no
  type, since 0 does not say the first bin is at 0 ppm (duckn 2.0 §3.1); `Hz` or `rad/s` -> a
  `frequency` axis, which has a local zero.
- **a 3D file with a time unit** keeps the unit in `tags.xyzt_units` and adds no time axis (an
  axis would claim one time point, duckn 2.0 §1).

Vector and matrix intents (`intent_code` 1006-1011 and the like) are `intent` and `components`:
the components are the 5th dimension, as `nifti1.h` lays them out, with a length-1 4th dimension
that states nothing; or the 4th, in the four-dimensional layout some tools write. A displacement
field's `values.unit` is the spatial unit of `xyzt_units` (duckn 2.0 §2.2).

## 3. Slice timing is geometry

`slice_code`, `slice_duration`, `slice_start` and `slice_end`, on the slice dimension
`dim_info.slice_dim`, become the times at which each slice was acquired within its volume:

- a **sequential** order (codes 1, 2) is a step through space and time on the slice dimension:
  each slice one `slice_duration` later (or earlier) than the one below (duckn 2.0 §5.1);
- an **interleaved** order (codes 3-6) is a time origin per slice: `samples[i].origin` restates
  slice `i`'s position and adds its time, `origin`'s time being slice 0's (duckn 2.0 §5.4).

Each slice is at its acquisition instant (duckn 2.0 §5.1). The header states `slice_duration` as a
32-bit float; times are computed from its shortest decimal (duckn 2.0 §9): three slices of 0.1 s
are at 0.3 s, not 0.30000000447. The four fields are then not repeated in `tags`. A slice range
that does not cover every slice (`slice_start` > 0 or `slice_end` < the last), an unknown code, or
no `slice_dim` has no geometry, and the fields stay in `tags.slice_timing` as the source record.
`dim_info` is kept.

## 4. Values

`scl_slope` 0 or not finite: the stored values are the data, `values.transforms` `[]`, whatever
`scl_inter` says. `scl_slope` 1 with `scl_inter` 0: `[]`. Any other finite slope with a finite
intercept: one `linear`. A finite slope with an intercept that is not finite (which nifti1_io reads
as 0 and nibabel refuses): `values.transforms` left out, and reported.

## 5. What the record leaves out

As 1.1 §5, and in addition: `legacy.tags.sform` and `legacy.tags.qform` (the core states the
affine the world came from, and a differing qform is a transform, §1), `tags.toffset` (the origin's
time), and `tags.slice_timing` once it is geometry (§3). Nothing 2.0 writes is in the `legacy`
object, which is not written.

## 6. Example

The review's S21: four volumes, TR 2 s, six slices acquired interleaved (0, 2, 4, 1, 3, 5), 0.1 s
apart, in NIfTI's dimension order.

```json
{
  "version": "2.0",
  "world": { "axes": [
    { "id": "x", "type": "space", "unit": "mm", "positive": "right" },
    { "id": "y", "type": "space", "unit": "mm", "positive": "anterior" },
    { "id": "z", "type": "space", "unit": "mm", "positive": "superior" },
    { "id": "t", "type": "time", "unit": "s" } ] },
  "origin": [-12, -12, -9, 0],
  "dimensions": [
    { "step": [3, 0, 0, 0], "centering": "cell" },
    { "step": [0, 3, 0, 0], "centering": "cell" },
    { "step": [0, 0, 3, 0], "centering": "cell", "samples": [
      { "origin": [-12, -12, -9, 0.0] }, { "origin": [-12, -12, -6, 0.3] },
      { "origin": [-12, -12, -3, 0.1] }, { "origin": [-12, -12, 0, 0.4] },
      { "origin": [-12, -12, 3, 0.2] }, { "origin": [-12, -12, 6, 0.5] } ] },
    { "step": [0, 0, 0, 2.0], "centering": "node" }
  ],
  "values": { "transforms": [] },
  "extensions": {
    "nifti": { "version": "2.0", "nifti_version": 1,
               "tags": { "sform_code": 1, "qform_code": 0, "dim_info": { "slice_dim": 3 } } },
    "provenance": { "version": "1.1", "sources": [ { "format": "NIfTI" } ],
      "processing": [ { "name": "convert NIfTI", "software": { "name": "duckn", "version": "0.7.0" } } ] }
  }
}
```
