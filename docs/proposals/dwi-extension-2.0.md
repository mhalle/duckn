# Diffusion-Weighted MRI Extension for duckn, 2.0

**Extension name:** `dwmri`
**Version:** 2.0 (a new major: the gradients' frame becomes a matrix of their own, and a missing
one means unknown, where 1.0 read it as the identity; a 1.0 reader ignores and reports a 2.0
block, duckn 2.0 §2.3)
**For:** duckn convention 2.0 (draft, `docs/proposals/duckn-2.0.md`). 1.x files keep 1.0
(`docs/dwi-extension.md`).
**Status:** Draft, 2026-09-30. Written by `duckn.convention2_write` from its NRRD DWI import
(`nrrd_to_zarr(convention="2.0")`, experimental).

This document states what 2.0 changes in dwmri 1.0. Everything it does not mention - `b_value`,
`b_value_units`, the per-volume `gradients`, `b_matrices` and `b_values` (1.0 §4.2), implicit
normalization (§5), `acquisition` (§4.1) - is as 1.0 states it, read with 2.0's names.

---

## 1. Why a major version

dwmri 1.0 read its gradients through the core's `measurement_frame` (1.0 §6), with an absent
frame meaning the identity. Convention 2.0 has no core `measurement_frame`: a frame belongs to the
components dimension it describes (duckn 2.0 §5.3), and the DWI dimension is a `list`, whose
components are volumes, not a vector. A 1.0 block carried into a 2.0 file unrevised would read a
lost frame as the identity - gradients silently in the wrong axes, which no tensor fit reveals.

## 2. The DWI dimension

The volumes of a diffusion series are one dimension with `components: "list"` (never `vector`,
which 1.0 allowed: a list of volumes is not a spatial vector), and `intent`
`"diffusion-weighted"` (duckn 2.0 §2.2). The dimension carries the per-volume fields in its
`extensions.dwmri` block, as 1.0 §4.2 does on its axis. It may stand anywhere in the array's
dimension order: 1.0 §3's interleaving table, which read the order fastest first, is withdrawn,
and a reader finds the dimension by its block.

## 3. `frame`

| Field | Where | Meaning |
|---|---|---|
| `frame` | the top-level `dwmri` block | The basis the `gradients` and `b_matrices` are written in: a square matrix as rows, one row and column per spatial world axis; column c is the c-th basis vector, in world axes (duckn 2.0 §5.3's definition). In world axes a gradient is `F · g`, a b-matrix `F · B · Fᵀ`. |

**Absent, the gradients' frame is unknown**, and a reader does not use them for anything that
needs a direction (a tensor fit, tractography); it may still use the b-values. A writer whose
gradients are already in the world's axes writes the identity, stating it. `frame` replaces 1.0's
`gradient_frame`:

| 1.0 | 2.0 |
|---|---|
| `gradient_frame` `measurement` (or absent) with a `measurement_frame` | `frame` = that frame (read by the file's version, duckn 2.0 §14: 1.0 by columns, 1.1 and later by rows) |
| `gradient_frame` `measurement` with no `measurement_frame` | `frame` = the identity (1.0 §6's default, stated) |
| `gradient_frame` `world` | `frame` = the identity |
| `gradient_frame` `image` | no `frame`: FSL's convention flips the first component when the image's determinant is positive, and a 1.0 file does not record whether its converter did (duckn 2.0 §19 item 19) |

## 4. From other formats

- **NRRD** (`DWMRI_gradient_NNNN` with a `measurement frame`): `frame` is the measurement frame
  as rows (NRRD writes columns); with no `measurement frame`, the identity (dwi 1.0 §6's default,
  stated).
- **DICOM** (Diffusion Gradient Orientation, 0018,9089): in the patient coordinate system, so
  `frame` is the identity in a DICOM (LPS) world.
- **FSL / BIDS** (`.bvec`): a converter brings the gradients into world axes itself - applying
  FSL's first-component flip when the NIfTI affine's determinant is positive, then the rotation of
  the affine the world came from (the sform, else the qform; nifti 2.0 §1), the orthogonal factor
  of its polar decomposition, a sheared affine being reported - and writes `frame` as the
  identity. Image-frame gradients have no form in 2.0.
- **MRtrix** (`.b`): scanner coordinates, which are RAS. In an RAS world `frame` is the identity;
  in a DICOM (LPS) world it is diag(-1, -1, 1): the map from RAS to the world's axes.

## 5. What the block leaves out

1.0's `legacy.keyvalues` (the source NRRD's `DWMRI_*` key/value strings) restated the gradients
and b-value the block states: it is left out (duckn 2.0 §2.3), and an export writes the keys again
from the fields. `gradient_frame` is replaced by `frame` (§3).

## 6. Phase encoding names a dimension

1.0's `acquisition.phase_encoding_direction` (`"i"`, `"j-"`, ...) named an image axis in a layout
the file does not fix, so it is not carried from a 1.0 block; 2.0 names the array's dimension:

| Field | Meaning |
|---|---|
| `acquisition.phase_encoding` | `{ "dimension": <index into dimensions>, "polarity": "+" or "-" }`, `+` along increasing index |
| `acquisition.slice_dimension` | the index into `dimensions` that `acquisition.slice_timing` is indexed along (one time per index, in index order) |

`acquisition.slice_timing` stays in the block: a diffusion series' volumes are a list, with no
time axis for the slice times to be geometry on (duckn 2.0 §5.1).

## 7. Example

Two volumes (b = 0, then b = 1000 along the patient's left), gradients in world axes:

```json
{
  "version": "2.0",
  "intent": "diffusion-weighted",
  "world": { "axes": [
    { "id": "x", "type": "space", "unit": "mm", "positive": "left" },
    { "id": "y", "type": "space", "unit": "mm", "positive": "posterior" },
    { "id": "z", "type": "space", "unit": "mm", "positive": "superior" } ] },
  "origin": [0, 0, 0],
  "dimensions": [
    { "components": "list",
      "extensions": { "dwmri": { "gradients": [[0, 0, 0], [1, 0, 0]] } } },
    { "step": [0, 0, 2.0], "centering": "cell" },
    { "step": [0, 2.0, 0], "centering": "cell" },
    { "step": [2.0, 0, 0], "centering": "cell" }
  ],
  "values": { "transforms": [] },
  "extensions": {
    "dwmri": { "version": "2.0", "b_value": 1000, "frame": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
               "acquisition": { "phase_encoding": { "dimension": 2, "polarity": "-" } } },
    "provenance": { "version": "1.1", "sources": [ { "format": "NRRD" } ],
      "processing": [ { "name": "convert NRRD", "software": { "name": "duckn", "version": "0.7.0" } } ] }
  }
}
```
