# NIfTI Extension for duckn, 2.0

**Extension name:** `nifti`
**Version:** 2.0 (a new major: slice timing moves from this block into the geometry, and the
affine records go; a 1.x reader ignores and reports a 2.0 block, duckn 2.0 §2.3)
**For:** duckn convention 2.0 (draft, `docs/proposals/duckn-2.0.md`). 1.x files keep 1.2
(`docs/nifti-spec.md`).
**Status:** Draft, 2026-09-30, after an adversarial round on NIfTI (duckn 2.0 §18 round 11, §19
items 6, 25-27). Written by `duckn.convention2_write` (`nifti_to_zarr(convention="2.0")`,
experimental); the scenario file S21 converts to the review's reference header, and thirteen
NIfTI files agree with nibabel's affine and scaling (`scripts/convention2_corpus.py`).

This document states what 2.0 changes in nifti 1.2. Everything it does not mention - the
extension-level fields (1.2 §4.1), the `tags` it keeps (`sform_code`, `qform_code`, `dim_info`,
`intent`, `cal`, `descrip`, `aux_file`, `extensions`), the fields excluded (§5), and the files
refused (1.2 §4.4: CIFTI-2, NIfTI-MRS, Analyze 7.5) - is as 1.2 states it, read with 2.0's names.

---

## 1. The world

NIfTI's world is RAS+ for every transform code (NIfTI-1, `nifti1.h`).

- **With a transform**: three axes of `type` `space`, `positive` `right`, `anterior`, `superior`,
  ids `x`, `y`, `z`, in the spatial unit of `xyzt_units` (no unit where its code is 0). Its
  translation is `origin`; its columns are the steps of `i`, `j`, `k`.
- **Which transform.** nifti1.h defines the qform (method 2: a quaternion, `pixdim` and `qfac`)
  and the sform (method 3: a matrix) and leaves the choice between them to the reader,
  "depending on its purposes"; readers differ. The converter takes its caller's choice: the
  default is nibabel's rule (`get_best_affine`), which FSL and SPM share - the sform when
  `sform_code` > 0, else the qform; the alternative prefers the qform. SimpleITK (2.5.6) has
  rules of its own: it takes the qform for an MNI or aligned sform beside a scanner qform, and
  for a sheared sform. The choice is recorded in the provenance step's `parameters` as
  `"affine": "sform"` or `"qform"` when the file had both (only then did it decide the world). The
  transform chosen is used as written: the sform does not use `pixdim`, so a disagreement between
  its column lengths and `pixdim` is reported, never acted on. A singular or non-finite
  transform places nothing, and is passed over, reported. (duckn 0.6.3 and earlier took the qform
  whenever the sform's lengths disagreed with `pixdim`, and otherwise rescaled the sform's
  columns: a heuristic that could move a file into another frame.)
- **No shared frame.** No transform code names a frame another file can share, and no
  `world.reference` is written. Code 4 says "an MNI 152 template" without saying which (there
  are several, millimeters apart), code 3 Talairach without a version, codes 1, 2 and 5 a
  scanner, another file, some template. The codes stay in `tags`. A writer that knows the frame
  from elsewhere (the template the registration used) may state a reference for it.
- **The other transform**, where it places the grid differently, is a `world.transforms` entry
  to the bare reference `sform` or `qform` (local to the array, duckn 2.0 §1), with `to.axes`
  the same three RAS axes (with the unit only where it is known) and the affine from this world
  to that one (`other · used⁻¹`). "Differently" is judged at the precision the header holds
  them: a qform is float32 quaternion parameters, offsets and `pixdim`, so a qform written from an
  sform - what every scanner converter writes - agrees with it only to float32. The two are the
  same placement when they agree within a few float32 units in the last place of the larger
  entry, or when the qform equals the one the sform would be written as. A singular other
  transform places nothing and is not stated.
- **Neither** (both codes 0, NIfTI's "method 1"): three axes of `type` `space` with no `positive`
  and no reference, `pixdim` along each axis, origin 0 - method 1's own statement, never the
  fall-back affine some readers build.

## 2. Dimensions and units

duckn's NIfTI import keeps NIfTI's dimension order (`i`, `j`, `k`, then the 4th and 5th). The
spatial dimensions are `cell` (a voxel); a spatial dimension of length 1 has no cell (duckn 2.0
§5.1) and states its extent as `thickness`, the length of its step (`pixdim`). The 4th dimension:

- **time** when its unit (`xyzt_units`, temporal bits) is a time: a world axis `t` in that unit.
  Volume k is at `toffset` + k·`pixdim[4]` (nifti1.h), so `origin`'s time is `toffset` (plus
  slice 0's offset, §3), and the dimension steps by `pixdim[4]`. **Volumes state no
  `centering`**: nifti1.h gives a volume a time and no extent, and does not say which instant of
  its acquisition - its start, its middle - that time is (duckn 2.0 §5.1: a position with neither
  `centering` nor `thickness` is where the source placed the sample). NIfTI-2 has the same time
  fields and says no more. NIfTI-MRS and BIDS sidecars do (a dwell time, an acquisition
  duration); a converter that reads them may state it.
- **of no type** when the temporal unit is unknown (code 0): nothing says the axis is time
  (duckn 2.0 §3.1); an axis of no type, id `a3`, stepped by `pixdim[4]`, with no centering.
- **a diffusion series** when a `dwmri` block describes it (the converter had the b-values and
  gradients): a `list`, whatever `xyzt_units` says - its volumes are not a time series, and slice
  timing stays in the `dwmri` block (dwmri 2.0 §2, §6).
- **a spectrum** when its unit is not a time: `ppm` -> a `chemical-shift` axis only when `toffset`
  is set, placing the first bin; with `toffset` unset (0), an axis of no type, since 0 does not
  say the first bin is at 0 ppm (duckn 2.0 §3.1); `Hz` or `rad/s` -> a `frequency` axis, which has
  a local zero, its first bin at `toffset`.
- **components** for the intents that hold them (below), never time.
- **a 3D file with a time unit** keeps the unit in `tags.xyzt_units` and adds no time axis (an
  axis would claim one time point, duckn 2.0 §1).

`toffset` and `slice_duration` are in the unit of `pixdim[4]` (`xyzt_units`), not always seconds.

**Intents that hold components** (nifti1.h, and FSL's codes as nibabel lists them): 1004
GENMATRIX (an M × N matrix, M and N in `intent_p1` and `intent_p2`: 2 × 2 and 3 × 3 are matrix
dimensions, any other shape a `list`), 1005 SYMMATRIX, 1006 DISPVECT, 1007 VECTOR, 1010
QUATERNION, 2003 RGB_VECTOR, 2004 RGBA_VECTOR, and FSL's 2006 FNIRT displacement field (a
`vector`, `intent` `displacement-field`) and 2007-2009, 2016 and 2017 coefficient fields (a
`list`). 1008 POINTSET, 1009 TRIANGLE and 1011 DIMLESS are not. The components are the 5th
dimension, as nifti1.h lays them out, with a length-1 4th dimension that states nothing; or the
4th, in the four-dimensional layout some tools write. A statistical intent (2-24) with a 5th
dimension holds the statistic and then its parameters per voxel (nifti1.h, "STATISTICAL
PARAMETRIC DATASETS"): a `list`. **A SYMMATRIX is reordered**: nifti1.h stores the lower triangle
by rows (xx xy yy xz yz zz), and duckn's `3D-symmetric-matrix` is xx xy xz yy yz zz; the converter
permutes, and an exporter permutes back and states intent 1005 with `intent_p1` 3 (duckn 0.6.4).
A displacement field's `values.unit` is the spatial unit of `xyzt_units` (duckn 2.0 §2.2); its
components' basis is not stated by NIfTI (ITK writes LPS components in an RAS file), so no
`frame` is written: unknown, until a converter that knows its producer states one. An exporter
writes components in the file's RAS world, through the store's `frame` and world.

**RGB24 and RGBA32** (datatypes 128 and 2304) are uint8 with a trailing dimension of `RGB-color`
or `RGBA-color` components, `values.transforms` `[]` (nifti1.h: scaling does not apply to them);
NIfTI states no color space, so none is written.

## 3. Slice timing is geometry

`slice_code`, `slice_duration`, `slice_start` and `slice_end`, on the slice dimension
`dim_info.slice_dim`, become the times at which each slice was acquired within its volume:

- a **sequential** order (codes 1, 2) is a step through space and time on the slice dimension:
  each slice one `slice_duration` later (or earlier) than the one below (duckn 2.0 §5.1);
- an **interleaved** order (codes 3-6) is a time origin per slice: `samples[i].origin` restates
  slice `i`'s position and adds its time, `origin`'s time being slice 0's (duckn 2.0 §5.4).

Each slice is at its acquisition instant (duckn 2.0 §5.1), `toffset` plus its offset. A file with
no time axis (3D, or a spectral 4th dimension) keeps its slice timing in the record. A NIfTI-1
header states `slice_duration` as a 32-bit float; times are computed from its shortest decimal
(duckn 2.0 §9): three slices of 0.1 s are at 0.3 s, not 0.30000000447. NIfTI-2's fields are
64-bit and are used as they are. The four fields are then not repeated in `tags`. A
`slice_end` at or below `slice_start` (0, unset, as many writers leave it) states no range, and
every slice is timed. A range that does not cover every slice (`slice_start` > 0 or `slice_end` <
the last), an unknown code, or no `slice_dim` has no geometry, and the fields stay in
`tags.slice_timing` as the source record. `dim_info` is kept.

## 4. Values

As nifti1_io, NIfTI's reference library, reads them: `scl_slope` 0 or not finite, the stored
values are the data, `values.transforms` `[]`, whatever `scl_inter` says. A usable slope with an
intercept that is not finite: the intercept is 0 (nibabel refuses such a file; duckn reads it,
0.6.4). `scl_slope` 1 with `scl_inter` 0: `[]`. Any other: one `linear`, which applies to both
parts of a complex value. Header values a NIfTI-1 file holds as 32-bit floats (`pixdim`,
`toffset`) are copied exactly; a value computed from one uses its shortest decimal (duckn 2.0 §9).

## 5. What the record leaves out

As 1.2 §5, and in addition: `legacy.tags.sform` and `legacy.tags.qform` (the core states the
transform the world came from, and a differing other one is a transform, §1), `tags.toffset`
(the origin's time), and `tags.slice_timing` once it is geometry (§3). Nothing 2.0 writes is in
the `legacy` object, which is not written. `tags.extensions`, the header extensions, are kept
whole, as in 1.2 §4.4.

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
    { "step": [0, 0, 0, 2.0] }
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
