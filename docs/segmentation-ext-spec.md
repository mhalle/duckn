# Segmentation Extension for duckn

**Extension name:** `seg`
**Version:** 0.6
**Status:** Draft

---

## 1. Purpose

This document defines the `seg` extension for the duckn convention. It replaces the `.seg.nrrd` metadata encoding — where segment properties were flattened into NRRD key/value pairs with `SegmentN_` prefixes and `~^|&`-delimited substructure — with a clean JSON representation.

The data model is the same. What changes is the encoding: structured objects replace string packing.

A secondary goal is to decouple segment identity from any single ontology. The `.seg.nrrd` `TerminologyEntry` tag hardcodes a DICOM Segmentation IOD classification pattern (category/type/region with SNOMED codes) as *the* way to describe what a segment contains. This extension separates two concerns:

- **Designations:** what the segment *is*, expressed in one or more coding systems (SNOMED-CT, FMA, TA2, NCIt, user-defined labels, etc.)
- **DICOM classification:** the category/type/region pattern needed specifically for DICOM SEG round-tripping

A segment can carry multiple designations from different ontologies simultaneously. The DICOM classification structure is available when needed, but is no longer the mandatory backbone of segment identity.

The extension also supports string references in `label_value`, allowing a segment to be defined as the union of other named segments. This enables hierarchical ontologies (such as brain atlas parcellations) to be represented compactly and without redundancy, using the existing `id` field as the reference target.

---

## 2. Data Layout

A segmentation is a Zarr array whose voxel values encode segment membership. For binary labelmaps, these are integer labels; for fractional labelmaps, they are continuous values. Spatial embedding (origin, directions, space) is described by the duckn convention fields as usual.

An array carrying this extension should declare a matching duckn `intent`: `"label-map"` for a binary labelmap, `"probability-map"` for a fractional one. The extension does not require it — `intent` describes the array, `source_representation` describes the segmentation — but a reader that dispatches on `intent` alone should still see the right thing.

Everything in this section describes binary labelmaps. Fractional labelmaps are addressed separately below.

### Non-overlapping segments

The array has 3 spatial dimensions. Each voxel's integer value identifies which segment it belongs to. This is the common case.

**Label value 0 is reserved for background** and must not be claimed by any segment: a duckn array's `fill_value` is typically 0, so a segment claiming label 0 would claim every unwritten voxel.

### Overlapping segments: layers

The array has a `list` axis (kind `"list"`) plus 3 spatial dimensions. Each position along the list axis is a **layer** — a 3D label volume. Segments that would collide in a single volume are assigned to different layers. Multiple segments may share the same label value if they are in different layers.

### Overlapping segments: label unions

An alternative to layers is to decompose the volume into non-overlapping **islands** and define each semantic segment as the union of one or more islands. The array remains a single 3D volume with no extra axis.

For example, a tumor that partially overlaps the liver can be decomposed into three islands — liver-only voxels (label 1), tumor-only voxels (label 2), and the overlap region (label 3). The "Liver" segment is then defined as `"label_value": [1, 3]` and the "Tumor" segment as `"label_value": [2, 3]`. The overlap region (label 3) appears in both segments' label value lists.

Islands are implicit: they exist as label values in the voxel data and do not require their own segment entries. However, an island *may* have an explicit segment entry if metadata is needed for it — for instance, to name the tumor-liver intersection zone or attach designations to it. In that case, its label value will appear both in its own entry and in the composite segments that include it.

Not all label values in the volume need to be described by a segment entry. Undescribed labels are implementation-defined (e.g., treated as "background" or "unknown"); a reader that must map every voxel to a segment should say what it does with them.

The layer and label-union mechanisms are independent and may coexist in the same segmentation. They address overlapping segments through different strategies: layers duplicate the spatial volume; label unions partition it.

### Hierarchical segments: segment references

A segment's `label_value` may contain string entries that reference other segments by their `id`. A segment defined this way is the union of the effective voxel sets of all referenced segments, plus any integer label values it also directly lists.

This allows hierarchical ontologies to be expressed compactly. A parent region is defined by listing its children by `id`; the full set of voxels belonging to the parent is the transitive union of all descendant leaf segments — without materializing that full list of integers in the file. Leaf segments have only integer `label_value` entries; interior nodes reference other segments by `id`.

### Fractional labelmaps

When `source_representation` is `"fractional-labelmap"`, voxel values are continuous — the fraction of the voxel occupied by a segment, or the probability that it belongs to one — and the integer-equality rule above does not apply.

Each segment therefore needs its own volume of fractional values, so a fractional segmentation **must** carry a `list` axis and assign every segment a distinct `layer`. `label_value` still identifies the segment within its layer and is conventionally `1`, but it selects a layer's worth of fractional values rather than matching voxels by equality.

The island and segment-reference mechanisms (label unions, hierarchical references) are defined only for binary labelmaps: both are set operations over integer labels, and neither has a defined meaning over fractional values. A fractional segmentation must give each segment a single integer `label_value`.

The value range is not constrained here — `[0, 1]` is typical, but an application storing 0–255 or 0–100 should record the scaling with the duckn convention's `value_transforms` rather than inventing a convention in this extension.

### Empty segmentation

Unlike `.seg.nrrd`, a Zarr store does not require non-empty data. An empty segmentation can be represented as a zero-extent array or by providing only the extension metadata with no voxel data. The single-voxel sentinel hack is not needed.

`segments` is still required, but it may be an empty array for a segmentation that describes nothing. This is the one exception to §4.4's rule against empty collections: `segments` is a structural field rather than an optional one, and omitting it entirely would be indistinguishable from a malformed file.

---

## 3. Extension Fields

The `seg` extension is declared at the top level of the `"duckn"` object's `"extensions"` and carries the array-wide segmentation metadata. Per-segment metadata lives in a `"segments"` array within this object.

### 3.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification.

```json
"version": "0.6"
```

**Version semantics.** While the major version is `0`, the *minor* version may introduce breaking changes; this overrides the duckn convention's default rule that minor increments are additive. From 1.0 onward, major increments signal breaking changes and minor increments are additive.

Version 0.6 is a breaking change from 0.5: the `identifiers` field was removed in favor of `designations`, `dicom` moved from `metadata.dicom` to a first-class segment field, and 3D Slicer application state moved under `metadata.slicer`. A reader for 0.5 must not assume it can read 0.6, and a reader for 0.6 that wants to accept older files should migrate those three shapes on load.

#### `source_representation`

Optional. The representation type stored in this file.

| Value | Description |
|-------|-------------|
| `"binary-labelmap"` | Integer labels, one value per segment per layer |
| `"fractional-labelmap"` | Continuous values representing partial volume or probability |
| `"closed-surface"` | Surface meshes (not stored in this array) |
| `"planar-contour"` | Planar contours (not stored in this array) |

The first two describe the voxel data in the array itself. The last two exist because a `.seg.nrrd` may name a non-labelmap master representation; a file may declare one so that the rest of its segment metadata survives conversion, but the mesh or contour geometry itself is out of scope for this extension. Other values may appear — readers should not fail on an unrecognized one.

```json
"source_representation": "binary-labelmap"
```

#### `metadata`

An open-ended object for application-specific metadata. Each key identifies the application or pipeline that produced the metadata. The spec does not define the contents — applications are free to store whatever they need.

Well-known keys:

- **`slicer`** — 3D Slicer application state:
  - `contained_representations`: array of representation names the application should be prepared to generate (e.g., `["binary-labelmap", "closed-surface"]`)
  - `conversion_parameters`: mesh generation parameters (smoothing factor, decimation factor, etc.), replacing the `&`-and-`|`-delimited `Segmentation_ConversionParameters` string from `.seg.nrrd`
  - `reference_extent_offset`: a 3-element array `[i, j, k]` giving the voxel-coordinate offset of this array's origin relative to a reference image grid. Allows reconstructing the original image extent when the segmentation covers only a subregion.

```json
"metadata": {
  "slicer": {
    "contained_representations": ["binary-labelmap", "closed-surface"],
    "conversion_parameters": {
      "Smoothing factor": {
        "value": "0.5",
        "description": "Fraction of Gaussian standard deviation relative to voxel size"
      }
    },
    "reference_extent_offset": [100, 50, 0]
  },
  "totalsegmentator": {
    "task": "total",
    "model": "3d_fullres",
    "version": "1.5.6"
  }
}
```

#### `terminologies`

An object registering the coding systems used in segment designations. Each key is a short identifier for the system (used in `designations[].scheme`); each value is an object with metadata about that system.

```json
"terminologies": {
  "SCT": {
    "name": "SNOMED Clinical Terms",
    "version": "2024-09-01",
    "url": "https://browser.ihtsdotools.org",
    "url_template": "https://browser.ihtsdotools.org/?perspective=full&conceptId1={code}"
  },
  "FMA": {
    "name": "Foundational Model of Anatomy",
    "url": "http://purl.org/sig/ont/fma/",
    "url_template": "http://purl.org/sig/ont/fma/fma{code}"
  },
  "TA2": {
    "name": "Terminologia Anatomica 2nd Edition",
    "url": "https://ta2viewer.openanatomy.org"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | no | Full human-readable name of the coding system |
| `version` | no | Version of the coding system in use |
| `url` | no | URL for the coding system's browser, specification, or landing page |
| `url_template` | no | Template for concept URLs. The substring `{code}` is replaced with a designation's `code` to produce a resolvable link for that concept |

This field serves two purposes: it provides provenance for every coded entry in the file, and it gives readers (human or machine) a resolvable pointer to the source of truth for each coding system.

Every coding system used in a segment's `designations` or `dicom` entries **should** be registered here, and a writer that knows the system should register it. Registration is a recommendation, not a requirement: a `scheme` with no registry entry does not make the file invalid, it only leaves the reader with less context. Readers must therefore not reject an unregistered `scheme`, and must not assume the registry enumerates every scheme in the file.

#### `segments`

Required. An array of segment objects. Each describes one semantic region in the segmentation. The array index is the segment's ordinal position — it replaces the `N` in `SegmentN_*`. May be empty for an empty segmentation (§2).

#### `legacy`

Optional. Verbatim source metadata preserved so a file converted *from* another format can be converted back to it without loss of formatting.

For `.seg.nrrd` sources this holds the original key/value strings under a `keyvalues` object. A writer that has not modified the segmentation may replay these strings byte-for-byte; a writer that has modified it must regenerate from the model and should drop or refresh the stale entries.

```json
"legacy": {
  "keyvalues": {
    "Segment0_ID": "Segment_1",
    "Segment0_Tags": "Segmentation.Status:inprogress|"
  }
}
```

This field carries no semantics of its own: readers that are not round-tripping to the source format should ignore it. It is the same mechanism the NIfTI extension uses for its own provenance (see `nifti-spec.md` §4.3).

---

### 3.2 Segment Object Fields

Each element of the `segments` array is a JSON object with the following fields. All fields are optional except `id` and `label_value`.

#### `id`

A stable, unique identifier for the segment within this segmentation. Does not change when the segment is renamed. String entries in other segments' `label_value` arrays resolve against this field.

```json
"id": "Segment_1"
```

#### `name`

The human-readable display name. This is the user-facing label, independent of any ontology. It may be user-authored, auto-generated from terminology, or a local nickname for a structure.

```json
"name": "Right kidney"
```

#### `display`

An optional object providing the segment's display name in additional languages, keyed by BCP 47 language tags. `name` is the default fallback; `display` provides translations.

```json
"name": "Right kidney",
"display": {
  "en": "Right kidney",
  "la": "Ren dexter",
  "de": "Rechte Niere",
  "ja": "右腎"
}
```

When `display` is present, the value of `name` should also appear under the appropriate language key so that `display` is self-contained. A reader using `display` can ignore `name`; a reader not using `display` can ignore it.

#### `color`

Display color as an RGB array with values in [0.0, 1.0].

```json
"color": [0.89, 0.85, 0.78]
```

#### `label_value`

The label value or values identifying the voxels belonging to this segment. Required.

Each element is either an **integer** or a **string**:

- An **integer** is a literal voxel label value. The segment includes all voxels in its layer whose value equals this integer.
- A **string** is a reference to another segment by its `id`. The segment includes the full effective voxel set of the referenced segment, resolved recursively.

A scalar value follows the same rule: a bare integer is a literal; a bare string is a reference.

```jsonc
"label_value": 1                          // leaf: single literal voxel value
"label_value": [1, 3, 7]                  // leaf: literal label union (island model)
"label_value": ["child-a", "child-b"]     // interior node: references only
"label_value": [1, "child-a", "child-b"]  // mixed: own voxels plus referenced children
```

Integer entries must not be 0, which is reserved for background (§2).

The **effective voxel set** of a segment is the union of:
- all voxels whose label value matches any integer in `label_value`, and
- the effective voxel sets of all segments referenced by string entries in `label_value`.

Because a label value only identifies voxels within a layer, an element of an effective voxel set is a *(layer, label value)* pair, not a bare integer. Integers contributed directly by a segment take that segment's own `layer`; those contributed through a reference take the referenced segment's layer (§3.2 `layer`).

Resolution is recursive and terminates at leaf segments (those with no string entries). Circular references are forbidden.

A reader that does not support string references may still process segments whose `label_value` contains only integers. Segments with string entries are opaque to such a reader, but their presence does not invalidate the file.

#### `layer`

The zero-based index of the layer (position along the `list` axis) that contains this segment's literal voxel values. Omit for non-overlapping segmentations where there is only one layer; an absent `layer` means layer 0.

The `layer` field applies only to integer label values within this segment. Referenced segments have their own `layer` assignments, which are respected during recursive resolution. A segment whose `label_value` holds only references owns no voxels directly, so its own `layer` is meaningless and should be omitted.

When an array has more than one `list`-kind axis, `layer` indexes the first one.

```json
"layer": 0
```

#### `extent`

The bounding box of the non-empty region within the segment, as a 6-element array: `[min_i, max_i, min_j, max_j, min_k, max_k]` in voxel coordinates. For segments defined by references, `extent` describes the bounding box of the full effective voxel set, not only any directly-owned integer label values.

Both bounds are **inclusive** — a single-voxel segment at the origin has extent `[0, 0, 0, 0, 0, 0]` — matching the `.seg.nrrd` convention this field is converted from. `i`, `j`, `k` are the array's three spatial axes in storage order, whatever they are named in `dimension_names`; a non-spatial axis such as `list` is not counted.

```json
"extent": [45, 102, 30, 98, 12, 55]
```

#### `designations`

An array of coded references identifying what this segment represents in external coding systems. Each entry says "this segment is concept *X* in system *Y*". The first entry is the preferred identification. See §4.1 for full semantics.

```json
"designations": [
  {
    "scheme": "SCT",
    "code": "64033007",
    "meaning": "Kidney",
    "modifier": {"scheme": "SCT", "code": "24028007", "meaning": "Right"}
  },
  {"scheme": "FMA", "code": "7205", "meaning": "Right kidney"}
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `scheme` | yes | Key identifying the coding system; should match an entry in the top-level `terminologies` registry (§3.1) |
| `code` | yes | The concept identifier within that coding system |
| `meaning` | no | Human-readable name of the concept, as of the registered terminology version. Recommended when known |
| `modifier` | no | A designation object qualifying this one (typically laterality). One level of nesting; modifiers do not carry their own modifiers |

The `scheme` + `code` pair is the segment's identity in that system; `meaning` is a convenience rendering. If an embedded `meaning` disagrees with what the coding system says the code means, **the code wins** (§4.1).

#### `dicom`

The DICOM Segmentation IOD classification structure (category, type, type modifier, anatomic region, region modifier). See §4.2. Present only when DICOM SEG interoperability is needed.

```json
"dicom": {
  "category": {"scheme": "SCT", "code": "123037004", "meaning": "Body structure"},
  "type": {"scheme": "SCT", "code": "64033007", "meaning": "Kidney"},
  "anatomic_region": {"scheme": "SCT", "code": "64033007", "meaning": "Kidney"},
  "anatomic_region_modifier": {"scheme": "SCT", "code": "24028007", "meaning": "Right"}
}
```

#### `metadata`

An open-ended object for application-specific per-segment metadata, following the same pattern as the extension-level `metadata` field. Each key identifies the source application or standard.

Well-known keys:

- **`slicer`** — 3D Slicer per-segment state:
  - `name_auto_generated`: boolean, `true` if the name was derived from terminology
  - `color_auto_generated`: boolean, `true` if the color was auto-assigned
  - `tags`: arbitrary key/value pairs from Slicer's internal tagging system

```json
"metadata": {
  "slicer": {
    "name_auto_generated": true,
    "color_auto_generated": false,
    "tags": {"Status": "reviewed"}
  }
}
```

---

## 4. Segment Identity

Segment identity has two parts:

1. **Designations** — an array of coded references saying "this segment is concept *X* in system *Y*". This is the primary mechanism for interoperable segment identity.

2. **DICOM classification** — the structured category/type/modifier/region hierarchy required by DICOM Segmentation IOD. Lives in the `dicom` field on the segment. Needed only for DICOM round-tripping.

### 4.1 Designations

The `designations` array lists coded concept references. A segment is a real anatomical or pathological entity that different communities identify using different coding systems — a kidney is SNOMED 64033007, FMA 7203, and TA2 5765 simultaneously. Each array entry captures one such identification. The first entry is the preferred one.

```json
"designations": [
  {
    "scheme": "SCT",
    "code": "64033007",
    "meaning": "Kidney",
    "modifier": {"scheme": "SCT", "code": "24028007", "meaning": "Right"}
  },
  {"scheme": "FMA", "code": "7205", "meaning": "Right kidney"}
]
```

#### Codes are authoritative; meanings are renderings

The identity carried by a designation is `scheme` + `code` (+ `modifier`, when present). The coding system itself — at the version recorded in the `terminologies` registry — is the source of truth for what that code means.

`meaning` is a snapshot rendering embedded for the reader's convenience: it lets a viewer display something sensible without a terminology service (SNOMED requires a license in many countries; FMA and TA2 lookups require network access), it feeds DICOM export (see §4.2), and it acts as a human-auditable cross-check. But it is not authoritative: **if the embedded `meaning` disagrees with the coding system, the code wins.** Writers should include `meaning` when they know it; readers must not treat it as identity.

Concept URLs are not embedded per-entry. When the registry entry for a scheme provides a `url_template`, readers derive the concept URL by substituting the designation's `code`.

#### Post-coordination via `modifier`

Many coding systems express concepts like "right kidney" as a base concept plus a qualifier rather than a single pre-coordinated code. The `modifier` field carries that qualifier as a nested designation. This mirrors DICOM's modifier code sequences, so laterality survives round-tripping even when no pre-coordinated concept exists. Modifiers nest one level only.

#### Relationship to `name`

The `name` field on the segment is the user-facing display label. It may echo a designation's `meaning`, or it may be entirely different ("Bob's left kidney"). The two are independent: `name` is what you show in the UI; `designations` are what you use for computation, interoperability, and lookup.

### 4.2 DICOM Classification

The DICOM classification structure lives in the `dicom` field on the segment. It provides the specific category/type/modifier/region hierarchy defined by the DICOM Segmentation IOD — the information needed to write a DICOM SEG object.

```json
"dicom": {
  "category": {"scheme": "SCT", "code": "49755003", "meaning": "Morphologically abnormal structure"},
  "type": {"scheme": "SCT", "code": "4147007", "meaning": "Mass"},
  "anatomic_region": {"scheme": "SCT", "code": "23451007", "meaning": "Adrenal gland"},
  "anatomic_region_modifier": {"scheme": "SCT", "code": "24028007", "meaning": "Right"}
}
```

Each entry is a coded entry with the same shape as a designation (minus `modifier`): `scheme` and `code` required, `meaning` optional but recommended.

Every field of `dicom` is itself optional, so that a partial classification can be recorded as it becomes known. Writing a conformant DICOM SEG object additionally requires `category` and `type` (Type 1 in the IOD), so a writer that finds either missing must fail rather than invent one.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `category` | for DICOM SEG export | coded entry | Segmentation Category (e.g., "Morphologically abnormal structure", "Tissue") |
| `type` | for DICOM SEG export | coded entry | Segmentation Type within the category (e.g., "Mass", "Neoplasm") |
| `type_modifier` | no | coded entry | Qualifier on the type. Omit if not applicable. |
| `anatomic_region` | no | coded entry | Anatomic region |
| `anatomic_region_modifier` | no | coded entry | Qualifier on the region (typically laterality). Omit if not applicable. |

DICOM classification entries use SNOMED CT codes (`"scheme": "SCT"`) by convention, but the scheme is always explicit — legacy files may carry SRT or DCM codes.

When writing a DICOM SEG object, CodeMeaning is required. Writers derive it from the coded entry's `meaning`, falling back to the segment's `name`, and fail if neither is available.

The `.seg.nrrd` `TerminologyEntry` also stored "context name" strings naming 3D Slicer's terminology selector lookup tables. These are application state, not data semantics, and belong in `metadata.slicer` if needed.

### 4.3 Relationship Between Designations and DICOM

The `designations` and `dicom` fields are independent. The same SNOMED concept might appear in both — once as a flat designation, once within the structured classification. They serve different purposes:

- `designations` answers: "What is this structure, in any ontology?"
- `dicom` answers: "How should this segment be classified in a DICOM Segmentation IOD?"

When converting to DICOM SEG, a writer uses `dicom`. When performing ontology-based lookup or cross-referencing, a reader uses `designations`.

### 4.4 Absence and Omission

Following the duckn specification's "absent means unknown" principle:

- If a segment has no designations, omit the `designations` field. Do not include an empty array.
- If a designation's `meaning` is unknown, omit it. Do not use an empty string or `null`.
- If a segment has no DICOM classification, omit the `dicom` field. Do not include an empty object.
- Within `dicom`, omit `type_modifier` and `anatomic_region_modifier` when they do not apply. Do not use `null`.
- If no terminology registrations are needed, omit the `terminologies` field entirely.

---

## 5. Consistency Rules

Throughout this section, a segment's **effective voxel set** is the set of *(layer, label value)* pairs defined in §3.2, and the *scalar-vs-array* spelling of `label_value` carries no meaning: `1`, `[1]`, and a reference resolving to the same single pair are all the same set.

**Structure**

1. The length of `segments` is independent of any axis size — multiple segments can share a label value across layers, and multiple segments can exist in the same layer with different label values.
2. Where a segment specifies a `layer`, there must be a `list`-kind axis in the array, and the `layer` value must be a valid index into that axis. It follows that a segment in an array with no `list` axis must omit `layer` entirely rather than specifying 0 — the two are equivalent (§3.2), but only the omission is representable.
3. Where a `kind` constraint requires a specific axis size (from the duckn convention), the corresponding `shape` element must match.
4. A fractional segmentation (`"source_representation": "fractional-labelmap"`) must have a `list` axis, and each of its segments must specify a distinct `layer` and a single integer `label_value` (§2).

**Identity**

5. `id` must be unique across all segments in the segmentation.
6. `scheme` values used in `designations` or `dicom` coded entries should have a corresponding key in the top-level `terminologies` registry. This is a recommendation, not a requirement (§3.1).

**Label values**

7. Integer entries in `label_value` must not be 0, which is reserved for background (§2).
8. No two segments may have the same effective voxel set. Comparing whole sets — rather than individual values — is what allows an island to be shared: a given *(layer, label)* pair may appear in the effective sets of any number of segments, which is the mechanism for representing overlapping structures (§2). What must be unique is the *combination* a segment claims, since two segments claiming exactly the same voxels are indistinguishable.
9. Not all integer label values present in the voxel data need to appear in a segment entry. Undescribed label values are implementation-defined (§2).

**References**

10. String entries in `label_value` must match the `id` of a segment within the same `segments` array.
11. The segment reference graph formed by string entries in `label_value` must be acyclic. A segment may not directly or transitively reference itself.

Rules 5–11 constrain the metadata alone and are cheap to check; rules 2–4 additionally require the array's shape and axes. None of them can be verified from the voxel data, which this extension never requires a reader to load — in particular, rule 9 means a validator cannot tell a legal undescribed label from a mistake.

Writers should validate before serializing. Readers should not assume a file is valid: a segmentation that violates rule 10 or 11 has no well-defined effective voxel set, and a reader encountering one should report the error rather than resolving references partially.

---

## 6. Mapping from `.seg.nrrd`

| `.seg.nrrd` field | duckn `seg` extension field |
|---|---|
| `Segmentation_MasterRepresentation` / `Segmentation_SourceRepresentation` | `source_representation` |
| `Segmentation_ContainedRepresentationNames` | `metadata.slicer.contained_representations` (array) |
| `Segmentation_ConversionParameters` | `metadata.slicer.conversion_parameters` (object) |
| `Segmentation_ReferenceImageExtentOffset` | `metadata.slicer.reference_extent_offset` (array) |
| `SegmentN_ID` | `segments[n].id` |
| `SegmentN_Name` | `segments[n].name` |
| — (no `.seg.nrrd` equivalent) | `segments[n].display` (multilingual names) |
| `SegmentN_NameAutoGenerated` | `segments[n].metadata.slicer.name_auto_generated` (boolean) |
| `SegmentN_Color` | `segments[n].color` (RGB array) |
| `SegmentN_ColorAutoGenerated` | `segments[n].metadata.slicer.color_auto_generated` (boolean) |
| `SegmentN_LabelValue` | `segments[n].label_value` (integer, string, or mixed array) |
| `SegmentN_Layer` | `segments[n].layer` (integer; omitted when 0 — see Parsing Notes) |
| `SegmentN_Extent` | `segments[n].extent` (6-element array) |
| `SegmentN_Tags` (minus TerminologyEntry) | `segments[n].metadata.slicer.tags` (object) |
| `SegmentN_Tags` TerminologyEntry — category/type/modifier/region | `segments[n].dicom` (object) |
| `SegmentN_Tags` TerminologyEntry — type code + type modifier | `segments[n].designations` (first entry, modifier included) |
| `SegmentN_Tags` TerminologyEntry — context names | Omitted (application state) |
| — (no `.seg.nrrd` equivalent) | `segments[n].label_value` string entries (segment references) |

### Parsing Notes

Implementers converting `.seg.nrrd` files should be aware of the following encoding differences:

- **Master vs Source representation**: 3D Slicer renamed `Segmentation_MasterRepresentation` to `Segmentation_SourceRepresentation` around version 5.3. Converters should accept either key.
- **Representation names**: `.seg.nrrd` uses title-case names (e.g., `"Binary labelmap"`, `"Closed surface"`). Normalize to kebab-case (`"binary-labelmap"`, `"closed-surface"`).
- **Pipe-delimited lists**: `Segmentation_ContainedRepresentationNames` and `Segmentation_ConversionParameters` use `|` and `&` as delimiters, often with trailing separators. Split on the delimiter and drop empty elements.
- **Tags string**: `SegmentN_Tags` is a `|`-delimited sequence of `key:value` pairs. Strip the `Segmentation.` prefix from tag keys (e.g., `Segmentation.Status` → `Status`). The `TerminologyEntry` key is parsed into the `dicom` and `designations` fields per the mapping table above.
- **Escaped newlines in descriptions**: `ConversionParameters` description strings may contain literal `\n` escape sequences representing newlines.
- **Layer 0**: `.seg.nrrd` writes `SegmentN_Layer` on every segment, including single-layer files. Drop it when it is 0 — absent means layer 0 (§3.2), and carrying it explicitly would claim a `list` axis that a 3D file does not have (§5 rule 2).
- **Missing `LabelValue`**: a segment with no binary labelmap representation may have no `SegmentN_LabelValue`. Do not default it to 0, which is reserved for background; assign an unused label value instead.
- **Two-part coded entries**: a `TerminologyEntry` triplet may carry an empty or absent meaning (`SCT^64033007^` or `SCT^64033007`). Keep the code — `meaning` is optional (§4.1).

---

## 7. Examples

### 7.1 Non-Overlapping Labelmap with Multi-Ontology Designations

A 256×256×128 binary labelmap segmentation with two segments in LPS space. Each segment carries designations from multiple coding systems, and the DICOM classification needed for SEG export:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [256, 256, 128],
  "data_type": "uint8",
  "dimension_names": ["i", "j", "k"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 32] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "left-posterior-superior",
      "space_origin": [-127.5, -127.5, 0.0],
      "intent": "label-map",
      "axes": [
        { "kind": "space", "centering": "cell", "space_direction": [1, 0, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 1, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 0, 2], "unit": "mm" }
      ],
      "extensions": {
        "seg": {
          "version": "0.6",
          "source_representation": "binary-labelmap",
          "terminologies": {
            "SCT": {
              "name": "SNOMED Clinical Terms",
              "version": "2024-09-01",
              "url": "https://browser.ihtsdotools.org",
              "url_template": "https://browser.ihtsdotools.org/?perspective=full&conceptId1={code}"
            },
            "FMA": {
              "name": "Foundational Model of Anatomy",
              "url": "http://purl.org/sig/ont/fma/",
              "url_template": "http://purl.org/sig/ont/fma/fma{code}"
            },
            "TA2": { "name": "Terminologia Anatomica 2nd Edition", "url": "https://ta2viewer.openanatomy.org" }
          },
          "segments": [
            {
              "id": "Segment_1",
              "name": "Right kidney",
              "label_value": 1,
              "color": [0.89, 0.85, 0.78],
              "extent": [90, 170, 100, 180, 40, 100],
              "designations": [
                {
                  "scheme": "SCT",
                  "code": "64033007",
                  "meaning": "Kidney",
                  "modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" }
                },
                { "scheme": "FMA", "code": "7205", "meaning": "Right kidney" },
                { "scheme": "TA2", "code": "5767", "meaning": "Right kidney" }
              ],
              "dicom": {
                "category": { "scheme": "SCT", "code": "123037004", "meaning": "Body structure" },
                "type": { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" },
                "anatomic_region": { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" },
                "anatomic_region_modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" }
              }
            },
            {
              "id": "Segment_2",
              "name": "Left kidney",
              "label_value": 2,
              "color": [0.90, 0.82, 0.72],
              "extent": [85, 165, 60, 140, 38, 98],
              "designations": [
                {
                  "scheme": "SCT",
                  "code": "64033007",
                  "meaning": "Kidney",
                  "modifier": { "scheme": "SCT", "code": "7771000", "meaning": "Left" }
                },
                { "scheme": "FMA", "code": "7204", "meaning": "Left kidney" },
                { "scheme": "TA2", "code": "5766", "meaning": "Left kidney" }
              ],
              "dicom": {
                "category": { "scheme": "SCT", "code": "123037004", "meaning": "Body structure" },
                "type": { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" },
                "anatomic_region": { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" },
                "anatomic_region_modifier": { "scheme": "SCT", "code": "7771000", "meaning": "Left" }
              }
            }
          ],
          "metadata": {
            "slicer": {
              "contained_representations": ["binary-labelmap", "closed-surface"]
            }
          }
        }
      }
    }
  }
}
```

### 7.2 Overlapping Segments with Layers

A tumor partially overlapping the liver, represented as two segments in separate layers:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [2, 256, 256, 128],
  "data_type": "uint8",
  "dimension_names": ["list", "i", "j", "k"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [1, 64, 64, 32] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "left-posterior-superior",
      "space_origin": [-127.5, -127.5, 0.0],
      "intent": "label-map",
      "axes": [
        { "kind": "list" },
        { "kind": "space", "centering": "cell", "space_direction": [1, 0, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 1, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 0, 2], "unit": "mm" }
      ],
      "extensions": {
        "seg": {
          "version": "0.6",
          "source_representation": "binary-labelmap",
          "segments": [
            {
              "id": "Segment_1",
              "name": "Tumor",
              "label_value": 1,
              "layer": 0,
              "color": [0.8, 0.2, 0.2],
              "designations": [{ "scheme": "SCT", "code": "108369006", "meaning": "Neoplasm" }]
            },
            {
              "id": "Segment_2",
              "name": "Liver",
              "label_value": 1,
              "layer": 1,
              "color": [0.2, 0.6, 0.8],
              "designations": [{ "scheme": "SCT", "code": "10200004", "meaning": "Liver" }]
            }
          ]
        }
      }
    }
  }
}
```

Note that both segments use `label_value: 1` — this is valid because they are in different layers.

### 7.3 Overlapping Segments with Label Unions

The same tumor-liver overlap, represented as label unions in a single 3D volume:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [256, 256, 128],
  "data_type": "uint8",
  "dimension_names": ["i", "j", "k"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 32] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "left-posterior-superior",
      "space_origin": [-127.5, -127.5, 0.0],
      "intent": "label-map",
      "axes": [
        { "kind": "space", "centering": "cell", "space_direction": [1, 0, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 1, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 0, 2], "unit": "mm" }
      ],
      "extensions": {
        "seg": {
          "version": "0.6",
          "source_representation": "binary-labelmap",
          "segments": [
            {
              "id": "Segment_1",
              "name": "Tumor",
              "label_value": [2, 3],
              "color": [0.8, 0.2, 0.2],
              "designations": [{ "scheme": "SCT", "code": "108369006", "meaning": "Neoplasm" }]
            },
            {
              "id": "Segment_2",
              "name": "Liver",
              "label_value": [1, 3],
              "color": [0.2, 0.6, 0.8],
              "designations": [{ "scheme": "SCT", "code": "10200004", "meaning": "Liver" }]
            }
          ]
        }
      }
    }
  }
}
```

Label 1 is liver-only voxels, label 2 is tumor-only voxels, label 3 is the overlap region. The island at label 3 has no explicit segment entry — it exists only as a shared integer in the two composite segments.

### 7.4 Research Segmentation Without DICOM

A segmentation from a research pipeline using only FMA codes, no DICOM classification needed:

```json
"extensions": {
  "seg": {
    "version": "0.6",
    "source_representation": "binary-labelmap",
    "terminologies": {
      "FMA": {
        "name": "Foundational Model of Anatomy",
        "url": "http://purl.org/sig/ont/fma/",
        "url_template": "http://purl.org/sig/ont/fma/fma{code}"
      }
    },
    "segments": [
      {
        "id": "S1",
        "name": "Left ventricle",
        "label_value": 1,
        "designations": [{ "scheme": "FMA", "code": "7101", "meaning": "Left ventricle" }]
      },
      {
        "id": "S2",
        "name": "Right ventricle",
        "label_value": 2,
        "designations": [{ "scheme": "FMA", "code": "7098", "meaning": "Right ventricle" }]
      }
    ]
  }
}
```

### 7.5 Hierarchical Ontology (Allen Mouse Brain CCF)

A whole-brain mouse atlas segmentation where voxel label values are Allen CCF structure IDs. Leaf segments carry integer `label_value`; parent structures are defined by referencing their direct children by `id`. The full hierarchy is expressed compactly — the voxel set of each interior node is the transitive union of its descendants without any redundant integer lists.

*The listing below is an excerpt: most of the referenced child segments are elided for brevity. In a complete file, every `id` referenced from a `label_value` must resolve to a segment in the same array (§5).*

```json
"extensions": {
  "seg": {
    "version": "0.6",
    "source_representation": "binary-labelmap",
    "terminologies": {
      "CCF": {
        "name": "Allen Mouse Brain Common Coordinate Framework",
        "version": "3.0",
        "url": "http://atlas.brain-map.org"
      }
    },
    "segments": [
      {
        "id": "997",
        "name": "root",
        "label_value": ["8", "1009", "73", "1024", "304325711"],
        "color": [1.0, 1.0, 1.0],
        "designations": [{ "scheme": "CCF", "code": "997", "meaning": "root" }]
      },
      {
        "id": "8",
        "name": "Basic cell groups and regions",
        "label_value": ["567", "343", "512"],
        "color": [0.749, 0.855, 0.890],
        "designations": [{ "scheme": "CCF", "code": "8", "meaning": "Basic cell groups and regions" }]
      },
      {
        "id": "315",
        "name": "Isocortex",
        "label_value": ["184", "500", "453", "1057", "677", "247", "669", "31", "972", "44", "714", "95", "254", "22", "541", "922", "895"],
        "color": [0.439, 1.0, 0.443],
        "designations": [{ "scheme": "CCF", "code": "315", "meaning": "Isocortex" }]
      },
      {
        "id": "184",
        "name": "Frontal pole, cerebral cortex",
        "label_value": ["68", "667", "526157192", "526157196", "526322264"],
        "color": [0.149, 0.561, 0.271],
        "designations": [{ "scheme": "CCF", "code": "184", "meaning": "Frontal pole, cerebral cortex" }]
      },
      {
        "id": "68",
        "name": "Frontal pole, layer 1",
        "label_value": 68,
        "color": [0.149, 0.561, 0.271],
        "designations": [{ "scheme": "CCF", "code": "68", "meaning": "Frontal pole, layer 1" }]
      },
      {
        "id": "667",
        "name": "Frontal pole, layer 2/3",
        "label_value": 667,
        "color": [0.149, 0.561, 0.271],
        "designations": [{ "scheme": "CCF", "code": "667", "meaning": "Frontal pole, layer 2/3" }]
      }
    ]
  }
}
```

The full CCF ontology follows this pattern for all 1327 structures. A reader that supports string references can reconstruct the complete hierarchy from the `label_value` arrays alone. A reader that does not support string references can still process the leaf segments and render the full-resolution labelmap; it simply cannot resolve the aggregate regions.

### 7.6 Minimal

A segmentation with the smallest useful metadata:

```json
"extensions": {
  "seg": {
    "version": "0.6",
    "segments": [
      { "id": "S1", "label_value": 1, "name": "Liver" },
      { "id": "S2", "label_value": 2, "name": "Spleen" }
    ]
  }
}
```

---

## 8. Design Notes

**Why `seg`, not `segmentation`.** This extension's data model — layers, `source_representation`, representation conversion state — is inherited directly from 3D Slicer's `.seg.nrrd` format, and the short name echoes that lineage. The Slicer-specific state itself (`contained_representations`, `conversion_parameters`, `reference_extent_offset`, per-segment auto-generation flags and tags) is confined to the `metadata.slicer` namespace, so the extension's first-class fields — segments, designations, label unions, layers, DICOM classification — remain platform-neutral. An application that knows nothing about Slicer can read and write conforming files while ignoring `metadata` entries it does not recognize.

**Why `designations` is an array.** A segment is a real anatomical or pathological entity. Different communities identify that entity using different coding systems. A kidney is SNOMED 64033007, FMA 7203, TA2 5765, and NCIt C12415 — simultaneously. An array of coded entries makes this multiplicity explicit and avoids privileging any single ontology. The first entry is the preferred identification.

**Why `dicom` is separate from `designations`.** The DICOM Segmentation IOD has a specific classification structure (category → type → modifier, plus anatomic region → modifier) that doesn't map cleanly to a flat list of codes. It is a *classification* pattern, not just an *identification* pattern. Mixing the two would either force the DICOM structure onto non-DICOM use cases (as `.seg.nrrd` does) or lose the structure needed for DICOM round-tripping. Keeping them separate means each concern has the right shape.

**Why the `terminologies` registry exists.** When a segment carries a code like `SCT:64033007`, a reader benefits from knowing what "SCT" means, what version was used, and where to look it up. The top-level `terminologies` object provides this once, rather than repeating it on every coded entry. The registry's `url` points to the coding *system*; concept-level URLs are derived from `url_template` plus a designation's `code`, so they are never embedded per-entry where they could drift or bloat.

**Why `meaning` is optional.** The coding system, not the file, is the authority on what a code means — an embedded name is at best a snapshot and at worst misinformation, so it cannot be part of a designation's identity. It is still worth carrying when known: it lets any viewer render the segment without a terminology service (SNOMED is licensed; ontology lookups need network access), it supplies DICOM's required CodeMeaning on export, and it gives humans a visible cross-check against the code. Hence: optional, recommended, and never authoritative — on conflict, the code wins.

**Why `name` is independent of designations.** Users name segments in ways that don't match any ontology: "suspect lesion #3", "Bob's left kidney", "ROI for dosimetry". The display name is a user-facing label that should be preserved exactly as given. Ontology codes are for interoperability; `name` is for the human in the loop. The optional `display` dict allows the display name itself to be multilingual — for example, a segmentation created in a German-speaking hospital can carry both the German and English segment names, independent of what any ontology calls the structure.

**Why `color` is here at all.** Color is technically a display hint, which the duckn convention generally avoids. However, segment color is so universally used in segmentation workflows — and so tightly bound to segment identity — that omitting it would force every application to reinvent a color-assignment scheme. It is a recommended display color, not a mandate.

**Why `segments` is an array, not a map.** Segments have a natural ordering (the order in which they were created or appear in the UI). An array preserves this. The `id` field provides stable lookup when ordering is irrelevant.

**Relationship to DICOM SEG.** The `dicom` classification object and the coded entry shape (`scheme`/`code`/`meaning`) are designed to be losslessly convertible to and from the *coded* content of DICOM Segmentation IOD segment descriptions: the coded entry triplet maps directly to DICOM's `CodeSequence` items, and `modifier` to its modifier sequences. Segment attributes outside that classification — `SegmentAlgorithmType`, `SegmentAlgorithmName`, and the rest of the segment macro — have no field here and belong in `metadata.dicom` if a converter needs to preserve them. The Slicer-specific "context name" strings are omitted — they named UI lookup tables, not data semantics.

Note that `metadata.dicom` (a converter's scratch space for unmapped DICOM attributes) is distinct from the first-class `dicom` field (the classification). In version 0.5 the classification itself lived under `metadata.dicom`; readers accepting older files should migrate it.

**Why `label_value` accepts arrays.** Overlapping structures are common in medical imaging — a tumor invading an organ, nested anatomical regions, or probabilistic boundaries. The layer mechanism handles this by duplicating the spatial volume, which is correct but expensive. Label unions offer an alternative: decompose the scene into non-overlapping islands (each with a unique label value in a single volume), then define each semantic segment as the union of one or more islands. The overlap region becomes a shared island. This is lossless, compact, and scales to many overlapping structures without adding dimensions. The two mechanisms coexist because they serve different workflows: layers are natural when segments are authored independently; label unions are natural when the decomposition into non-overlapping regions is computed upfront (e.g., by a segmentation pipeline that produces disjoint partitions).

**Why string entries in `label_value` reference by `id`, not by array index.** Array indices are positional and change when segments are reordered, inserted, or deleted. The `id` field is defined as stable — it does not change when the segment is renamed or reordered. Using `id` as the reference target means the reference graph remains valid across edits that do not change segment identity.

**Why the reference graph must be acyclic.** A cycle would make the effective voxel set of a segment depend on itself, which is undefined. An acyclic directed graph is sufficient to represent all biologically meaningful hierarchies, including ontologies with multiple inheritance (a segment may be referenced by any number of parents), as long as there is no circularity. Writers should validate acyclicity before serializing; readers encountering a cycle should treat the affected segments' effective voxel sets as undefined and report an error (§5).

**Why string references do not carry layer information.** The `layer` field of a referenced segment is authoritative for that segment's voxel data. When resolving a reference chain, each segment's layer is taken from its own definition. This avoids needing to re-specify layer context at every reference site and ensures that layer assignments are defined once, close to the data they describe.
