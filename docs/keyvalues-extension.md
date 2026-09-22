# Key/Value Extension for duckn

**Extension name:** `keyvalues`
**Version:** 1.0 (proposed)
**Status:** Draft. Written 2026-09-22. The reader and writer in `convert.py` already produce an
unversioned form of this object (§6); this document gives it a version, a shape that cannot
collide with its own fields, and rules.

---

## 1. Purpose

This document defines the `keyvalues` extension for the duckn convention: **NRRD header
key/value pairs (`key:=value` lines) that no other part of the convention or any registered
extension claims**, kept so that NRRD → Zarr → NRRD loses nothing. It round-trips with NRRD
losslessly.

It is a compatibility store, and nothing more:

1. **Preservation.** A NRRD file may carry key/value pairs no extension claims. A converter must
   keep them somewhere, or a round trip loses them. The released converter already does this,
   without a specification.
2. **Interoperability with NRRD tools.** 3D Slicer, teem and pynrrd show and keep key/value
   pairs, so what was in the source header is there again after export.

It is deliberately weak. Values are strings, as they are in NRRD, and carry no types, no units
and no structure; a reader owes them nothing beyond keeping them. **It is not the place for new
metadata.** A project or tool with metadata of its own writes it as an extension - typed JSON
under a name of its own, unregistered and at a 0.x version until it settles (§3.1 of the
convention, "Unregistered extensions"). Anything a reader must *act on* belongs in the
convention or in an extension, never here.

---

## 2. Extension Fields

```json
{
  "duckn": {
    "version": "1.1",
    "extensions": {
      "keyvalues": {
        "version": "1.0",
        "entries": {
          "Scanner": "unit-3",
          "study_note": "rescanned after motion",
          "support_offset": "6.6 6.3 7.6"
        }
      }
    }
  }
}
```

### 2.1 Top-Level Fields

#### `version`

Required. The version of this extension specification, as a string (`"1.0"`).

#### `entries`

Required. A JSON object whose members are the key/value pairs. Every value is a **string**. An
object with no entries is not written: omit the extension instead.

The pairs are held under `entries`, not beside `version`, so that a key named `version` (NRRD
permits one) cannot collide with the extension's own field.

### 2.2 What Belongs Here

- Pairs read from a NRRD header that no extension claimed (§4).
- Pairs a writer adds only so that they appear in a NRRD export, for a NRRD consumer that
  expects them.

A writer does not put its own metadata here to avoid defining an extension. Such metadata is
an unregistered extension (§1).

### 2.3 Keys

- A key is a non-empty string. It must not contain the two-character sequence `:=`, a newline
  (`\n`) or a carriage return (`\r`), and must not begin or end with whitespace. These are the
  conditions under which it survives as a NRRD `key:=value` line.
- A key must not equal a NRRD field identifier (`sizes`, `space directions`, `kinds`, ... - the
  complete list is the NRRD format's field set; `convert.py` holds it as `_NRRD_SPEC_FIELDS`).
  A NRRD reader would take such a line as a field, not a key/value.
- A key must not match the NRRD encoding of a **registered extension** (for example
  `SegmentN_*` and `Segmentation.*` for `seg`, `DWMRI_*` for `dwmri`, `prov_*` for
  `provenance`). On NRRD import, extensions claim their keys first and only the remainder lands
  in `keyvalues` (§4); a writer that puts such a key here produces a file that does not
  round-trip - the next import hands it to the extension.
- Keys are case-sensitive and unordered. No meaning may be given to the order of `entries`.

### 2.4 Values

- A value is a string, possibly empty. It must not contain a newline or carriage return.
- **No type is recorded, and none may be inferred.** `"1"` and `"1.0"` are different strings,
  and a round trip must preserve which was written. A reader that needs a number parses it and
  owns the parse.

---

## 3. Semantics

### 3.1 Key/values describe the array they are attached to

Like every other field, a key/value describes **this array**. It does not describe a source,
a series, or a group of arrays.

### 3.2 Derivation drops them

Key/values are metadata a source "happened to have" in the sense of §4.5 of the convention: no
general rule says which of them survive an arbitrary operation, because no reader knows what they
mean. **An operation that derives a new array (§4.5: resampling, cropping, filtering, computing
one array from another) must not copy key/values from its source.**

A faithful re-encoding - rechunking, recompression, a change of container, NRRD ↔ Zarr - keeps
them unchanged.

### 3.3 Scope: top level only

This version defines `keyvalues` at the top level of the `"duckn"` object only. NRRD has no
per-axis key/values, and a per-axis form would need an encoding convention (an axis index in the
key) before it could round-trip. A writer must not place `keyvalues` in an axis's `extensions`.

---

## 4. NRRD Encoding

The extension *is* the NRRD encoding: each entry is one line.

```
Scanner:=unit-3
support_offset:=6.6 6.3 7.6
```

- **Import:** every `key:=value` line that is not a NRRD field is offered first to the
  registered extensions' parsers, in the converter's order; what no extension claims becomes an
  entry.
- **Export:** every entry is written as one `key:=value` line, after the lines of the structured
  extensions.
- A NRRD file with two lines for the same key: the reader keeps the last and reports the
  duplicate.

---

## 5. Relationship to Other Metadata

**Extensions, registered or not.** Metadata that someone owns and means belongs in an
extension. When a NRRD key family is given a specification, the new extension's NRRD encoding is
those keys, and a converter that knows it claims them on import (§4) instead of leaving them
here; a converter that does not leaves them here. Both files round-trip. This is how `dwmri`
relates to NA-MIC's `DWMRI_*` keys today.

**`seg.legacy.keyvalues`.** The segmentation extension's `legacy.keyvalues` holds a source
`.seg.nrrd`'s *own* key/value strings verbatim, so that an unmodified segmentation can be written
back byte for byte; it is dropped as soon as the segmentation is modified. It is a replay buffer
for one extension, not general metadata, and is unaffected by this document.

---

## 6. Reading Older Files

The released converter writes the pairs directly as the extension's object - no `version`, no
`entries`:

```json
"keyvalues": { "some_key": "some value" }
```

A reader of this version treats a `keyvalues` object that has neither `version` nor `entries` as
that legacy form: every member is a pair (and every value is converted to a string). A writer of
this version always writes `version` and `entries`. A legacy object with a pair named `version`
or `entries` is read correctly by this rule only when the other member is absent; that case is
reported, and the object is read as legacy.

---

## 7. Design Notes

**Why strings only.** This extension exists to hold NRRD's key/values, and in NRRD every value
is a string. Recording a type would mean inventing one on import - deciding that `"1.0"` is the
number 1 - and a round trip would no longer give back what it was given. Typed metadata has a
better home: an extension, where JSON's own types apply (§1).

**Why derivation drops key/values.** The alternative - carry them unless told otherwise - is the
inheritance §4.5 of the convention rejects. A header key is often true of the acquisition and
false of anything resampled from it, and nothing here says which.

---

## 8. Consistency Rules

1. `version` and `entries` are present; `entries` is a non-empty object of strings.
2. No key is empty, contains `:=`, `\n` or `\r`, or begins or ends with whitespace; no value
   contains `\n` or `\r`.
3. No key equals a NRRD field identifier or matches a registered extension's NRRD encoding.
4. `keyvalues` appears only at the top level of the `"duckn"` object.

A reader that finds a violation reports it and keeps the entry where it can; a writer does not
produce one.
