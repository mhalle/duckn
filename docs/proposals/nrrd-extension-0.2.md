# NRRD Extension for duckn, 0.2

**Extension name:** `nrrd`
**Version:** 0.2 (unstable: any 0.x may change incompatibly, and a reader reads a 0.x version only
as itself, duckn 2.0 §2.3)
**For:** duckn convention 2.0 (draft, `docs/proposals/duckn-2.0.md`). 1.x files keep 0.1
(`docs/nrrd-extension.md`).
**Status:** Draft, 2026-09-30. Written by `duckn.convention2_write` (`nrrd_to_zarr(convention="2.0")`,
experimental); the scenario files S20 and S24 convert to the review's reference headers.

---

## 1. Purpose

As 0.1: the source NRRD's fields that the convention does not state are kept, as numbers, so that
NRRD -> Zarr -> NRRD loses nothing and a reader can see what the source said. The block is a
**source record** (duckn 2.0 §2.3): it describes the array only while the array is a faithful
re-encoding of that file, and a tool that derives an array drops it (duckn 2.0 §8).

What changes from 0.1, all from the convention's §15.1 and §17:

- **A no-space file has a world.** 0.1 kept a no-space file's `spacings`, `axis mins` and
  `axis maxs` per axis and inferred no world. In 2.0 they are the world's axes (§3), stated by
  the core; this block keeps only that the source had no `space` (`no_space`), so that an export
  writes `spacings` and `axis mins` again rather than `space directions`.
- **What the core states is not repeated** (duckn 2.0 §2.3): 0.1's per-axis `spacing`,
  `axis_min` and `axis_max` are gone.
- **Range-axis units and a scalar file's measurement frame** have no core field and are kept here.

## 2. Fields

Top level (`extensions.nrrd`):

| Field | NRRD field | Type |
|---|---|---|
| `version` | - | `"0.2"`, required |
| `no_space` | - | `true` when the source had neither `space` nor `space dimension`; absent otherwise |
| `content` | `content` | string |
| `min`, `max` | `min`, `max` | number |
| `old_min`, `old_max` | `old min`, `old max` | number |
| `measurement_frame` | `measurement frame` | a matrix as rows (duckn 2.0 §5.3's form), only when no dimension holds components it could govern and no `dwmri` block takes it for its gradients (dwmri 2.0 §3) |

Per dimension (`dimensions[i].extensions.nrrd`, read under the top-level block, duckn 2.0 §2.3):

| Field | NRRD field | Type |
|---|---|---|
| `unit` | `units` of a range axis (one with a range kind, or no kind and no spacing) | string, as NRRD wrote it |
| `kind` | `kinds`, for a domain kind the core cannot state (a `domain` axis with no spacing: the dimension states nothing) | string |

A value NRRD writes as `NaN` or `???` (unknown for that axis) is left out.

## 3. Semantics

- **The world of a no-space file** is its `spacings` (or `axis mins` and `axis maxs`), `units`
  and `axis mins`, one world axis per such axis, as duckn 2.0 §15.1 states it: its `type` comes
  from the axis's `kinds` entry (`space` -> `space`, `time` -> `time`); a `domain` axis states no
  type (duckn 2.0 §19 item 15). A converter may take its caller's assertion that `domain` axes are
  spatial; it then states `type` `space` and records the assertion in its provenance step
  (`"parameters": {"domain_axes": "space"}`). NRRD's field is `centers`, `centerings` its synonym
  (duckn reads both, 0.6.4); an unstated centering is NRRD's default, `cell`, and is written.
- **A spaced axis in a file with `space`** (a time axis with `spacings` beside `space directions`)
  is a world axis of its own after the spatial ones, typed by its kind (duckn 2.0 §15.1).
- **No value mapping is inferred**, as in 0.1: `old_min` / `old_max` are recorded as the file
  stated them, and no `values.transforms` is derived from them. A converter that writes them
  therefore cannot state the mapping, and leaves `values.transforms` absent (duckn 2.0 §6).
- **The core wins.** A reader never takes geometry or values from this block (duckn 2.0 §2.3).
- **`axis maxs` beside `spacings`** restate the grid: they are left out, and a converter reports
  one that disagrees with the grid (`min + size x spacing` for a cell-centered axis,
  `min + (size - 1) x spacing` for a node-centered one).

## 4. NRRD Encoding

| NRRD field | 2.0 |
|---|---|
| `space`, `space dimension`, `space units`, `space origin`, `space directions`, `measurement frame` (of a components dimension), `kinds` of domain and range axes, `centers`, `thicknesses`, `labels` | the core, by duckn 2.0 §15.1 |
| `spacings`, `units`, `axis mins` of a no-space file | the core's world (§3), and `no_space` here |
| `units` of a range axis, `measurement frame` of a scalar file | here (§2) |
| `content`, `min`, `max`, `old min`, `old max` | here |
| key/value pairs | the `keyvalues` extension |
| `number`, `block size` | reported (`nrrd-field-dropped`), never dropped silently |
| `type`, `encoding`, `endian`, `data file`, `line skip`, `byte skip` | Zarr's own metadata: they describe the file, not the array |

**Export.** `space units` has one entry for every axis of the NRRD `space`, the world's spatial
axes, including one no dimension steps along (NRRD requires it); a non-spatial world axis exports
through `spacings`, `units` and `axis mins`. With `no_space`, export writes each world axis's step as `spacings`, its
unit as `units`, and the lower edge of its first cell (a cell-centered axis) or its first sample
(a node-centered one) as `axis mins`, and writes no `space`. A dimension whose world axis is of
type `time` exports with `kinds` `time`, one of type `space` with `space` - or `domain` where the
provenance step records the caller's assertion (§3), which the source's kind was - and any other
with `domain`.

## 5. From 0.1

A 1.x file's 0.1 block is read as 0.1 (its per-axis geometry places no world: 0.1 §3). Converting
such a file to 2.0 is not a rewrite (duckn 2.0 §8): a 2.0 writer builds the world from the 0.1
block's `spacing` and `axis_min` exactly as from a no-space NRRD (§3), and writes 0.2.

## 6. Example

The review's S20: a 2D NRRD of `sizes: 200 100`, `spacings: 0.25 0.5`, `units: "mm" "mm"`,
`axis mins: -5 10`, `centers: cell cell`, `kinds: domain domain`, no `space`.

```json
{
  "version": "2.0",
  "world": { "axes": [ { "id": "x", "unit": "mm" }, { "id": "y", "unit": "mm" } ] },
  "origin": [-4.875, 10.25],
  "dimensions": [
    { "step": [0, 0.5], "centering": "cell" },
    { "step": [0.25, 0], "centering": "cell" }
  ],
  "values": { "transforms": [] },
  "extensions": {
    "nrrd": { "version": "0.2", "no_space": true },
    "provenance": { "version": "1.1", "sources": [ { "format": "NRRD" } ],
      "processing": [ { "name": "convert NRRD", "software": { "name": "duckn", "version": "0.7.0" } } ] }
  }
}
```
