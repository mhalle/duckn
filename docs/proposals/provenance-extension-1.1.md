# Provenance Extension for duckn, 1.1

**Extension name:** `provenance`
**Version:** 1.1 (a minor version: it only adds, and a 1.0 reader reads a 1.1 block as 1.0,
missing the additions and misreading nothing, duckn 2.0 §2.3)
**For:** duckn convention 2.0 (draft, `docs/proposals/duckn-2.0.md`) and 1.x alike.
**Status:** Draft, 2026-09-30. Written by `duckn.convention2_write` (experimental).

This document states what 1.1 adds to provenance 1.0 (`docs/provenance-extension.md`). Everything
it does not mention is as 1.0 states it, section for section.

---

## 1. What 1.1 adds

### 1.1 A writer records itself

In a convention 2.0 file every writer adds one step to `processing` naming itself, with at least
`name` and `software` (`name` and `version`), as duckn 2.0 §9 requires; a converter also records
what it converted as a `sources` entry with at least its `format` and, where it knows one, an
identifying field (`path`, `url`, `doi`, `identifier`; 1.0 §7). A tool that writes an array from
one that has a `provenance` block keeps its `sources` and `processing` - the input's lineage is
the new array's - and appends its own step. This is conformance, not validity: a file without it
is still read.

It is how a reader recognizes a file from a writer later found defective. duckn's 1.x converters
recorded nothing about themselves, so the four converter defects duckn 0.6.1 fixed, and the two
0.6.3 fixed, cannot be told from correct files; 2.0 files can.

### 1.2 `previous`: a step that takes the previous output and a source

1.0 §5.1: a step's `inputs` indexes `sources`; an absent `inputs` means the step operates on the
previous step's output (or, for the first step, on all sources). Nothing let a step say both - a
registration of the converted image (the previous output) to an atlas (a new source).

| Field | Type | Meaning |
|---|---|---|
| `previous` | boolean, default `false` | The step also takes the previous step's output, beside the sources its `inputs` index. With `inputs` absent it says nothing new (1.0's rule already takes the previous output). |

It is a field, not a value inside `inputs`, so that a 1.0 reader, which does not know it, reads
`inputs` as the integers 1.0 defines and misses only the addition.

## 2. Example

A CT converted from DICOM, then registered to an atlas:

```json
"provenance": {
  "version": "1.1",
  "sources": [
    { "type": "file", "format": "DICOM", "identifier": "1.2.826.0.1.3680043.8.498.10033451" },
    { "type": "file", "format": "NIfTI", "path": "mni_icbm152_t1_tal_nlin_asym_09c.nii",
      "description": "MNI152 2009c asymmetric template" }
  ],
  "processing": [
    { "name": "convert DICOM", "software": { "name": "duckn", "version": "0.7.0" }, "inputs": [0] },
    { "name": "affine registration to the template", "software": { "name": "ANTs", "version": "2.5.0" },
      "inputs": [1], "previous": true }
  ]
}
```

The first step names its source: without `inputs`, 1.0's rule would have it take every source,
the template included. The second took the first step's output (`previous`) and the template
(`inputs: [1]`). A writer appending a step to a file whose first step has no `inputs` and one
source adds `inputs: [0]` to that step when it appends a source, so that its meaning does not
change.
