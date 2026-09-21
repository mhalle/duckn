# Segmentation Extension for duckn — 0.8 Draft

**Extension name:** `seg`
**Version:** 0.8 (proposed)
**Status:** Draft — not implemented. `segmentation-ext-spec.md` describes version 0.7, which is what the released code reads and writes. This document replaces it when 0.8 is implemented.

---

## 1. Purpose

This document defines the `seg` extension for the duckn convention. It replaces the `.seg.nrrd` metadata encoding — where segment properties were flattened into NRRD key/value pairs with `SegmentN_` prefixes and `~^|&`-delimited substructure — with a clean JSON representation.

### Scope

Version 0.8 draws the extension's boundary with one test. A field belongs in the file when either:

1. a reader needs it to **interpret the voxels faithfully** — which values are which segment, which values mean "nothing here" and which mean "not evaluated"; or
2. **a standard imaging file carries its equivalent** — a DICOM Segmentation's segment label, coded property type, and recommended display color; a `.seg.nrrd` file's segment name, color, and terminology entry — so that a round trip needs it.

Everything else is enrichment: alternative renditions, groupings and hierarchies, cross-walks to further ontologies, translations, measurements. Those are real needs, and experience shows several of them are common. But they change for their own reasons, under other authorities, and most of them are properties of a *labeling scheme* rather than of one file — the hierarchy of an atlas applies to every volume labeled with that atlas. They belong in documents outside the file that reference it. What the file owes those documents is a stable way to be referenced (§7), and that is all this extension says about them.

The result is smaller than version 0.7, which carried groups and their claims in the file, and much smaller than the unbundling into peer `semantic` and `rendition` extensions that was explored after it. A segment is again one record: its values, its identity, its recommended color.

---

## 2. Data Layout

A segmentation is a Zarr array whose voxel values encode segment membership. For binary labelmaps, these are integer labels; for fractional labelmaps, they are continuous values. Spatial embedding (origin, directions, space) is described by the duckn convention fields as usual.

An array carrying this extension should declare a matching duckn `intent`: `"label-map"` for a binary labelmap, `"probability-map"` for a fractional one. The extension does not require it — `intent` describes the array, `source_representation` describes the segmentation — but a reader that dispatches on `intent` alone should still see the right thing.

Everything in this section describes binary labelmaps until the subsection on fractional ones.

### Segments own sets of values

The array has 3 spatial dimensions. Each **segment** lists the integer voxel values that belong to it, in `label_values`. A segment's voxels are all voxels in its layer whose value is in that list. In the common case the list has one entry and no two segments share a value: that is the classic label table, one row per value (§5).

### Overlapping segments: shared values

Overlap within a single volume is represented by decomposing the scene into non-overlapping **islands**, each with its own voxel value, and letting segments share values. A tumor that partially overlaps the liver is three islands — liver only (1), tumor only (2), and the overlap (3) — and two segments:

```json
"segments": [
  { "id": "liver", "name": "Liver", "label_values": [1, 3] },
  { "id": "tumor", "name": "Tumor", "label_values": [2, 3] }
]
```

Value 3 belongs to both. The islands themselves have no segment entries and no ids: an intersection is a set operation on two segments, not a third thing. Where an intersection *is* something in its own right — "tumor within the liver" as a clinical region — a writer gives it a segment of its own, `{"id": "invaded", "label_values": [3]}`, and the value is then shared three ways.

### Overlapping segments: layers

Alternatively the array has a `list` axis (kind `"list"`) plus 3 spatial dimensions. Each position along the list axis is a **layer** — a 3D label volume. Segments that would collide in a single volume are assigned to different layers, and the same value may be used in different layers without any relation between its uses.

The two mechanisms are independent and may coexist. Layers duplicate the volume and are natural when segments are authored independently; shared values partition it and are natural when a pipeline computes the decomposition up front.

### Background, unknown, and undescribed values

Three kinds of voxel are not part of any structure, and they mean different things.

**Background** is a definite claim: *none of the structures this layer describes is here.* An unlabeled voxel is a negative for every segment in the layer, which is what makes it countable — a voxel labeled in one segmentation and background in another is a disagreement. It is not a claim that nothing at all is there: a liver-only segmentation's background says "not liver" and is silent about the spleen, because the file has no spleen segment to be negative about. By default a layer's background is the value 0 — a Zarr array's `fill_value` is typically 0, so 0 is what every unwritten voxel reads as. A layer whose background is another value, or deserves a name, declares a segment with `"role": "background"`.

**Unknown** is the absence of a claim: *this voxel was not classified.* A scan cropped mid-organ, an artifact region, a model that abstains below a confidence threshold, a manual segmentation with three of five lesions done — in each, labeling the voxels as background would assert "nothing here" where the truth is "not evaluated." A segment with `"role": "unknown"` says so. A reader excludes unknown voxels from comparisons and measurements rather than counting them either way; this is the "ignore index" of segmentation benchmarks, and for the same reason. Unknown is always explicit: if unlabeled voxels defaulted to unknown, no comparison could ever penalize over-segmentation. A writer who wants unwritten regions to read as unevaluated sets the array's `fill_value` to an unknown segment's value.

**Undescribed** values are a writer's error with a defined reading. Every value present in a layer's data must belong to some segment (§5), so that a reader can always answer "what is value 5?" When one does not, a validator with the data reports it, and a reader does not fail: it treats the value as unknown — rendered distinctly, excluded from comparison — and surfaces the problem. Merging it into background would silently erase something a writer painted; treating it as a structure would be a guess. A writer has two conforming ways to say what such a value is, and they mean different things: a **minimal segment**, `{"id": "v5", "label_values": [5]}` — "a structure I have not named," counted as itself — or the same with `"role": "unknown"`.

### Fractional labelmaps

When `source_representation` is `"fractional-labelmap"`, voxel values are continuous — the fraction of the voxel occupied by a segment, or the probability that it belongs to one — and the integer-equality rule above does not apply.

Each segment therefore needs its own volume of fractional values, so a fractional segmentation **must** carry a `list` axis and assign every segment a distinct `layer`. `label_values` still identifies the segment within its layer and is conventionally `[1]`, but it selects a layer's worth of fractional values rather than matching voxels by equality. Shared values and roles are defined only for binary labelmaps.

The value range is not constrained here — `[0, 1]` is typical, but an application storing 0–255 or 0–100 should record the scaling with the duckn convention's `value_transforms` rather than inventing a convention in this extension.

### Empty segmentation

Unlike `.seg.nrrd`, a Zarr store does not require non-empty data. An empty segmentation can be represented as a zero-extent array or by providing only the extension metadata with no voxel data. `segments` is still required, but it may be an empty array for a segmentation that describes nothing. This is the one exception to §4.4's rule against empty collections: `segments` is structural, and omitting it would be indistinguishable from a malformed file.

---

## 3. Extension Fields

The `seg` extension is declared under the `"duckn"` object's `"extensions"` key.

### 3.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification, as a string.

```json
"version": "0.8"
```

**Version semantics.** While the major version is `0`, the *minor* version may introduce breaking changes; this overrides the duckn convention's default rule that minor increments are additive. From 1.0 onward, major increments signal breaking changes and minor increments are additive. `version` must be a string: a JSON number `0.10` is the float 0.1, and a reader that parsed it that way would mistake a later file for an earlier one. A missing or unparseable version is an error, never "older than everything."

Version 0.8 is a breaking change from 0.7; §6.3 describes what changed and how older files are read.

#### `source_representation`

The segmentation's primary representation — the one edited by the user and from which others are derived. One of `"binary-labelmap"`, `"fractional-labelmap"`, `"closed-surface"`, `"planar-contour"`. Unchanged from 0.7.

```json
"source_representation": "binary-labelmap"
```

#### `labeling_scheme`

The labeling scheme this segmentation was produced under, as a key of `terminologies` — or an array of keys, for an array whose layers follow different schemes.

```json
"labeling_scheme": "TotalSegmentator"
```

A labeling scheme is a coding system whose codes are the classes of a segmenter, an atlas, or a protocol: TotalSegmentator's class list at a version, the Allen CCF structure ontology, FreeSurfer's `aseg` labels. Declaring it says two things. Every segment that is a structure carries a designation in that scheme (§5), so the class each segment was produced as is recorded, exactly, in the file. And the registry entry gives the scheme's version, so a reader knows *which* edition of the class list that was.

This is the join that lets documents outside the file apply to it without per-file work. A hierarchy, a color scheme, or a cross-walk to SNOMED written once for "TotalSegmentator 2.4" applies to every file that declares that scheme (§7). It is the scheme's *definition* that is recorded — identity — and not an account of the run that applied it, which is provenance and belongs to the `provenance` extension.

A variant with a different class list — TotalSegmentator's `total` and `total_mr` tasks — is a different scheme and is registered under its own key. A hand-drawn segmentation declares no scheme.

#### `terminologies`

An object registering the coding systems used in this extension's coded entries. Each key is a short identifier for the system (the value used as `scheme`); each value is an object describing it.

```json
"terminologies": {
  "SCT": {
    "name": "SNOMED Clinical Terms",
    "version": "2024-09-01",
    "url": "https://browser.ihtsdotools.org",
    "url_template": "https://browser.ihtsdotools.org/?perspective=full&conceptId1={code}"
  },
  "TotalSegmentator": {
    "name": "TotalSegmentator class labels, task total",
    "version": "2.4",
    "url": "https://github.com/wasserth/TotalSegmentator"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | no | Full human-readable name of the coding system |
| `version` | no | Version of the coding system in use. Should be present for a scheme named by `labeling_scheme` |
| `url` | no | URL for the coding system's browser, specification, or landing page |
| `url_template` | no | Template for concept URLs. The substring `{code}` is replaced with a coded entry's `code` |

Every scheme used in a segment's `designations` or `dicom` entries **should** be registered here. Registration is a recommendation, not a requirement, with one exception: a scheme named by `labeling_scheme` must be registered. Readers must not reject an unregistered `scheme`, and must not assume the registry enumerates every scheme in the file.

#### `segments`

Required. An array of segment objects (§3.2). The array index is the segment's ordinal position — it replaces the `N` in `SegmentN_*`. The order is also the **recommended draw order**, later over earlier, where segments overlap (§3.2 `color`); it carries no other meaning. May be empty for an empty segmentation (§2).

#### `metadata`

An open-ended object for application-specific state about the segmentation, keyed by source application. The well-known key `slicer` holds 3D Slicer's `contained_representations`, `conversion_parameters`, and `reference_extent_offset`. Unchanged from 0.7.

#### `legacy`

Optional. Verbatim source metadata preserved so a file converted *from* another format can be converted back without loss of formatting. For `.seg.nrrd` sources this holds the original key/value strings under a `keyvalues` object. A writer that has not modified the segmentation may replay these strings byte-for-byte; a writer that has modified it must regenerate from the model and should drop or refresh the stale entries. Readers that are not round-tripping to the source format ignore it. Unchanged from 0.7.

### 3.2 Segment Object Fields

Each element of `segments` is a JSON object. All fields are optional except `id` and `label_values`.

#### `id`

A stable, unique identifier for the segment within this segmentation. It does not change when the segment is renamed or the array reordered. It is the handle by which anything outside the file refers to the segment (§7).

```json
"id": "kidney_left"
```

An `id` is a token: non-empty, and containing none of `.`, `/`, or `#`, which the reference syntax reserves (§7.1). Ids in practice are tokens anyway — `997`, `Segment_1`, `ctx-lh-bankssts`. A DICOM UID, which contains dots, belongs in `metadata`, not in the handle other things point at.

#### `name`

The human-readable display string, as the author gave it. It is independent of any ontology: it may echo a designation's `meaning`, be a local nickname, or be the only identity a segment has — "suspect lesion #3" has no code and needs none.

```json
"name": "Right kidney"
```

In this convention the string is a *name*, never a *label*: "label" means an integer voxel value throughout (§9). DICOM's `SegmentLabel`, which is a string, maps here.

#### `label_values`

Required. The integer voxel values belonging to this segment, as a non-empty array of distinct integers. The segment's voxels are all voxels in its layer whose value is in the array.

```json
"label_values": [1, 3]
```

It is always an array, even for one value: `[1]`, never `1`. A value may appear in more than one segment of the same layer; that is how overlap is represented (§2). The **effective value set** of a segment is the set of *(layer, value)* pairs formed from its `layer` and each entry — a value only identifies voxels within a layer.

A segment with no `role` must not list its layer's background value (§5).

#### `role`

Optional. One of:

| Value | Meaning | In a comparison or measurement |
|-------|---------|-------------------------------|
| `"background"` | these values mean "none of the described structures is here" | counted, as the negative class |
| `"unknown"` | these values mean "not classified" | excluded |

Omit for a segment that is a structure. At most one segment per layer has the background role; any number may be unknown. A layer's background value set is its background segment's `label_values`; when the layer has no background segment it is `[0]`, unless an unknown segment lists 0, in which case the layer has no background at all — FreeSurfer's `aseg`, whose 0 is "Unknown," is such a layer.

The *reason* a region is unknown — artifact, outside the field of view, abstained — is a meaning and goes in `name` or `designations`. That is what keeps this field at two values.

#### `layer`

The zero-based index of the layer (position along the `list` axis) holding this segment's values. Omit for segmentations with one layer; an absent `layer` means layer 0. When an array has more than one `list`-kind axis, `layer` indexes the first.

#### `extent`

The bounding box of the segment's non-empty region, as `[min_i, max_i, min_j, max_j, min_k, max_k]` in voxel coordinates, both bounds inclusive, over the array's three spatial axes in storage order. Unchanged from 0.7.

```json
"extent": [45, 102, 30, 98, 12, 55]
```

#### `color`

Optional. The producer's **recommended display color**, as a CSS color string. It is a recommendation in the sense DICOM gives the word: a viewer may override it, and a rendition applied from outside the file takes precedence over it.

```json
"color": "#dd8265"
```

A string is used rather than a triple of numbers because the syntax states the color space: `#dd8265` is sRGB by definition and `lab(60.014 -9.012 35.198)` is CIELab under D50, where `[0.87, 0.51, 0.40]` could be sRGB, linear light, or anything else. No earlier version of this extension said which.

| | Writers | Readers |
|---|---|---|
| **must** | write `#rrggbb`, or `#rrggbbaa` with alpha, for an sRGB color that fits 8 bits per channel; write a color whose source is CIELab as `lab()` (§6.2) | accept hex, `rgb()`, and `lab()` |
| **may** | write another absolute CSS color — `hsl()`, `oklch()`, `color()` in a predefined space, a named color — for wider gamut or precision | accept the absolute color grammar of CSS Color Level 4 |
| **never** | `currentColor`, system colors, `color-mix()`, relative colors | — |

A value a reader cannot parse is treated as absent, and an absent color means the viewer chooses. Alpha defaults to opaque; a reader may ignore it. Conversion between color spaces for display is CSS Color 4's, and this extension defines none of its own.

**Overlap.** Where two segments cover the same voxel — a shared value, or the same position in two layers — the recommended draw order is the order of `segments`, later over earlier. A color table with one entry per value gives a shared value the color of the last segment listing it.

#### `designations`

An array of coded entries identifying what this segment represents in external coding systems. Each entry says "this segment is concept *X* in system *Y*," **exactly**. The first entry is the preferred identification. See §4.1.

```json
"designations": [
  { "scheme": "SCT", "code": "18639004", "meaning": "Left kidney structure" },
  { "scheme": "TA2", "code": "5765", "meaning": "Kidney",
    "modifier": { "scheme": "SCT", "code": "7771000", "meaning": "Left" } }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `scheme` | yes | Key identifying the coding system; should match an entry in `terminologies` (§3.1) |
| `code` | yes | The concept identifier within that coding system |
| `meaning` | no | Human-readable name of the concept, as of the registered terminology version. Recommended when known |
| `modifier` | no | A coded entry qualifying this one, typically laterality. One level of nesting. Its `scheme` need not match the base entry's |

#### `dicom`

The DICOM Segmentation IOD classification structure (category, type, type modifier, anatomic region, region modifier). See §4.2. Present only when DICOM SEG interoperability is needed.

#### `metadata`

An open-ended object for application-specific per-segment metadata, keyed by source application or standard. The well-known key `slicer` holds `name_auto_generated`, `color_auto_generated`, and `tags`; `dicom` is a converter's scratch space for segment attributes this extension has no field for (`SegmentAlgorithmType`, `SegmentAlgorithmName`, tracking identifiers).

---

## 4. Segment Identity

Segment identity has two parts:

1. **Designations** — coded entries saying "this segment is concept *X* in system *Y*." The primary mechanism for interoperable identity.
2. **DICOM classification** — the structured category/type/modifier/region hierarchy required by the DICOM Segmentation IOD. Needed only for DICOM round-tripping.

### 4.1 Designations

A segment is a real anatomical or pathological thing that different communities identify using different coding systems — a kidney is SNOMED 64033007, FMA 7203, and TA2 5765 simultaneously. Each entry captures one such identification; the first is preferred. When `labeling_scheme` is declared, one of them is the class the segment was produced as.

#### Designations are exact

Every consumer relies on one contract: a designation *is* the segment. A DICOM exporter takes the first designation as the property type; a lookup by code returns the segment; a stylesheet keyed by code paints it. So a designation is written only when the identification is exact. A segment that is merely *near* a concept, or narrower than one — a mass that may be a neoplasm; "kidney" for a left kidney in a scheme that cannot say left — does not get that concept as a designation. Weaker correspondences are enrichment and are recorded outside the file (§7.2).

Coverage is a different question from identity. A liver segment from a scan cropped mid-organ is exactly liver — every voxel in it is liver — and is designated so. That it is not *all* of the liver is said by the unknown role on the region that was not evaluated, never by weakening the designation.

#### Codes are authoritative; meanings are renderings

The identity carried by a designation is `scheme` + `code` (+ `modifier`). The coding system itself — at the version recorded in `terminologies` — is the source of truth for what that code means. `meaning` is a snapshot embedded for the reader's convenience: it lets a viewer display something sensible without a terminology service, it supplies DICOM's required CodeMeaning on export, and it is a human-auditable cross-check. **If the embedded `meaning` disagrees with the coding system, the code wins.** Writers should include `meaning` when they know it; readers must not treat it as identity.

Concept URLs are not embedded per entry; readers derive them from the registry's `url_template`.

#### Post-coordination via `modifier`

Many coding systems express "left kidney" as a base concept plus a qualifier, and some — Terminologia Anatomica among them — have no laterality at all. The `modifier` field carries the qualifier as a nested coded entry, and **its scheme need not match the base entry's**: TA2 "Kidney" qualified by SNOMED "Left" is an exact identification of a left kidney. This mirrors DICOM's modifier code sequences, whose laterality codes are SNOMED whatever the type code's scheme. Modifiers nest one level only. A writer prefers a pre-coordinated code where the scheme has one, and post-coordination otherwise; both are exact.

#### Relationship to `name`

`name` is what is shown; `designations` are what is used for computation, interoperability, and lookup. They are independent.

### 4.2 DICOM Classification

```json
"dicom": {
  "category": { "scheme": "SCT", "code": "49755003", "meaning": "Morphologically abnormal structure" },
  "type": { "scheme": "SCT", "code": "4147007", "meaning": "Mass" },
  "anatomic_region": { "scheme": "SCT", "code": "23451007", "meaning": "Adrenal gland" },
  "anatomic_region_modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" }
}
```

Each entry is a coded entry with the same shape as a designation, minus `modifier`. Every field is optional, so a partial classification can be recorded as it becomes known. Writing a conformant DICOM SEG object additionally requires `category` and `type`, so a writer that finds either missing must fail rather than invent one.

| Field | Required | Description |
|-------|----------|-------------|
| `category` | for DICOM SEG export | Segmentation Category |
| `type` | for DICOM SEG export | Segmentation Type within the category |
| `type_modifier` | no | Qualifier on the type |
| `anatomic_region` | no | Anatomic region |
| `anatomic_region_modifier` | no | Qualifier on the region, typically laterality |

DICOM classification entries use SNOMED CT codes by convention, but the scheme is always explicit — legacy files may carry SRT or DCM codes. When writing DICOM SEG, CodeMeaning is required: writers derive it from the entry's `meaning`, falling back to the segment's `name`, and fail if neither is available.

### 4.3 Relationship Between Designations and DICOM

The two are independent; the same SNOMED concept may appear in both. `designations` answers "what is this structure, in any ontology?" and `dicom` answers "how is this segment classified in a DICOM Segmentation IOD?" A DICOM writer uses `dicom`; lookup and cross-referencing use `designations`.

### 4.4 Absence and Omission

Following the convention's "absent means unknown" principle:

- If a segment has no designations, omit `designations`. Do not include an empty array.
- If a coded entry's `meaning` is unknown, omit it. Do not use an empty string or `null`.
- If a segment has no DICOM classification, omit `dicom`; within it, omit modifiers that do not apply.
- If a segment has no role, omit `role`. If it has no recommended color, omit `color`.
- If no terminology registrations are needed, omit `terminologies`.

---

## 5. Consistency Rules

A segment's **effective value set** is the set of *(layer, value)* pairs defined in §3.2. Two segments may have the same effective value set: identity is the `id`, never the voxels.

**Structure**

1. The length of `segments` is independent of any axis size.
2. Where a segment specifies a `layer`, there must be a `list`-kind axis in the array, and `layer` must be a valid index into it. A segment in an array with no `list` axis omits `layer` rather than specifying 0.
3. Where a `kind` constraint requires a specific axis size (from the duckn convention), the corresponding `shape` element must match.
4. A fractional segmentation must have a `list` axis, and each of its segments must have a distinct `layer`, a single entry in `label_values`, and no `role`.

**Identity**

5. `id` must be unique across `segments`, non-empty, and must not contain `.`, `/`, or `#`.
6. `scheme` values used in `designations` or `dicom` should have a key in `terminologies`. Every key named by `labeling_scheme` must.
7. When `labeling_scheme` is declared, every segment with no `role` carries a designation whose `scheme` is a declared labeling scheme.

**Values**

8. `label_values` is a non-empty array of distinct integers. Booleans are not integers.
9. At most one segment per layer has `"role": "background"`. A segment with no `role` must not list a value in its layer's background value set (§3.2 `role`).
10. A value listed by a segment with a `role` is not listed by any other segment of the same layer: "nothing here" and "not evaluated" do not overlap a structure, or each other.
11. Every value present in a layer's voxel data is in the layer's background value set or in some segment's `label_values`. This is a writer's obligation and the one rule that needs the data. A reader meeting a violation treats the value as unknown and reports it (§2).

Rules 1 and 5–10 constrain the metadata alone; rules 2–4 additionally require the array's shape and axes; rule 11 requires the voxel data, which this extension never requires a reader to load.

**The label table.** A layer in which every segment has exactly one entry in `label_values` and no value is shared is a classic label table: one row per value, one color per value, exportable as a single labelmap with no relabeling. This is a property a reader or exporter *checks*, not a constraint on files; version 0.7 imposed it on every file, and 0.8 makes it the special case.

Writers should validate before serializing. Readers should not assume a file is valid.

---

## 6. Mapping to Other Formats

### 6.1 `.seg.nrrd`

| `.seg.nrrd` field | duckn `seg` extension field |
|---|---|
| `Segmentation_MasterRepresentation` / `Segmentation_SourceRepresentation` | `source_representation` |
| `Segmentation_ContainedRepresentationNames` | `metadata.slicer.contained_representations` (array) |
| `Segmentation_ConversionParameters` | `metadata.slicer.conversion_parameters` (object) |
| `Segmentation_ReferenceImageExtentOffset` | `metadata.slicer.reference_extent_offset` (array) |
| `SegmentN_ID` | `segments[n].id` |
| `SegmentN_Name` | `segments[n].name` |
| `SegmentN_NameAutoGenerated` | `segments[n].metadata.slicer.name_auto_generated` (boolean) |
| `SegmentN_Color` | `segments[n].color` (CSS color string, hex) |
| `SegmentN_ColorAutoGenerated` | `segments[n].metadata.slicer.color_auto_generated` (boolean) |
| `SegmentN_LabelValue` | `segments[n].label_values` (one-entry array; several entries when space-separated) |
| `SegmentN_Layer` | `segments[n].layer` (integer; omitted when 0) |
| `SegmentN_Extent` | `segments[n].extent` (6-element array) |
| `SegmentN_Tags` (minus TerminologyEntry) | `segments[n].metadata.slicer.tags` (object) |
| `SegmentN_Tags` TerminologyEntry — category/type/modifier/region | `segments[n].dicom` (object) |
| `SegmentN_Tags` TerminologyEntry — type code + type modifier | `segments[n].designations` (first entry, modifier included) |
| `SegmentN_Tags` TerminologyEntry — context names | Omitted (application state) |
| — (no `.seg.nrrd` equivalent) | `segments[n].role`, `labeling_scheme` |

**Color.** On import each float channel `x` becomes `round(255 * x)` and the color is written as hex. On export each channel is written as `n / 255` with six significant digits, which is how 3D Slicer writes it, so a color that came from Slicer returns byte for byte. Any other color — a `lab()` from DICOM, a wide-gamut color — is converted to sRGB by CSS Color 4's conversion and clipped. Alpha is not exported.

**Overlap on export.** 3D Slicer writes one `LabelValue` per segment and represents overlap with layers. An exporter that finds shared values **materializes** them: segments whose value sets are pairwise disjoint within a layer are written as they are, relabeled one value per segment where a segment lists several; segments that share values are assigned to further layers until no two in a layer overlap. The voxels of every segment are preserved; the island decomposition is not, and does not need to be. An unmodified file with `legacy` key/values is replayed verbatim instead.

**Roles on export.** A background segment at value 0 is not written as a segment. Any other segment with a role is written as an ordinary segment, which is all the format can say.

**Parsing notes** (unchanged from 0.7): accept either the `Master` or `Source` representation key; normalize representation names to kebab-case; split pipe-delimited lists and drop empty elements; strip the `Segmentation.` prefix from tag keys; drop a `Layer` of 0; do not default a missing `LabelValue` to 0, assign an unused value instead; keep a two-part coded entry whose meaning is empty.

### 6.2 DICOM Segmentation

| DICOM attribute | duckn `seg` extension field |
|---|---|
| `SegmentNumber` | position; in a LABELMAP, the entry of `label_values` |
| `SegmentLabel` | `segments[n].name` (and `id`, when usable as a token) |
| `SegmentedPropertyCategoryCodeSequence` | `segments[n].dicom.category` |
| `SegmentedPropertyTypeCodeSequence` (+ modifier) | `segments[n].dicom.type`, `type_modifier`; and the first of `designations` |
| `AnatomicRegionSequence` (+ modifier) | `segments[n].dicom.anatomic_region`, `anatomic_region_modifier` |
| `RecommendedDisplayCIELabValue` | `segments[n].color`, as `lab()` |
| `SegmentAlgorithmType`, `SegmentAlgorithmName`, tracking identifiers | `segments[n].metadata.dicom` |
| `SegmentationType` `BINARY` / `FRACTIONAL` / `LABELMAP` | `source_representation`, and the layer layout |

**Color.** DICOM encodes CIELab as the ICC profile connection space, whose reference white is D50 — the same color space as CSS Color 4's `lab()`. The three 16-bit values are therefore transcribed, not converted: `L = v × 100 / 65535`, and `a = v × 255 / 65535 − 128`, likewise `b`, written as `lab(L a b)` with at least three decimal places, which recovers the 16-bit integers exactly. `[39330, 30580, 41942]` becomes `"lab(60.014 -9.012 35.198)"`. Export converts the segment's color to `lab()` by CSS Color 4's conversions and scales to 16 bits; alpha is dropped. When the attribute is absent the color is left absent.

dcmqi, and 3D Slicer through it, have long computed these values with a D65 reference white, so many existing files carry numbers that, read correctly, differ by a few 8-bit levels per channel from the color their author saw. A converter transcribes the numbers as written by default; it may offer compensation for files it can identify as produced that way, as an explicit option that is off by default.

**Overlap on export.** A segmentation whose layers are all label tables (§5) exports as `LABELMAP`, with values renumbered 1..N as that type requires. One with shared values or several layers exports as `BINARY`, one segment's frames per segment, which DICOM permits to overlap. No segment is dropped and none is refused on account of overlap.

**Roles on export.** A background segment is not exported. A segment with the unknown role is exported as an ordinary segment with whatever designation it carries.

### 6.3 Reading Older Files

A reader for 0.8 that accepts older files migrates on load.

| Older shape | 0.8 shape |
|---|---|
| `label_value: 5` (0.7; 0.6 scalar) | `label_values: [5]` |
| `label_value: [1, 3]` (0.6 list of integers) | `label_values: [1, 3]` |
| `background: true` (0.7) | `role: "background"` |
| `color: [r, g, b]` floats | `#rrggbb`, each channel `round(255 * x)`, **read as sRGB** — the first time that assumption is written down |
| `display` (multilingual names) | kept under `metadata.display`; translations are now external (§7.2) |
| a group — 0.7 `members`, or 0.6 string entries in `label_value` — whose effective values lie in one layer | a segment whose `label_values` is that set, keeping the group's `id`, `name`, `color`, `designations`, and `dicom` |
| a group whose effective values span layers | no 0.8 form; reported and omitted |
| `disjoint`, `exhaustive` (0.7) | dropped; reported when a claim is discarded |
| an island leaf synthesized by the 0.7 migration (`label_<value>`, no designations), whose value a migrated group now lists | omitted |
| 0.5 `identifiers`, `metadata.dicom` classification, Slicer fields | as in 0.6: `designations`, `dicom`, `metadata.slicer` |

Migration from 0.6 is nearly the identity, because 0.8's `label_values` is 0.6's list of integers made uniform. A 0.7 hierarchy — an atlas whose interior structures were groups — survives as segments whose value lists are the transitive union of their descendants': the voxels of "Isocortex" are still recoverable from the file alone. What is lost is the tree itself and the partition claims, which belong to the atlas and are supplied from outside (§7.2).

---

## 7. References From Outside the File

### 7.1 What the File Guarantees

Renditions, groupings, cross-walks, translations, and measurements live in documents outside the file. For those documents to be possible, this extension guarantees the following, and nothing more.

**Stable ids.** A segment's `id` is unique, does not change on rename or reorder, and is a token (§3.2). Its qualified name is `seg.segments.<id>`: dots walk the duckn metadata, by id and never by array index. Where a reference must also name the array, the Zarr path comes first and `#` separates the two — `study/seg#seg.segments.liver` — because Zarr node names may contain dots and slashes are Zarr's. That is why `.`, `/`, and `#` are reserved in ids.

**Portable keys.** An id means something only in its own file. Three things in the file mean the same in every file, and an external document may select by them with no file in hand:

- a **designation** — scheme, code, and optionally modifier — compared for equality, with no reasoning over a terminology's hierarchy;
- a **role** — `background` or `unknown`, including the background a reader assigns to value 0 by default;
- **undescribed** — values present in the data that no segment lists (§2).

**The labeling scheme.** `labeling_scheme` and its registered version identify which documents written for a scheme apply to this file. A document written for another version of the scheme is reported as such, not silently applied.

**Stale references are reported.** An external document naming an id the file does not have, or a code no segment carries, has matched nothing. For an id that is an error to report; for a code it is normal — a cropped scan has no left kidney — and a checker reports which members of a grouping were absent rather than failing.

**The file is never modified** by applying an external document. A writer that embeds a result — a color taken from a stylesheet, written into `color` — has made a new recommendation, which is thereafter the file's own.

### 7.2 Notes on External Documents

*This section is not normative. The external documents are the part of the design most in flux, and their specification is deferred; these notes record what has been settled so far and where the drafts are.*

- **Renditions.** Several named renditions per file, a default, CSS-like rules (selector plus declaration block) with a cascade, `color` / `opacity` / `display` properties, explicit paint order, flattening to a color table, and image windows. `rendition-ext-spec.md` drafts this as an in-file extension; under the present scope it becomes the external stylesheet format, and the file's own `color` values are the floor it cascades over — viewer, then file, then stylesheet. A small in-file home for the recommended windows of a grayscale image (DICOM's `WindowCenter`/`WindowWidth`, NIfTI's `cal_min`/`cal_max`) is still needed and belongs with the image, not with `seg`.
- **Groups and hierarchy.** A group has an id, a name, designations, and members given as selectors — normally designation codes in a labeling scheme, so that one definition of TotalSegmentator's groupings or the Allen structure graph serves every file that declares the scheme; by segment id for an ad hoc group in one study; or another group. Claims of `disjoint` and `exhaustive` live on the group. A member that matches nothing in a file is not an error. Stylesheets paint and select groups by id.
- **Inexact correspondences.** `closeMatch`, `broadMatch`, `narrowMatch`, and `relatedMatch`, keyed by relation with lists of coded entries, in the manner of SKOS; and cross-walks adding designations in further ontologies after the fact. `semantic-ext-spec.md` drafts these, with the conceptual model — entities, bindings, the three kinds of meaning — that describes the external layer, where things other than segments (fiducials, class ranks) would be bound to the same vocabulary.
- **Translations** of names, per language.
- **Measurements.** DICOM puts these in a separate object, the structured report, and so does this design. Records of a coded concept, a value, a UCUM unit, and one segment or a pair; checkable where recomputable from the arrays referenced.
- **Scope of a document.** One external specification with sections is preferred to several, since every section shares the targeting mechanism of §7.1. Where duckn metadata is placed on a Zarr *group*, that group's attributes are a natural home for documents that span its arrays, with references that may not leave the group.

---

## 8. Examples

### 8.1 Non-Overlapping Labelmap

Two kidneys from a model with a declared labeling scheme, each identified three ways.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "labeling_scheme": "TotalSegmentator",
  "terminologies": {
    "SCT": { "name": "SNOMED Clinical Terms", "version": "2025-03",
             "url_template": "http://snomed.info/id/{code}" },
    "TA2": { "name": "Terminologia Anatomica 2nd Edition",
             "url": "https://ta2viewer.openanatomy.org" },
    "TotalSegmentator": { "name": "TotalSegmentator class labels, task total", "version": "2.4",
             "url": "https://github.com/wasserth/TotalSegmentator" }
  },
  "segments": [
    {
      "id": "kidney_right",
      "name": "Right kidney",
      "label_values": [2],
      "color": "#b97a57",
      "designations": [
        { "scheme": "TotalSegmentator", "code": "kidney_right" },
        { "scheme": "SCT", "code": "9846003", "meaning": "Right kidney structure" },
        { "scheme": "TA2", "code": "5765", "meaning": "Kidney",
          "modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" } }
      ],
      "dicom": {
        "category": { "scheme": "SCT", "code": "123037004", "meaning": "Anatomical Structure" },
        "type": { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" },
        "type_modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" }
      }
    },
    {
      "id": "kidney_left",
      "name": "Left kidney",
      "label_values": [3],
      "color": "#b5651d",
      "designations": [
        { "scheme": "TotalSegmentator", "code": "kidney_left" },
        { "scheme": "SCT", "code": "18639004", "meaning": "Left kidney structure" },
        { "scheme": "TA2", "code": "5765", "meaning": "Kidney",
          "modifier": { "scheme": "SCT", "code": "7771000", "meaning": "Left" } }
      ]
    }
  ]
}
```

Every layer here is a label table (§5). TA2 has no laterality, and the cross-scheme modifier makes its designation exact all the same.

### 8.2 Overlap by Shared Values, with an Unknown Region

A liver, a lesion that partially overlaps it, and a region degraded by motion that was not evaluated. Values: 1 liver only, 2 lesion only, 3 both, 9 artifact.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "terminologies": {
    "SCT": { "name": "SNOMED Clinical Terms", "version": "2025-03" }
  },
  "segments": [
    {
      "id": "liver", "name": "Liver", "label_values": [1, 3], "color": "#dd8265",
      "designations": [{ "scheme": "SCT", "code": "10200004", "meaning": "Liver" }]
    },
    {
      "id": "tumor", "name": "Suspect lesion #3", "label_values": [2, 3], "color": "#cc3333b3",
      "designations": [{ "scheme": "SCT", "code": "4147007", "meaning": "Mass" }]
    },
    {
      "id": "artifact", "name": "Motion artifact, not evaluated",
      "label_values": [9], "role": "unknown", "color": "#80808080"
    }
  ]
}
```

Value 3 is listed twice and has no entry of its own. The lesion comes after the liver, so it is drawn over it, and a one-color-per-value table gives value 3 the lesion's color. The lesion is exactly a mass; that it may be a neoplasm is a weaker statement and is not a designation. Background is 0 by default and needs no segment. A Dice score against another reader's segmentation excludes the voxels at 9.

### 8.3 Overlap by Layers

The same liver and lesion, authored independently in two layers of an array with a `list` axis. Both use value 1, which is unrelated between layers.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "segments": [
    { "id": "liver", "name": "Liver", "label_values": [1], "layer": 0, "color": "#dd8265" },
    { "id": "tumor", "name": "Suspect lesion #3", "label_values": [1], "layer": 1, "color": "#cc3333b3" }
  ]
}
```

### 8.4 An Atlas Under a Labeling Scheme

An excerpt of a whole-brain mouse atlas whose voxel values are Allen CCF structure ids. The file carries one segment per value present and declares the scheme; the structure hierarchy is the atlas's, is the same for every volume labeled with it, and is supplied from outside (§7.2), keyed by these codes.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "labeling_scheme": "CCF",
  "terminologies": {
    "CCF": { "name": "Allen Mouse Brain Common Coordinate Framework, structure ontology",
             "version": "3", "url": "https://atlas.brain-map.org" }
  },
  "segments": [
    { "id": "68", "name": "Frontal pole, layer 1", "label_values": [68],
      "color": "#268f45",
      "designations": [{ "scheme": "CCF", "code": "68", "meaning": "Frontal pole, layer 1" }] },
    { "id": "667", "name": "Frontal pole, layer 2/3", "label_values": [667],
      "color": "#268f45",
      "designations": [{ "scheme": "CCF", "code": "667", "meaning": "Frontal pole, layer 2/3" }] },
    { "id": "184", "name": "Frontal pole, cerebral cortex", "label_values": [184],
      "color": "#268f45",
      "designations": [{ "scheme": "CCF", "code": "184", "meaning": "Frontal pole, cerebral cortex" }] }
  ]
}
```

Structure 184 is an interior node of the atlas, and it has a segment because the annotation volume labels some voxels with it directly — voxels not resolved to any of its layers. Its segment lists only those voxels. "All of the frontal pole" is a group over 184 and its descendants, defined once for the scheme. A writer that wants that union answerable from the file alone may instead list the descendants' values in 184's `label_values`; shared values make that legal.

### 8.5 A Layer Whose Zero Is "Unknown"

FreeSurfer's `aseg` has no "nothing here" class; its 0 is "Unknown." The layer has no background.

```json
{
  "version": "0.8",
  "labeling_scheme": "FreeSurferColorLUT",
  "terminologies": {
    "FreeSurferColorLUT": { "name": "FreeSurfer color lookup table", "version": "7.4" }
  },
  "segments": [
    { "id": "Unknown", "name": "Unknown", "label_values": [0], "role": "unknown" },
    { "id": "Left-Hippocampus", "name": "Left hippocampus", "label_values": [17],
      "color": "#dcd814",
      "designations": [{ "scheme": "FreeSurferColorLUT", "code": "17", "meaning": "Left-Hippocampus" }] }
  ]
}
```

### 8.6 Minimal

```json
{
  "version": "0.8",
  "segments": [
    { "id": "S1", "label_values": [1], "name": "Liver" },
    { "id": "S2", "label_values": [2], "name": "Spleen" }
  ]
}
```

---

## 9. Design Notes

**Why the scope narrowed.** Version 0.7 put groups and their claims in the file, and the design explored after it went further: peer extensions for meaning and for rendition, several renditions per file, a rule cascade, a measurement extension. Each piece answered a real need. But a duckn file's job is to let its voxels be interpreted faithfully, and to carry what the imaging formats it exchanges with carry. Measured against that, most of the new machinery was enrichment — and most of it was not even about one file: an atlas hierarchy, a house color scheme, and a cross-walk to SNOMED are properties of a labeling scheme, written once and applied to thousands of files. Putting them in every file duplicates them and freezes them at whatever they were on the day of writing. So the file keeps the segment record that DICOM SEG and `.seg.nrrd` also keep, gains the two things interpretation was missing — overlap without synthetic segments, and the difference between "nothing here" and "not evaluated" — and guarantees the handles by which everything else can point at it.

**Why `label_values` is a set, and always an array.** Through 0.6 `label_value` could be an integer, a list of integers, or a list mixing integers with segment ids; 0.7 made it one integer and moved unions into groups, which forced every overlap island to have a segment of its own and made migration synthesize segments named `label_3`. An island is not a segment. It is a cell of a Venn diagram whose whole meaning is which segments contain it, and a list of values per segment says exactly that with no extra entries. The field is plural and always an array so that there is one type and one code path: the 0.6 allowance that `1` and `[1]` were the same thing is what let a migration bug create two owners for one value. Two writers describing the same segmentation now produce the same entries.

**Why one owner per value became the special case.** Version 0.7 required that a value resolve to exactly one segment, which is what makes a label table a label table — and what made overlap need groups. With sets, the requirement would forbid the mechanism. It survives as the *definition* of the case every simple consumer wants (§5): when it holds, a layer is one labelmap and one color table with no policy needed; when it does not, draw order decides, and exporters materialize.

**Why groups left the file.** Two things seemed to need them. Overlap is handled by shared values. And a partition claim — these classes are mutually exclusive and exhaustive — is a fact about a binding that needs no group: the segments of a label-table layer are disjoint by construction, and a softmax layer is a partition of its classes by construction. What remains is hierarchy, and a hierarchy belongs to its scheme. The Allen structure graph ships with the atlas, at a version; a file that names the scheme and version has said which graph applies, and one external definition keyed by code serves every such file. A file remains interpretable alone at the level its producer asserted — the labels — and "which voxels are Isocortex" is a query over them, as a stylesheet is a presentation of them.

**Why `labeling_scheme`.** It is the smallest thing that makes the external layer work. Without it, an external hierarchy must be matched to a file by guesswork; with it, the file says "my segments are classes of this scheme at this version," each segment carries its class as an exact designation, and everything written for the scheme joins by code. It records a definition, not a process: nothing about the run, the weights, or the inputs, which are provenance.

**Why designations are exact.** A designation is relied on as identity by every consumer that reads one. The alternative considered — a `relation` on each designation, or a `mappings` field beside them — is sound, and is where inexact correspondences will be recorded; but no imaging format carries them, nothing about interpreting the voxels depends on them, and they are typically added by someone other than the producer, later. So in the file the rule is simply that a designation is exact or is not written, and laterality in schemes that lack it is reached exactly through a cross-scheme `modifier`, as DICOM does it.

**Why roles, and why two.** Background and unknown differ in what a reader may conclude: one is a negative that counts, the other is no claim and is excluded. No amount of styling recovers the distinction once both are painted with the same value, and it changes the result of every comparison and measurement, so it is interpretation and belongs in the file. It is a `role` enumeration rather than 0.7's `background` boolean so that the two are structurally exclusive. Reasons for being unknown are meanings and go where meanings go, which is what keeps the enumeration from growing.

**Why undescribed values read as unknown.** Version 0.6 called them implementation-defined, which meant a validator could not tell a legitimate implicit island from a bug. Making description a writer's obligation makes the rule checkable; giving readers a defined, conservative fallback keeps damaged files usable. Strict on write, tolerant and loud on read.

**Why `color` is a string on the segment.** DICOM SEG and `.seg.nrrd` each carry one recommended color per segment, so the file carries one. It is a CSS string because a triple of numbers is not a color until something says what space it is in, and no earlier version did; and because DICOM's CIELab *is* CSS's `lab()`, a DICOM color is transcribed losslessly rather than pushed through a conversion that implementations have disagreed about for two decades. More than one rendition, opacity and visibility as independent properties, and coloring by group are real needs and are external.

**Why `segments` order is the draw order.** Overlapping segments with one color each need an order to be viewable at all, 3D Slicer already uses list order, and a default that lives in the file means two viewers with no stylesheet agree. It is a display default and nothing else: no rule of §5 depends on order.

**Why `seg`, not `segmentation`.** The data model — layers, `source_representation`, representation conversion state — is inherited from 3D Slicer's `.seg.nrrd`, and the short name echoes that lineage. The Slicer-specific state is confined to `metadata.slicer`, so the first-class fields remain platform-neutral.

**Why `name`, not "label."** Every community this convention serves uses "label" for something different — an integer in imaging, a string in the semantic web, both in DICOM. The imaging sense cannot be moved, because "label map" and "label value" are the field's terms, so in this convention *label* means the integer exclusively, and the string is a `name`: what SKOS calls `prefLabel`, what schema.org calls `name`, what DICOM calls `SegmentLabel`. A coded entry's `meaning` is a different thing again — the terminology's rendering of a code, DICOM's `CodeMeaning`.

**Why `dicom` is separate from `designations`.** The DICOM Segmentation IOD has a specific classification structure (category → type → modifier, plus anatomic region → modifier) that does not map onto a flat list of codes. Mixing the two would either force the DICOM structure onto non-DICOM uses, as `.seg.nrrd` does, or lose the structure needed for DICOM round-tripping.

**Why `segments` is an array, not a map.** Segments have a natural order — creation order, UI order, and now draw order. An array preserves it; `id` provides stable lookup.
