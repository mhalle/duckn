# NRRD Extension for duckn

**Extension name:** `nrrd`
**Version:** 0.1 (unstable: any 0.x may change incompatibly)
**Status:** Draft. Written 2026-09-26. `convert.py` writes and reads it.

---

## 1. Purpose

A NRRD header has fields the convention deliberately does not model (duckn-spec §6), because
something else states the same fact about the array: `sizes` is the Zarr shape, `spacings` is
the length of a `space_direction`. The exclusion assumed that something else is always there,
and it is not. A NRRD with no `space` has no `space_direction`, so its `spacings`, `axis mins`
and `axis maxs` are its only geometry; `old min` / `old max` record a quantization the file
does not otherwise state; `content` is the only description some files carry. Through duckn
0.5.4 the converter dropped all of them without a word.

This extension keeps them, as numbers, so that NRRD -> Zarr -> NRRD loses nothing and a
reader can see what the source said. It is **about the source NRRD** (duckn-spec §4.7): it
describes the array only while the array is a faithful re-encoding of that file, and a tool
that derives an array (duckn-spec §4.5) drops it.

It is not `keyvalues`, which holds only `key:=value` strings and may not hold a NRRD field
([keyvalues-extension](keyvalues-extension.md), section 2.3).

## 2. Fields

Top level (`extensions.nrrd`):

| Field | NRRD field | Type |
|---|---|---|
| `version` | - | `"0.1"`, required |
| `content` | `content` | string |
| `min`, `max` | `min`, `max` | number |
| `old_min`, `old_max` | `old min`, `old max` | number |

Per axis (`axes[i].extensions.nrrd`, declared by the top-level entry):

| Field | NRRD field | Type |
|---|---|---|
| `spacing` | `spacings` | number |
| `axis_min` | `axis mins` | number |
| `axis_max` | `axis maxs` | number |

A value NRRD writes as `NaN` (unknown for that axis) is omitted. Per-axis values are on the
axis they describe, so they follow the convention's axis order (slowest first), not NRRD's.

## 3. Semantics

- **No value mapping is inferred.** `old_min` / `old_max` are recorded as the file stated
  them. NRRD leaves the mapping they imply implicit in the storage type's range and most
  readers do not apply it; the converter writes no `value_transforms` from them.
- **No world embedding is inferred.** `spacing` and `axis_min` on an axis without a
  `space_direction` are coordinates along that axis only; they are not turned into a `space`.
- **A convention field wins.** Where the convention states the same fact (an axis with a
  `space_direction` has a spacing), a reader takes the convention's, and a writer does not
  add the extension's value to contradict it.

## 4. NRRD Encoding

Import writes each field present in the header; export writes each field present here,
`NaN` for an axis that has none. Per-axis NRRD `units` are not part of this extension: they
map to each axis's `unit` (the convention's field), and export writes them for the axes that
have no `space_direction` (an axis with one takes its unit from `space units`).

A field the converter neither models nor keeps (`number`, `block size`) is reported
(`nrrd-field-dropped`), never dropped silently. Storage fields (`type`, `encoding`, `endian`,
`data file`, `line skip`, `byte skip`) describe the file, not the array, and Zarr's metadata
replaces them.

## 5. Earlier stores

The zero-copy converter of 0.5.4 and earlier wrote `extensions.legacy = {nrrd_type,
encoding}` - no `version`, under a name no specification owns. Nothing reads it now: a
zero-copy export takes the encoding from the array's own codecs. A reader ignores it, as it
does any unknown extension.
