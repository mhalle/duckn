# DICOM Extension for duckn, 2.0

**Extension name:** `dicom`
**Version:** 2.0 (a new major: a varying rescale moves from the per-slice record to the core's
`axis_linear`, where a 1.0 reader looks for it in `samples`; a 1.0 reader ignores and reports a
2.0 block rather than misread it, duckn 2.0 §2.3)
**For:** duckn convention 2.0 (draft, `docs/proposals/duckn-2.0.md`). 1.x files keep 1.0
(`docs/dicom-spec.md`).
**Status:** Draft, 2026-09-30; the MR Diffusion row of §3 added 2026-10-01 (duckn 2.0 §19 item 29). Written by `duckn.convention2_write` (`dicom_to_zarr(convention="2.0")`,
experimental); the scenario series S17, S19 and S26 convert to the review's reference headers, and
a GE, a Siemens and a Philips series agree with pydicom voxel for voxel
(`scripts/convention2_corpus.py`).

This document states what 2.0 changes in dicom 1.0. Everything it does not mention - the tag
encoding (1.0 §4), the groups by name (§5), splitting tags between the series and the slices
(§6.1), redaction as `null` with `anonymized: true` (§4.3), `stored_values` and the rule that
nothing in stored-value units is written of values that are not stored values (§5.10), private
elements (§9), and that the block describes the source, not the array (§10.1) - is as 1.0 states
it, read with 2.0's names (`axes` -> `dimensions`, `space_direction` -> `step`, `sample_units` ->
`values.unit`, `value_transforms` -> `values.transforms`).

---

## 1. The `dicom:` reference

This extension defines the reference prefix `dicom:` (duckn 2.0 §1, §3.2). Its value is a DICOM
UID: one to 64 characters, digits and dots, no component of more than one digit starting with 0
(PS3.5 §9.1).
A reader of this extension reports a value that breaks this grammar; the core does not refuse it
(duckn 2.0 §10 rule 11).

A DICOM UID names one frame whichever kind it is, so one prefix serves both of DICOM's frames, and
what a reference covers is said by where it stands:

- **Frame of Reference UID** (0020,0052) -> `world.reference` `dicom:<UID>`, which covers the
  world's spatial axes: arrays that carry the same one are in one patient frame. The UID also stays
  in `tags`, as the source record (§3).
- **Synchronization Frame of Reference UID** (0020,0200) -> stays in `tags` only, unless the time
  axis is measured from that frame's zero; then it is also an identity transform on the time
  axis: `{ "to": { "reference": "dicom:<UID>", "axes": [ ...the time axis... ] }, "on": ["t"],
  "forward": { "identity": true } }`. Trigger Time (after the R wave) and Acquisition Time measured
  from the first frame (§5) are not measured from its zero, so a converter of them does not state
  the frame (duckn 2.0 §3.2).

## 2. The world

A converter writes the world DICOM's patient coordinates are in: three axes of `type` `space`,
`positive` `left`, `posterior`, `superior`, in `mm`, ids `x`, `y`, `z` (duckn 2.0 §3). Image
Position (Patient) of the first slice - the lowest along the normal, the cross product of the row
and column direction cosines of Image Orientation (Patient) - is `origin`. The row index steps
along the column direction (Image Orientation's second triplet) by Pixel Spacing's first value,
the column index along the row direction (the first triplet) by its second. The slice step is the
spacing between slices along their normal. Slices evenly spaced (differences within 0.001 mm)
take a step; slices that are not take a step of 1 mm along the normal and each slice's
`position` in mm (duckn 2.0 §5.4). Slices shifted in-plane by the same amount each (a uniform
gantry tilt) are a sheared step; slices shifted unevenly take each slice's `origin`, and
non-parallel slices each slice's `origin` and `steps`. Slice Thickness is `thickness`.

## 3. What the record leaves out

The block's `tags` (and each sample's `metadata.dicom`) keep every attribute the converter read,
except the list below, which is closed (duckn 2.0 §2.3): two converters that both write a record
write the same one (a record is optional, duckn 2.0 §9).

| Left out | Because the core states it as |
|---|---|
| Image Position (Patient), Image Orientation (Patient) | `origin`, `step`, `samples[i].position`, `.origin`, `.steps` |
| Pixel Spacing, Spacing Between Slices | the steps |
| Slice Thickness | `thickness`, `samples[i].thickness` |
| Rows, Columns, Number of Frames, Bits Allocated, Pixel Representation | Zarr's `shape` and `data_type` |
| Rescale Slope, Rescale Intercept, Rescale Type; Modality LUT Sequence; Pixel Value Transformation Sequence | `values.transforms` and `values.unit` (a rescale that varies too, §4) |
| (none: Color Space is kept) | Color Space is stated as the RGB dimension's `color_space` (1.0 §2's terms) and also stays in `tags` as the source's, as in 1.0 |
| Planar Configuration; Pixel Data and the other bulk data; overlay and curve groups; group 0002; group lengths; the Per-frame Functional Groups Sequence | as dicom 1.0 §9 |
| Bits Stored, High Bit, pixel value ranges, Pixel Padding Value and Range Limit, Real World Value Mapping, the Palette Color Lookup Table Descriptors, Data and UID - *when `stored_values` is `false`* | stated in stored-value units, which the array does not hold (1.0 §5.10); the padding is restated (§4) |
| The MR Diffusion attributes - Diffusion b-value (0018,9087), Diffusion Gradient Orientation (0018,9089), Diffusion Directionality (0018,9075), Diffusion b-value XX-ZZ (0018,9602-9607), the MR Diffusion Sequence (0018,9117) and its Diffusion Gradient Direction Sequence - *when a `dwmri` block states them* | the `dwmri` block (dwmri 2.0 §6), from which an export writes them again: two copies in two frames could disagree |
| private elements, all or none, at the writer's choice | as 1.0 §9; never read as restating the core or another extension (a diffusion direction a converter derived from them is recorded as derived, dwmri 2.0 §5) |

Identifiers and acquisition facts are **kept, even where the core states a fact computed from
them**: the Frame of Reference UID beside `world.reference`, each slice's `SOPInstanceUID`,
`InstanceNumber` and **Slice Location** (whose zero DICOM leaves unstated: the core does not state
it), and each phase's Trigger Time beside its time position (§5).

Per-slice tags may sit in `samples` on a regular dimension, one with a step and no positions (1.0
§6.2 said `samples` are omitted there); the samples then carry only `metadata`.

## 4. Values

- **Stored values** (`stored_values` `true`): `values.transforms` is the series' rescale, one
  `linear`, or `[]` where the source has none (duckn 2.0 §6: no rescale and no Modality LUT is the
  identity); `values.unit` is Rescale Type (`HU` as `{ "symbol": "HU", "scheme": "UCUM", "code":
  "[hnsf'U]" }`) - for CT, HU where Rescale Type is absent (PS3.3 C.8.2.1 requires it only when it
  is not HU); for PET, Units (0054,1001) `BQML` as `Bq/mL`; never `US`, Rescale Type's and Modality LUT
  Type's defined term for *unspecified* (PS3.3 C.11.1.1.2), which states no unit (a Modality of
  `US`, ultrasound, is another attribute and stays in the record).
- **Materialized values** (`stored_values` `false`): the array holds the quantity, and
  `values.transforms` is `[]`.
- **A rescale that varies along one dimension** is an `axis_linear` on it. Along the slices: one slope and one
  intercept per slice (a number where all agree), an instance with no rescale being the identity;
  `values.unit` is the Rescale Type every slice shares. The per-slice rescale is then not repeated
  in the record. Along the time points only (a dynamic PET with one slope per frame), it is an
  `axis_linear` on the time dimension (duckn 2.0 §13.6); duckn's converter does not state that
  case yet. Along both, it has no form yet (duckn 2.0 §6): `values.transforms` is left out and
  each slice keeps its own rescale in its `metadata.dicom`, the only statement of it.
- **Padding.** A single Pixel Padding Value is restated as `values.missing` (duckn 2.0 §6) under
  either policy, in the quantity's units: -2000 stored under an intercept of -1024 is `[-3024]`. It
  is restated only where §6 allows one list - the source's rescale is one for the series or none,
  there is no Pixel Padding Range Limit, no Modality LUT - and is otherwise left out (absent, never
  `[]`). The Pixel Padding Value itself stays in `tags` only while `stored_values` is `true`.

## 5. Time

A 4D series' time dimension comes only from real times; an index (Temporal Position Identifier,
Instance Number) is not a time, and a dimension ordered by one states nothing.

- **Trigger Time** (0018,1060): a world axis `{ "id": "t", "type": "time", "unit": "ms", "name":
  "time after R wave" }`, measured from the R wave as the source states it: a first phase at 20 ms
  has the time component 20 in `origin`. A trigger time is an instant, so the dimension is `node`.
  A phase's time is its first slice's Trigger Time; each phase's Trigger Time stays in its
  sample's `metadata.dicom` where it is one value across the phase's slices, and is left out
  where it varies across them (it varies along both dimensions, 1.0 §6.1).
- **Acquisition Time** (0008,0032): the time of day at which the acquisition *started*, measured
  from the first frame, in `s`; a series that crosses midnight keeps increasing. A converter that
  knows each frame's duration (PET's Actual Frame Duration, 0018,1242, in ms: converted to s)
  places each frame at its middle with that `thickness`; one that does not writes the time with no
  `centering`, claiming no moment within the acquisition (duckn 2.0 §5.1). The two write
  different headers for one source: the extension decides that a converter reading durations is
  the one duckn 2.0 §9 means, and duckn's converter does not read them yet.
- Frames whose times are evenly spaced take a step of that spacing (for PET frames, the spacing of
  their middles, `origin`'s time at the first middle); others take positions on a step of one time
  unit (duckn 2.0 §5.4, §13.6).

## 6. Enhanced (multi-frame) objects

Per-frame geometry (Plane Position, Plane Orientation) goes onto the slice dimension's `samples`
as 1.0 §6.2 does for single-frame series: positions, origins, or origins and steps. A Pixel Value
Transformation per frame is the `axis_linear` of §4; one shared, or per frame and all equal, is the
one `linear`. An exporter writes the mapping where the IOD puts it: for Enhanced CT, MR and PET the
Pixel Value Transformation functional group, shared for a `linear`, per frame for an
`axis_linear` along the slice dimension.

## 7. Example

The review's S17: three CT slices 2 mm apart, stored values preserved, padding -2000 under an
intercept of -1024, acquisition times kept per slice.

```json
{
  "version": "2.0",
  "world": {
    "reference": "dicom:1.2.3.4",
    "axes": [
      { "id": "x", "type": "space", "unit": "mm", "positive": "left" },
      { "id": "y", "type": "space", "unit": "mm", "positive": "posterior" },
      { "id": "z", "type": "space", "unit": "mm", "positive": "superior" }
    ]
  },
  "origin": [-10, -20, 30],
  "dimensions": [
    { "step": [0, 0, 2.0], "centering": "cell", "thickness": 2.0,
      "samples": [
        { "metadata": { "dicom": { "AcquisitionTime": "101500.000" } } },
        { "metadata": { "dicom": { "AcquisitionTime": "101500.500" } } },
        { "metadata": { "dicom": { "AcquisitionTime": "101501.000" } } } ] },
    { "step": [0, 0.5, 0], "centering": "cell" },
    { "step": [0.5, 0, 0], "centering": "cell" }
  ],
  "values": {
    "unit": { "symbol": "HU", "scheme": "UCUM", "code": "[hnsf'U]" },
    "transforms": [ { "name": "linear", "parameters": { "slope": 1, "intercept": -1024 } } ],
    "missing": [-3024]
  },
  "extensions": {
    "dicom": {
      "version": "2.0",
      "stored_values": true,
      "tags": { "Modality": "CT", "KVP": 120, "Manufacturer": "Acme",
                "FrameOfReferenceUID": "1.2.3.4", "SynchronizationFrameOfReferenceUID": "1.2.3.9",
                "PixelPaddingValue": -2000 }
    },
    "provenance": { "version": "1.1", "sources": [ { "format": "DICOM" } ],
      "processing": [ { "name": "convert DICOM", "software": { "name": "duckn", "version": "0.7.0" } } ] }
  }
}
```
