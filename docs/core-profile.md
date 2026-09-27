# duckn core: a plain volume on one page

**Status:** Draft
**Applies to:** duckn convention 1.2

This page defines nothing. [duckn-spec.md](duckn-spec.md) is authoritative;
this is the part of it a plain oriented volume needs (a CT, an MR, a
label map, a derived field), in the order a writer fills it in. Everything
else in the convention is optional, and a reader that knows only this page
still reads such a file correctly.

## The file

A Zarr v3 array. The metadata is one object under `"duckn"` in its
`attributes`; the voxels, their type, chunking and compression are Zarr's.
Keep it local as a zip store (`volume.zarr.zip`): `zarr.json` plus chunk
members, readable by any Zarr v3 library.

```json
"attributes": {
  "duckn": {
    "version": "1.2",
    "space": "left-posterior-superior",
    "space_origin": [-250.0, -250.0, -400.0],
    "sample_units": "HU",
    "value_transforms": [],
    "axes": [
      { "kind": "space", "centering": "cell", "space_direction": [0, 0, 2.5], "unit": "mm" },
      { "kind": "space", "centering": "cell", "space_direction": [0, 0.98, 0], "unit": "mm" },
      { "kind": "space", "centering": "cell", "space_direction": [0.98, 0, 0], "unit": "mm" }
    ]
  }
}
```

## The fields

| Field | What to write |
|---|---|
| `version` | `"1.2"`. The convention version: the only version number a reader acts on. |
| `space` | The world frame, by full name: `left-posterior-superior` (DICOM, ITK) or `right-anterior-superior` (NIfTI, Slicer). |
| `space_origin` | The world position of the first stored sample (index 0 on every axis). |
| `axes` | One object per array dimension, in the array's own order (`axes[i]` describes `shape[i]`). |
| `axes[].kind` | `"space"` for a spatial axis. Others exist (`time`, `RGB-color`, `list`, ...); use them as needed. |
| `axes[].centering` | `"cell"` for acquired images: a sample is the center of its cell. |
| `axes[].space_direction` | The world step for one index along this axis: direction and spacing together, not a unit vector. |
| `axes[].unit` | `"mm"` for a spatial axis. |
| `value_transforms` | `[]` when the stored values are the values. A `linear` (`slope`, `intercept`) when they are an encoding, as a CT stored in `uint16` is. |
| `sample_units` | What the values measure (`"HU"`), when you know. |

Leave out whatever you do not know. Absent means unknown, never a default.
The one field where that needs care is `value_transforms`: write `[]` for the
common case. Leaving it out in a 1.2 file says the mapping is not stated
(duckn-spec §3.1), which is right only when you cannot vouch for one.

## Reading it

- A reader acts on `version`. A minor version only adds, so a 1.0 reader
  reads a 1.2 file; it must not treat a file of a higher major version as
  its own.
- World position of index `(i0, i1, i2)` is
  `space_origin + i0*axes[0].space_direction + i1*axes[1].space_direction + ...`.
  Axis order is the array's; it says nothing about memory layout.
- Values: apply `value_transforms` in order, stored to real. An unknown
  transform name means the mapping is unknown: keep the stored values
  available, and never present them as calibrated.
- An absent `value_transforms` means identity in a 1.0 or 1.1 file and "not
  stated" from 1.2. The numbers are the stored values either way; only
  whether they are in `sample_units` differs.
- Ignore any field or extension you do not know.

## When you need more

The rest of the convention covers what a plain volume does not have:
measurement frames for vector and tensor data, per-slice positions for
irregular series, `lut` and `axis_linear` value mappings, groups of arrays,
and extensions carrying a source format's metadata (`dicom`, `nifti`,
`nrrd`) or a domain's (`seg`, `dwmri`). Each is optional, and a reader of
this page loses nothing by skipping it.
