# Segmentation Extension for duckn — 0.8 Draft

**Extension name:** `seg`
**Version:** 0.8 (proposed)
**Status:** Draft — not implemented. `segmentation-ext-spec.md` describes version 0.7, which is what the released code reads and writes. This document replaces it when 0.8 is implemented.

---

## 1. Purpose

This document defines the `seg` extension for the duckn convention. It replaces the `.seg.nrrd` metadata encoding — where segment properties were flattened into NRRD key/value pairs with `SegmentN_` prefixes and `~^|&`-delimited substructure — with a clean JSON representation.

### Scope

Version 0.8 draws the extension's boundary with a stated test. A field belongs in the file when:

1. a reader needs it to **interpret the voxels faithfully** — which values are which segment, which values mean "nothing here" and which mean "not evaluated"; or
2. **a reference format carries its equivalent**, so that a round trip needs it. The reference formats for this extension are the DICOM Segmentation IOD and 3D Slicer's `.seg.nrrd`: a segment's label, coded property type, algorithm type, and recommended display color; a segment's name, color, and terminology entry.

Everything else is enrichment: alternative renditions, groupings and hierarchies, inexact correspondences to further ontologies, translations, measurements. Those are real needs, and experience shows several of them are common. But they change for their own reasons, under other authorities, and most of them are properties of a *labeling scheme* rather than of one file — the hierarchy of an atlas applies to every volume labeled with that atlas. They belong in documents outside the file that reference it.

That gives the test a deliberate third clause:

3. it is **a handle by which documents outside the file find it**: a segment's `id`, and the `labeling_scheme` declaration. These interpret no voxel and no reference format carries them. They are in the file because they are what makes it possible for everything else to stay out (§7).

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

### A designated segment covers all of its concept

A segment that carries a designation (§4.1) lists **every value in its layer whose voxels are that concept** — not some of them. This matters for labeling schemes with a hierarchy. An atlas annotation volume labels each voxel with its most specific structure, so the voxels of "Frontal pole" are spread over the values of its layers *and* a value of its own, for voxels not resolved to any layer. The segment designated "Frontal pole" lists all of those values:

```json
{ "id": "184", "name": "Frontal pole, cerebral cortex", "label_values": [68, 184, 667] }
```

A designation then always determines one voxel set, whoever wrote the file: "the volume of structure 184" has one answer. The unresolved remainder — the voxels valued 184 alone — is not the concept and does not carry its designation; a writer that wants to name it gives it a segment of its own with a `name` and no designation in the scheme.

Two consequences follow. A file for a deep hierarchy holds long value lists on its broad structures, which is the price of answering region queries from the file alone. And a writer lists broader segments **before** narrower ones, so that the default draw order (§3.2 `color`) paints the specific over the general.

### Background, unknown, and undescribed values

Three kinds of voxel are not part of any structure, and they mean different things.

**Background** is a definite claim: *none of the structures this layer describes is here.* An unlabeled voxel is a negative for every segment in the layer, which is what makes it countable — a voxel labeled in one segmentation and background in another is a disagreement. It is not a claim that nothing at all is there: a liver-only segmentation's background says "not liver" and is silent about the spleen, because the file has no spleen segment to be negative about. By default a layer's background is the value 0 — a Zarr array's `fill_value` is typically 0, so 0 is what every unwritten voxel reads as. A layer whose background is another value, or deserves a name, declares a segment with `"role": "background"`. A layer in which a segment with the unknown role claims 0 has no background at all (§3.2 `role`).

**Unknown** is the absence of a claim: *this voxel was not classified.* A scan cropped mid-organ, an artifact region, a model that abstains below a confidence threshold, a manual segmentation with three of five lesions done — in each, labeling the voxels as background would assert "nothing here" where the truth is "not evaluated." A segment with `"role": "unknown"` says so. A reader excludes unknown voxels from comparisons and measurements rather than counting them either way; this is the "ignore index" of segmentation benchmarks, and for the same reason. Unknown is always explicit: if unlabeled voxels defaulted to unknown, no comparison could ever penalize over-segmentation. A writer who wants unwritten regions to read as unevaluated sets the array's `fill_value` to an unknown segment's value.

**Undescribed** values are a writer's error with a defined reading. Every value present in a layer's data must be in the layer's background value set or belong to some segment (§5), so that a reader can always answer "what is value 5?" When one is not, a validator with the data reports it, and a reader does not fail: for the purposes of comparison and measurement it treats the value as it would unknown — excluded — renders it distinctly, and surfaces the problem. Undescribed is not the unknown *role*, and nothing that selects by role matches it (§7.1). Merging it into background would silently erase something a writer painted; treating it as a structure would be a guess. A writer has two conforming ways to say what such a value is, and they mean different things: a **minimal segment**, `{"id": "v5", "label_values": [5]}` — "a structure I have not named," counted as itself — or the same with `"role": "unknown"`.

### Fractional labelmaps

When `source_representation` is `"fractional-labelmap"`, voxel values are continuous — the fraction of the voxel occupied by a segment, or the probability that it belongs to one — and the integer-equality rule above does not apply.

Each segment therefore needs its own volume of fractional values, so a fractional segmentation **must** carry a `list` axis and assign every segment a distinct `layer`. `label_values` has no work to do and is `[1]` by rule (§5). A segment may carry a `role`: the "none of the above" channel of a softmax model is a background segment, and a per-voxel abstention map is an unknown one. Shared values, the background value set, and undescribed values are defined only for binary labelmaps.

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

Optional. The segmentation's primary representation — the one edited by the user and from which others are derived.

| Value | Description |
|-------|-------------|
| `"binary-labelmap"` | Integer-valued voxels; each value identifies segments |
| `"fractional-labelmap"` | Continuous voxel values, one layer per segment |
| `"closed-surface"` | The source is a surface mesh; the array is a derived rasterization |
| `"planar-contour"` | The source is a set of planar contours; the array is derived |

Other values may appear; readers should not fail on an unrecognized one. When absent, a reader treats an integer array as a binary labelmap.

#### `labeling_scheme`

Optional. The labeling scheme or schemes this segmentation was produced under: a key of `terminologies`, or an array of distinct keys when segments are drawn from more than one scheme — a multi-task model whose layers follow different class lists.

```json
"labeling_scheme": "TotalSegmentator"
```

A labeling scheme is a coding system whose codes are the classes of a segmenter, an atlas, or a protocol: TotalSegmentator's class list at a version, the Allen CCF structure ontology, FreeSurfer's `aseg` labels. Declaring it says that a segment's class in that scheme is recorded, exactly, as one of its designations (§5), and the registry entry says *which edition* of the class list that was. A segment belongs to the scheme whose designation it carries; the array form does not assign schemes to layers.

This is the join that lets documents outside the file apply to it without per-file work. A hierarchy, a color scheme, or a cross-walk to SNOMED written once for "TotalSegmentator 2.4" applies to every file that declares that scheme (§7). It is the scheme's *definition* that is recorded — identity — and not an account of the run that applied it, which is provenance and belongs to the `provenance` extension.

A variant with a different class list — TotalSegmentator's `total` and `total_mr` tasks — is a different scheme and is registered under its own key. A hand-drawn segmentation declares no scheme.

#### `terminologies`

An object registering the coding systems used in this extension's coded entries. Each key is a short identifier for the system (the value used as `scheme`); each value is an object describing it.

```json
"terminologies": {
  "SCT": {
    "name": "SNOMED Clinical Terms",
    "uri": "http://snomed.info/sct",
    "version": "2024-09-01",
    "url_template": "http://snomed.info/id/{code}"
  },
  "TotalSegmentator": {
    "name": "TotalSegmentator class labels, task total",
    "uri": "https://github.com/wasserth/TotalSegmentator#total",
    "version": "2.4"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | no | Full human-readable name of the coding system |
| `uri` | no | A canonical identifier for the coding system, compared as a string. It is what identifies the system across files: a key is local to its document (§7.1) |
| `version` | no | Version of the coding system in use, compared as an exact string |
| `url` | no | URL for the coding system's browser, specification, or landing page |
| `url_template` | no | Template for concept URLs. The substring `{code}` is replaced with a coded entry's `code` |

A writer **must** register every scheme it uses in `designations` or `dicom` entries, and should give each a `uri`. A scheme named by `labeling_scheme` must have both `uri` and `version`, since those are what an external document is matched against. A reader **must not** reject a file for an unregistered scheme, and must not assume the registry enumerates every scheme in the file: strict on write, tolerant on read.

#### `segments`

Required. An array of segment objects (§3.2). May be empty for an empty segmentation (§2).

The order of the array does two jobs and no others. It is the segment's ordinal position in format mappings — the `N` in `SegmentN_*` (§6.1), the order of a DICOM `SegmentSequence` (§6.2). And it is the **recommended draw order**, later over earlier, where segments overlap (§3.2 `color`). No rule of §5 depends on it.

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

An `id` is a **token**: one or more characters from `A`–`Z`, `a`–`z`, `0`–`9`, `_`, and `-`. Ids are case-sensitive. The grammar is stated positively because an id is embedded in references (§7.1), where a space, a `%`, or any of `.`, `/`, `#` would make a reference unparseable. Ids in practice are tokens already — `997`, `Segment_1`, `ctx-lh-bankssts`, `Left-Hippocampus`. Free text — a DICOM `SegmentLabel`, a name in any script — belongs in `name`; converters derive a token (§6).

#### `name`

The human-readable display string, as the author gave it, in any script. It is independent of any ontology: it may echo a designation's `meaning`, be a local nickname, or be the only identity a segment has — "suspect lesion #3" has no code and needs none.

```json
"name": "Right kidney"
```

In this convention the string is a *name*, never a *label*: "label" means an integer voxel value throughout (§9). DICOM's `SegmentLabel`, which is a string, maps here.

#### `label_values`

Required. The integer voxel values belonging to this segment, as a non-empty array of distinct integers **in ascending order**. The segment's voxels are all voxels in its layer whose value is in the array.

```json
"label_values": [1, 3]
```

It is always an array, even for one value: `[1]`, never `1`. A value may appear in more than one segment of the same layer; that is how overlap is represented (§2). The **effective value set** of a segment is the set of *(layer, value)* pairs formed from its `layer` and each entry — a value only identifies voxels within a layer.

A segment with no `role` must not list a value of its layer's background value set (§5).

#### `role`

Optional. One of:

| Value | Meaning | In a comparison or measurement |
|-------|---------|-------------------------------|
| `"background"` | "none of the described structures is here" | counted, as the negative class |
| `"unknown"` | "not classified" | excluded |

Omit for a segment that is a structure. At most one segment per layer has the background role; any number may be unknown. A layer's **background value set** is its background segment's `label_values`; when the layer has no background segment it is `[0]`, unless an unknown segment lists 0, in which case it is empty and the layer has no background — FreeSurfer's `aseg`, whose 0 is "Unknown," is such a layer. A layer with no background has no negative class: a comparison over it cannot count a false positive against the unlabeled region, and a reader should say so rather than report a score as though it could.

The *reason* a region is unknown — artifact, outside the field of view, abstained — is a meaning and goes in `name` or `designations`. That is what keeps this field at two values.

#### `layer`

The zero-based index of the layer (position along the `list` axis) holding this segment's values; a non-negative integer. Omit for segmentations with one layer; an absent `layer` means layer 0. When an array has more than one `list`-kind axis, `layer` indexes the first.

#### `extent`

The bounding box of the segment's non-empty region, as `[min_i, max_i, min_j, max_j, min_k, max_k]` in voxel coordinates, both bounds inclusive, over the array's three spatial axes in storage order.

```json
"extent": [45, 102, 30, 98, 12, 55]
```

`extent` is a cached index, kept because `.seg.nrrd` carries it. It is advisory: a reader must not rely on it for correctness, and any operation that changes the grid or the voxels — crop, resample, reorient, edit — must recompute it or drop it.

#### `color`

Optional. The producer's **recommended display color**, as a CSS color string. It is a recommendation in the sense DICOM gives the word: a viewer may override it, and a rendition applied from outside the file takes precedence over it.

```json
"color": "#dd8265"
```

A string is used rather than a triple of numbers because the syntax states the color space: `#dd8265` is sRGB by definition, and `lab(64.06 33.88 31.52)` is the same color as CIELab under D50, where `[0.87, 0.51, 0.40]` could be sRGB, linear light, or anything else. No earlier version of this extension said which.

Three forms are written, and every reader accepts all three:

| Form | Written when |
|------|--------------|
| `#rrggbb` | the color is sRGB and fits 8 bits per channel — the common case |
| `lab(L a b)` | the color's source is CIELab (§6.2), so that it is transcribed rather than converted |
| `rgb(r g b)` | the color is sRGB with more precision than 8 bits |

Writers do not write other CSS color forms: a conforming reader is not required to understand them, and a color a reader cannot parse is treated as absent. An absent color means the viewer chooses. CSS syntax admits an alpha component; writers should not write one, since no reference format carries it and opacity is a rendition's business (§7.2), and readers may ignore it. Conversion between color spaces for display is CSS Color Level 4's, and this extension defines none of its own.

**Draw order.** Where two segments cover the same voxel — a shared value, or the same position in two layers — the recommended draw order is the order of `segments`, later over earlier. Segments with a `role` are drawn beneath all others, whatever their position. A color table with one entry per value gives a shared value the color of the **last segment listing it that has a color**.

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
| `scheme` | yes | Key identifying the coding system, registered in `terminologies` (§3.1) |
| `code` | yes | The concept identifier within that coding system |
| `meaning` | no | Human-readable name of the concept, as of the registered terminology version. Recommended when known |
| `modifier` | no | A coded entry qualifying this one, typically laterality. One level of nesting. Its `scheme` need not match the base entry's |

#### `dicom`

The DICOM Segmentation IOD's per-segment content that has no other field: the classification structure (category, type, type modifier, anatomic region, region modifier) and the algorithm and tracking attributes. See §4.2. Present only when DICOM SEG interoperability is needed.

#### `metadata`

An open-ended object for application-specific per-segment metadata, keyed by source application or standard. The well-known key `slicer` holds `name_auto_generated`, `color_auto_generated`, and `tags`; `dicom` holds DICOM segment attributes this extension has no field for (`SegmentDescription`, further modifier items — §4.2); `duckn` holds what a migration preserved (§6.3).

---

## 4. Segment Identity

Segment identity has two parts:

1. **Designations** — coded entries saying "this segment is concept *X* in system *Y*." The primary mechanism for interoperable identity.
2. **DICOM content** — the structured classification and the algorithm attributes required by the DICOM Segmentation IOD. Needed only for DICOM round-tripping.

### 4.1 Designations

A segment is a real anatomical or pathological thing that different communities identify using different coding systems — a kidney is SNOMED 64033007, FMA 7203, and TA2 5765 simultaneously. Each entry captures one such identification; the first is preferred. When `labeling_scheme` is declared, one of them is the class the segment was produced as.

#### Designations are exact

Every consumer relies on one contract: a designation *is* the segment. A DICOM exporter takes the first designation as the property type; a lookup by code finds the segment; a stylesheet keyed by code paints it. So a designation is written only when the identification is exact, and the segment covers all of that concept in its layer (§2). A segment that is merely *near* a concept, or narrower than one — a mass that may be a neoplasm; "kidney" for a left kidney in a scheme that cannot say left; the unresolved remainder of an atlas structure — does not get that concept as a designation.

Exact identifications in several coding systems are all designations and all belong in the file, whoever added them and when. What is external is the *inexact* correspondence — close, broader, narrower, related — which a designation cannot express without breaking the contract (§7.2).

Two segments may carry the same designation: the same structure in two layers, or a structure and a migrated duplicate. A lookup by code therefore returns a set of segments, and a consumer that needs one takes the first in `segments` order.

Coverage is a different question from identity. A liver segment from a scan cropped mid-organ is exactly liver — every voxel in it is liver, and it lists every liver value in the layer — and is designated so. That it is not *all of the patient's* liver is said by the unknown role on the region that was not evaluated, never by weakening the designation.

#### Codes are authoritative; meanings are renderings

The identity carried by a designation is `scheme` + `code` (+ `modifier`). The coding system itself — at the version recorded in `terminologies` — is the source of truth for what that code means. `meaning` is a snapshot embedded for the reader's convenience: it lets a viewer display something sensible without a terminology service, it supplies DICOM's required CodeMeaning on export, and it is a human-auditable cross-check. **If the embedded `meaning` disagrees with the coding system, the code wins.** Writers should include `meaning` when they know it; readers must not treat it as identity.

Concept URLs are not embedded per entry; readers derive them from the registry's `url_template`.

#### Post-coordination via `modifier`

Many coding systems express "left kidney" as a base concept plus a qualifier, and some — Terminologia Anatomica among them — have no laterality at all. The `modifier` field carries the qualifier as a nested coded entry, and **its scheme need not match the base entry's**: TA2 "Kidney" qualified by SNOMED "Left" is an exact identification of a left kidney. This mirrors DICOM's modifier code sequences, whose laterality codes are SNOMED whatever the type code's scheme. Modifiers nest one level only. A writer prefers a pre-coordinated code where the scheme has one, and post-coordination otherwise; both are exact.

#### Relationship to `name`

`name` is what is shown; `designations` are what is used for computation, interoperability, and lookup. They are independent.

### 4.2 DICOM Content

```json
"dicom": {
  "category": { "scheme": "SCT", "code": "49755003", "meaning": "Morphologically abnormal structure" },
  "type": { "scheme": "SCT", "code": "4147007", "meaning": "Mass" },
  "anatomic_region": { "scheme": "SCT", "code": "23451007", "meaning": "Adrenal gland" },
  "anatomic_region_modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" },
  "algorithm_type": "SEMIAUTOMATIC",
  "algorithm_name": "GrowCut"
}
```

| Field | Needed for DICOM SEG export | Description |
|-------|-----------------------------|-------------|
| `category` | yes | Segmented Property Category, a coded entry |
| `type` | yes | Segmented Property Type, a coded entry |
| `type_modifier` | no | Qualifier on the type, a coded entry |
| `anatomic_region` | no | Anatomic region, a coded entry |
| `anatomic_region_modifier` | no | Qualifier on the region, typically laterality |
| `algorithm_type` | yes | `"AUTOMATIC"`, `"SEMIAUTOMATIC"`, or `"MANUAL"` — DICOM's Segment Algorithm Type, which is required of every segment |
| `algorithm_name` | unless `MANUAL` | DICOM's Segment Algorithm Name |
| `tracking_id`, `tracking_uid` | no | DICOM's Tracking ID and Tracking UID, which identify the same finding across objects |

The coded entries have the same shape as a designation, minus `modifier`. Every field is optional, so a partial record can be kept as it becomes known. A DICOM writer that finds a needed field missing takes it from its caller or **fails**; it must not invent one. `"AUTOMATIC"` written for a hand-drawn segment is a false statement in a clinical record.

DICOM permits several items in its type-modifier sequence and in its anatomic-region sequence; this extension holds one of each. An importer keeps the first and preserves the rest under `metadata.dicom`, and reports that it did.

Classification entries use SNOMED CT codes by convention, but the scheme is always explicit — legacy files may carry SRT or DCM codes. When writing DICOM SEG, CodeMeaning is required: writers derive it from the entry's `meaning`, falling back to the segment's `name`, and fail if neither is available.

### 4.3 Relationship Between Designations and DICOM

The two are independent; the same SNOMED concept may appear in both. `designations` answers "what is this structure, in any ontology?" and `dicom` answers "how is this segment described in a DICOM Segmentation IOD?" A DICOM writer uses `dicom`; lookup and cross-referencing use `designations`.

### 4.4 Absence and Omission

Following the convention's "absent means unknown" principle:

- If a segment has no designations, omit `designations`. Do not include an empty array.
- If a coded entry's `meaning` is unknown, omit it. Do not use an empty string or `null`.
- If a segment has no DICOM content, omit `dicom`; within it, omit what does not apply.
- If a segment has no role, omit `role`. If it has no recommended color, omit `color`.
- If no coded entries are used, omit `terminologies`.

---

## 5. Consistency Rules

A segment's **effective value set** is the set of *(layer, value)* pairs defined in §3.2. Two segments may have the same effective value set: identity is the `id`, never the voxels.

**Structure**

1. The length of `segments` is independent of any axis size.
2. `layer`, when present, is a non-negative integer. Where a segment specifies a `layer`, there must be a `list`-kind axis in the array, and `layer` must be a valid index into it. A segment in an array with no `list` axis omits `layer` rather than specifying 0.
3. Where a `kind` constraint requires a specific axis size (from the duckn convention), the corresponding `shape` element must match.

**Identity**

4. `id` is unique across `segments` and is a token as defined in §3.2.
5. A writer registers in `terminologies` every `scheme` used in `designations` or `dicom`. Every key named by `labeling_scheme` is registered with a `uri` and a `version`.
6. When `labeling_scheme` is declared, a segment carries **at most one** designation in each declared scheme, and a segment with no `role` **should** carry one. A segment that carries none is one the scheme did not produce — a remainder, a structure added by hand — and documents keyed by the scheme will not find it.
7. `role`, when present, is `"background"` or `"unknown"`.

**Values**

8. `label_values` is a non-empty array of distinct integers in ascending order. Booleans are not integers.
9. *(Binary labelmaps.)* At most one segment per layer has `"role": "background"`. A segment with no `role` must not list a value in its layer's background value set (§3.2 `role`).
10. *(Binary labelmaps.)* A value listed by a segment with a `role` is not listed by any other segment of the same layer: "nothing here" and "not evaluated" do not overlap a structure, or each other.
11. *(Binary labelmaps.)* Every value present in a layer's voxel data is in the layer's background value set or in some segment's `label_values`. This is a writer's obligation and the one rule that needs the data. A reader meeting a violation excludes the value from comparison and reports it (§2).
12. *(Fractional labelmaps.)* The array has a `list` axis; every segment has a distinct `layer`; every segment's `label_values` is `[1]`. At most one segment has the background role.

Rules 1, 4–10 and the metadata clauses of 12 constrain the metadata alone; rules 2, 3 and the axis clause of 12 additionally require the array's shape and axes; rule 11 requires the voxel data, which this extension never requires a reader to load.

**The label table.** A layer in which every segment *with no role* has exactly one entry in `label_values`, and no value is shared, is a classic label table: one row per value, one color per value, exportable as a single labelmap with no relabeling. This is a property a reader or exporter *checks*, not a constraint on files; version 0.7 imposed it on every file, and 0.8 makes it the special case.

**Disjointness.** Two segments in the same layer are disjoint exactly when their `label_values` do not intersect, which is checkable from metadata. Two segments in different layers may overlap whatever their values; whether they do is a fact about the data.

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
| `SegmentN_LabelValue` (one integer) | `segments[n].label_values` (one-entry array) |
| `SegmentN_Layer` | `segments[n].layer` (integer; omitted when 0) |
| `SegmentN_Extent` | `segments[n].extent` (6-element array) |
| `SegmentN_Tags` (minus TerminologyEntry and `duckn.role`) | `segments[n].metadata.slicer.tags` (object) |
| `SegmentN_Tags` `duckn.role` | `segments[n].role` |
| `SegmentN_Tags` TerminologyEntry — category/type/modifier/region | `segments[n].dicom` (object) |
| `SegmentN_Tags` TerminologyEntry — type code + type modifier | `segments[n].designations` (first entry, modifier included) |
| `SegmentN_Tags` TerminologyEntry — context names | Omitted (application state) |
| — (no `.seg.nrrd` equivalent) | `labeling_scheme`, further `designations`, `dicom.algorithm_*` |

**Ids.** `SegmentN_ID` is used as the `id` when it is a token (§3.2) and unique. Otherwise the converter derives one — each run of characters outside the token alphabet becomes `_`, an empty result becomes `Segment_<N>`, and a collision takes the suffix `_2`, `_3`, … — and keeps the original under `metadata.slicer.id` so an export can restore it.

**Color.** On import each float channel `x` becomes `round(255 * x)` and the color is written as hex. On export each channel is written as `n / 255` formatted with C's `%.6g`, which is how 3D Slicer writes it, so a color that came from Slicer returns byte for byte. A `lab()` or `rgb()` color is converted to sRGB by CSS Color 4's conversion, clipped, and written the same way.

**Overlap on export.** 3D Slicer writes one `LabelValue` per segment and represents overlap with layers. A `.seg.nrrd` cannot hold a multi-valued segment or a shared value, so an exporter **materializes**: it writes new voxel data, not only new metadata. Segments with no role are taken in `segments` order, and each is placed in the first layer, counting from 0, in which it shares no voxel with a segment already placed there; within that layer it is written with a single new label value, its voxels being the union of its old values'. The assignment is deterministic, so two exporters agree. The voxels of every segment are preserved; the island decomposition is not, and does not need to be. A segmentation with a deep hierarchy of nested value lists (§2) exports to one layer per level of nesting, which can be many full-size volumes; an exporter should warn. An unmodified file with `legacy` key/values is replayed verbatim instead.

**Roles on export.** The format's background is value 0 and has no segment. A role-bearing segment that lists 0 — a background, or FreeSurfer's "Unknown" — is therefore not written, and its voxels stay 0; if it carried a `name` or designations, the exporter reports their loss. Any other role-bearing segment is written as an ordinary segment with the tag `duckn.role` set to its role, which an importer reads back, so the role survives a round trip through Slicer.

**Parsing notes** (unchanged from 0.7): accept either the `Master` or `Source` representation key; normalize representation names to kebab-case; split pipe-delimited lists and drop empty elements; strip the `Segmentation.` prefix from tag keys; treat literal `\n` escape sequences in `ConversionParameters` descriptions as newlines; drop a `Layer` of 0; do not default a missing `LabelValue` to 0, assign an unused value instead; keep a two-part coded entry whose meaning is empty.

### 6.2 DICOM Segmentation

| DICOM attribute | duckn `seg` extension field |
|---|---|
| `SegmentNumber` | the entry of `label_values` in a `LABELMAP`; position in `segments` otherwise |
| `SegmentLabel` | `segments[n].name` |
| `SegmentDescription` | `segments[n].metadata.dicom.SegmentDescription` |
| `SegmentedPropertyCategoryCodeSequence` | `segments[n].dicom.category` |
| `SegmentedPropertyTypeCodeSequence` (+ first modifier item) | `segments[n].dicom.type`, `type_modifier`; and the first of `designations` |
| `AnatomicRegionSequence` (first item, + first modifier item) | `segments[n].dicom.anatomic_region`, `anatomic_region_modifier` |
| `SegmentAlgorithmType`, `SegmentAlgorithmName` | `segments[n].dicom.algorithm_type`, `algorithm_name` |
| `TrackingID`, `TrackingUID` | `segments[n].dicom.tracking_id`, `tracking_uid` |
| `RecommendedDisplayCIELabValue` | `segments[n].color`, as `lab()` |
| `SegmentationType` `BINARY` / `FRACTIONAL` / `LABELMAP` | `source_representation`, and the layer layout |
| `SegmentsOverlap` | computed on export, below; not stored |
| `PixelPaddingValue` of a `LABELMAP` | the background segment's value |

**Ids.** `SegmentLabel` is free text and always becomes the `name`. The `id` is `Segment_<SegmentNumber>`, which is unique by DICOM's own rule. DICOM has no field for a duckn `id`, so an id does not survive export to DICOM and re-import (§7.1).

**Layer layout on import.** A `LABELMAP` is one layer whose values are the Segment Numbers. A `BINARY` or `FRACTIONAL` object gives each segment its own frames; an importer may place each segment in its own layer with `label_values: [1]`, or, for `BINARY` segments it has verified do not overlap, combine them into one layer with distinct values. Both are conforming.

**Choosing a type on export.** DICOM's `LABELMAP` holds exactly one labelmap, in which a pixel can represent only one segment. A segmentation exports as `LABELMAP` when it has **a single layer that is a label table** (§5). Anything else — shared values, or more than one layer — exports as `BINARY`, whose segments are permitted to overlap. No segment is dropped and none is refused on account of overlap.

- **`LABELMAP`.** Segment Numbers need not be contiguous or start at 1 in this type; the pixel values *are* the Segment Numbers. So label values are **preserved**: an atlas or a FreeSurfer volume exports with its numbers intact, provided they are non-negative and fit the 8- or 16-bit unsigned pixel type (otherwise the exporter renumbers and reports it). DICOM requires every pixel value present to be described by a segment item, including the background: the exporter writes an item for the layer's background value — from the background segment's `name` and codes, or a generic one — and sets `PixelPaddingValue` to it, which is DICOM's statement that the value is background. A segment with the unknown role is written as an ordinary item; DICOM has no equivalent, and the exporter reports the loss.
- **`BINARY`.** Segment Numbers must start at 1 and increase by 1, so they are positions in `segments`, counting only segments written. Each segment's frames are the union of its values' voxels. A background segment is not written: in this type the absence of any segment is the background. An unknown segment is written as an ordinary segment, and reported.
- **`FRACTIONAL`.** As `BINARY`, with one segment per layer. `MaximumFractionalValue` follows from the array's `value_transforms`; `SegmentationFractionalType` (`PROBABILITY` or `OCCUPANCY`) is a distinction this extension does not record, and the exporter takes it from its caller or from `metadata.dicom`.

`SegmentsOverlap` is `NO` for a `LABELMAP`; for the other types it is `YES` when any two written segments share a value in a layer, `NO` when all are in one layer with disjoint values, and `UNDEFINED` otherwise, since overlap across layers is a fact about the data (§5).

`SegmentLabel` is required by DICOM: it is the segment's `name`, else the `meaning` of its first designation, else its `id`. A `LABELMAP` whose photometric interpretation is `PALETTE COLOR` takes its colors from a palette and is out of scope.

**Color.** `RecommendedDisplayCIELabValue` is specified in "PCS-Values," encoded "in the same form as the PCS in ICC Profiles" (PS3.3 C.10.7.1.1); the ICC profile connection space uses a D50 white point (PS3.4 N.2.2.2), and CSS Color 4 defines `lab()` relative to the same D50 white. The encoding is ICC version 4's 16-bit PCSLab. The three values are therefore transcribed, not converted: `L = v × 100 / 65535`, and `a = v × 255 / 65535 − 128`, likewise `b`, written as `lab(L a b)` with at least three decimal places, which recovers the 16-bit integers exactly (four is safer). `[39330, 30580, 41942]` becomes `"lab(60.014 -9.012 35.198)"`. Export converts the segment's color to `lab()` by CSS Color 4's conversions and scales to 16 bits. When the attribute is absent the color is left absent.

dcmqi, and 3D Slicer through it, compute these values from sRGB without the chromatic adaptation from D65 to D50 that the PCS requires. Many existing files therefore carry numbers that, read correctly, are not the color their author saw. The difference is zero for grays and small for muted colors, but reaches roughly 18 of 255 levels in a channel for saturated greens and yellows. A converter **transcribes the numbers as written**: the file says what it says. Compensation — reading the values as D65-referenced Lab when `Manufacturer` or `SoftwareVersions` identify a producer known to write them that way — is a *display-time* option of a reader, off by default, and is never written back into `color`: a compensated value re-exported to DICOM would be a third number with nothing recording how it was reached.

### 6.3 Reading Older Files

A reader for 0.8 that accepts older files migrates on load. The migration is defined from each older version's own shape; an implementation may chain through intermediate versions provided the result is the same.

| Older shape | 0.8 shape |
|---|---|
| `label_value: 5` (0.7; 0.6 scalar) | `label_values: [5]` |
| `label_value: [3, 1]` (0.6 list of integers) | `label_values: [1, 3]`, sorted |
| `background: true` (0.7) | `role: "background"` |
| an `id` that is not a token | derived as in §6.1, the original kept under `metadata.duckn.id`, and the change **reported**, since references to the old id no longer resolve |
| `color: [r, g, b]` floats | `#rrggbb`, each channel `round(255 * x)`, **read as sRGB** — the first time that assumption is written down |
| `display` (multilingual names) | kept under `metadata.duckn.display`; translations are now external (§7.2) |
| a group — 0.7 `members`, or 0.6 string entries in `label_value` — whose effective values lie in one layer | a segment whose `label_values` is that set **minus any value listed by a role-bearing member**, keeping the group's `id`, `name`, `color`, `designations`, and `dicom` |
| a group whose effective values span layers, or whose members do not resolve or form a cycle | no 0.8 form; reported and omitted |
| `disjoint`, `exhaustive` (0.7) | dropped; reported when a claim is discarded |
| 0.5 `identifiers`, `metadata.dicom` classification, Slicer fields | as in 0.6: `designations`, `dicom`, `metadata.slicer` |

**Order.** 0.7 resolved a value's color from its leaf first and a containing group only as a fallback; 0.8 gives a value the color of the last segment listing it. To preserve the picture, segments migrated from groups are moved to the **front** of `segments`, ordered by decreasing size of their value set and otherwise in their original order, so that the broadest structure is drawn first and every leaf is drawn over the groups that contain it.

**What a migration cannot decide, and reports.**

- A 0.7 `background: true` may have meant "not evaluated": 0.7 offered FreeSurfer's "Unknown" as its example of a background, and 0.8 calls that region unknown (§8.5). Nothing in a 0.7 file distinguishes the two, so the role migrates as `background` and a background segment that has a `name` is reported for review.
- A migrated group lists all of its concept's values, as §2 requires. A leaf that carries the **same designation** as a migrated group was the group's unresolved remainder; its designation is now inexact. The migration reports the pair and leaves the leaf as it is.
- Leaves that an earlier migration synthesized for 0.6 islands (`label_3`, named "label 3") are kept. They cannot be told from segments an author wrote, a redundant single-value segment violates nothing, and deleting one could leave a value undescribed.
- Colors that a pre-0.8 DICOM import computed from CIELab were converted with a D65 white point (§6.2), and the original values are not recoverable from the file.

Migration from 0.6 is nearly the identity, because 0.8's `label_values` is 0.6's list of integers made uniform. A 0.7 hierarchy survives as segments whose value lists are the transitive union of their descendants', which is what §2 asks of a natively written file too: the voxels of "Isocortex" are recoverable from the file alone. The file is *larger* for it — a deep atlas turns a few thousand member ids into many more integers — and what is lost is the tree itself and the partition claims, which belong to the atlas and are supplied from outside (§7.2).

---

## 7. References From Outside the File

### 7.1 What the File Guarantees

Renditions, groupings, inexact correspondences, translations, and measurements live in documents outside the file. For those documents to be possible, this extension guarantees the following.

**Ids, and how far they are stable.** A segment's `id` is unique and is a token (§3.2). It does not change when a segment is renamed, when `segments` is reordered, or when the store is rewritten by a duckn writer. It is **not** preserved across conversion to a format with no field for it: a round trip through DICOM loses it (§6.2); one through `.seg.nrrd` keeps it, since that format has an id of its own (§6.1).

**The reference syntax.** The qualified name of a segment is `seg.segments.<id>`: an extension name, a collection, and an id, separated by dots. This is the only form defined; it navigates by id and never by array index, and every other dotted form is reserved. Where a reference must also name the array, the Zarr path comes first and `#` separates the two — `study/seg#seg.segments.liver`. Slashes are Zarr's and dots are duckn's, and `#` is needed because a Zarr node name may itself contain dots. The Zarr path is resolved relative to the Zarr node that holds the referring document; a leading `/` means the store root. A document that lives outside any store identifies the store it applies to by means of its own, which this extension does not define.

**What is visible.** A segment's `layer` and `label_values` — its effective value set — are part of what an external document may rely on. They are the extent over which an external grouping is a union, over which disjointness is decided (§5), and over which a rendition is flattened to a color table.

**Portable keys.** An id means something only in its own file. Three things mean the same in every file, and an external document may select by them with no file in hand:

- a **designation**. A key of scheme and code matches a segment carrying a designation with that scheme and code *at any position* in `designations`, whatever its modifier; a key that includes a modifier matches only an equal modifier. Matching is equality of codes, with no reasoning over a terminology's hierarchy.
- a **role** — `background` or `unknown`. A role-bearing segment is also selectable by its id and by any designation it carries, but is never an implicit member of a grouping defined over structures. The default background, value 0 of a layer with no background segment, has no segment and no id and can be selected only by role.
- **undescribed** — values present in the data that no segment lists (§2). This key is data-dependent: it can be evaluated only by reading the voxels, and a consumer working from metadata alone treats it as matching nothing. It is never matched by the `unknown` role.

**Scheme identity.** A scheme key such as `SCT` is a name local to one document; one file's `SCT` is another's `SNOMED`. Two keys name the same coding system when their registrations have the same `uri` (§3.1). A consumer reconciling keys by any weaker evidence — equal `name`, equal `url` — may do so and must report that it did.

**Scheme version.** `labeling_scheme` and its registered `version` identify which documents written for a scheme apply to this file. An external document states the versions it applies to, and the check is exact string equality against that list: `"2.4"` and `"2.4.0"` are different versions. A document applied to a version it does not list is reported as such, not silently applied.

**Stale references are reported.** An external document naming an id the file does not have has matched nothing, and that is an error to report. A code that no segment carries is normal — a cropped scan has no left kidney — and a checker reports which members of a grouping were absent rather than failing.

**The file is never modified** by applying an external document. A writer that embeds a result — a color taken from a stylesheet, written into `color` — has made a new recommendation, which is thereafter the file's own.

### 7.2 Notes on External Documents

*This section is not normative. The external documents are the part of the design most in flux, and their specification is deferred; these notes record what has been settled so far and where the drafts are.*

- **Renditions.** Several named renditions per file, a default, CSS-like rules (selector plus declaration block) with a cascade, `color` / `opacity` / `display` properties, explicit paint order, flattening to a color table, and image windows. `rendition-ext-spec.md` drafts this as an in-file extension; under the present scope it is source material for the external stylesheet format, and the file's own `color` values are the floor it cascades over — viewer, then file, then stylesheet. The recommended windows of a grayscale image have an in-file home of their own, `presentation-extension.md`.
- **Groups and hierarchy.** A group has an id, a name, designations, and members given as selectors — normally designation codes in a labeling scheme, so that one definition of TotalSegmentator's groupings or the Allen structure graph serves every file that declares the scheme; by segment id for an ad hoc group in one study; or another group. Because a designated segment covers all of its concept (§2), a hierarchy document adds the *tree* — which structure is a child of which — and need not reconstruct any structure's voxels. Claims of `disjoint` and `exhaustive` live on the group. `disjoint` is checkable within a layer from value sets (§5). `exhaustive` generally is not checkable from one file: a member that matches nothing may be absent from the anatomy, cropped out of the scan, or missed, and nothing in the file says which.
- **Inexact correspondences.** `closeMatch`, `broadMatch`, `narrowMatch`, and `relatedMatch`, keyed by relation with lists of coded entries, in the manner of SKOS. `semantic-ext-spec.md` drafts these, with the conceptual model — entities, bindings, the three kinds of meaning — that describes the external layer, where things other than segments (fiducials, class ranks) would be bound to the same vocabulary. Exact identifications are designations and stay in the file (§4.1).
- **Translations** of names, per language.
- **Measurements.** DICOM puts these in a separate object, the structured report, and so does this design. Records of a coded concept, a value, a UCUM unit, and one segment or a pair; checkable where recomputable from the arrays referenced.
- **Scope of a document.** One external specification with sections is preferred to several, since every section shares the targeting mechanism of §7.1. The convention defines duckn metadata only on arrays; placing it on a Zarr *group*, as a home for documents that span the group's arrays with references that may not leave the group, is a change to the convention and is not assumed here.

---

## 8. Examples

### 8.1 Non-Overlapping Labelmap

Two kidneys from a model with a declared labeling scheme, each identified exactly in three systems.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "labeling_scheme": "TotalSegmentator",
  "terminologies": {
    "SCT": { "name": "SNOMED Clinical Terms", "uri": "http://snomed.info/sct",
             "version": "2025-03", "url_template": "http://snomed.info/id/{code}" },
    "TA2": { "name": "Terminologia Anatomica 2nd Edition",
             "uri": "https://ta2viewer.openanatomy.org" },
    "TotalSegmentator": { "name": "TotalSegmentator class labels, task total",
             "uri": "https://github.com/wasserth/TotalSegmentator#total", "version": "2.4" }
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
        "type_modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" },
        "algorithm_type": "AUTOMATIC",
        "algorithm_name": "TotalSegmentator"
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

The layer is a label table (§5) and exports to a DICOM `LABELMAP` with values 2 and 3 intact. TA2 has no laterality, and the cross-scheme modifier makes its designation exact all the same.

### 8.2 Overlap by Shared Values, with an Unknown Region

A liver, a lesion that partially overlaps it, and a region degraded by motion that was not evaluated. Values: 1 liver only, 2 lesion only, 3 both, 9 artifact.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "terminologies": {
    "SCT": { "name": "SNOMED Clinical Terms", "uri": "http://snomed.info/sct", "version": "2025-03" }
  },
  "segments": [
    {
      "id": "liver", "name": "Liver", "label_values": [1, 3], "color": "#dd8265",
      "designations": [{ "scheme": "SCT", "code": "10200004", "meaning": "Liver" }]
    },
    {
      "id": "tumor", "name": "Suspect lesion #3", "label_values": [2, 3], "color": "#cc3333",
      "designations": [{ "scheme": "SCT", "code": "4147007", "meaning": "Mass" }]
    },
    {
      "id": "artifact", "name": "Motion artifact, not evaluated",
      "label_values": [9], "role": "unknown", "color": "#808080"
    }
  ]
}
```

Value 3 is listed twice and has no entry of its own. The lesion comes after the liver, so it is drawn over it, and a one-color-per-value table gives value 3 the lesion's color. The artifact has a role and is drawn beneath both. The lesion is exactly a mass; that it may be a neoplasm is a weaker statement and is not a designation. Background is 0 by default and needs no segment. A Dice score against another reader's segmentation excludes the voxels at 9. The file does not export as a `LABELMAP` — value 3 is shared — and goes to DICOM as `BINARY`, or to `.seg.nrrd` as two layers.

### 8.3 Overlap by Layers

The same liver and lesion, authored independently in two layers of an array with a `list` axis. Both use value 1, which is unrelated between layers. Each layer is a label table, but there are two of them, so a DICOM export is `BINARY`.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "segments": [
    { "id": "liver", "name": "Liver", "label_values": [1], "layer": 0, "color": "#dd8265" },
    { "id": "tumor", "name": "Suspect lesion #3", "label_values": [1], "layer": 1, "color": "#cc3333" }
  ]
}
```

### 8.4 An Atlas Under a Labeling Scheme

An excerpt of a whole-brain mouse atlas whose voxel values are Allen CCF structure ids. Structure 184, the frontal pole, is an interior node of the atlas with two layers shown, and the annotation volume also labels some voxels 184 directly — voxels not resolved to either layer.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "labeling_scheme": "CCF",
  "terminologies": {
    "CCF": { "name": "Allen Mouse Brain Common Coordinate Framework, structure ontology",
             "uri": "https://atlas.brain-map.org/ccf/structure-graph", "version": "3" }
  },
  "segments": [
    { "id": "184", "name": "Frontal pole, cerebral cortex", "label_values": [68, 184, 667],
      "color": "#268f45",
      "designations": [{ "scheme": "CCF", "code": "184", "meaning": "Frontal pole, cerebral cortex" }] },
    { "id": "68", "name": "Frontal pole, layer 1", "label_values": [68],
      "color": "#2ea152",
      "designations": [{ "scheme": "CCF", "code": "68", "meaning": "Frontal pole, layer 1" }] },
    { "id": "667", "name": "Frontal pole, layer 2/3", "label_values": [667],
      "color": "#37b360",
      "designations": [{ "scheme": "CCF", "code": "667", "meaning": "Frontal pole, layer 2/3" }] }
  ]
}
```

The segment designated 184 covers **all** of the frontal pole (§2): its own value and its layers'. It is listed first, so the layers are drawn over it and only the unresolved voxels show its color. Those voxels need no segment of their own — value 184 is described — though a writer could add `{"id": "184-unresolved", "name": "Frontal pole, unresolved", "label_values": [184]}`, with no CCF designation, to name them. That 68 and 667 are *children* of 184 is the atlas's structure graph; it is the same for every volume labeled with CCF version 3 and is supplied from outside, keyed by these codes (§7.2).

### 8.5 A Layer Whose Zero Is "Unknown"

FreeSurfer's `aseg` has no "nothing here" class; its 0 is "Unknown." The layer has no background, so a comparison over it has no negative class (§3.2 `role`).

```json
{
  "version": "0.8",
  "labeling_scheme": "FreeSurferColorLUT",
  "terminologies": {
    "FreeSurferColorLUT": { "name": "FreeSurfer color lookup table",
                            "uri": "https://surfer.nmr.mgh.harvard.edu/fswiki/FsTutorial/AnatomicalROI/FreeSurferColorLUT",
                            "version": "7.4" }
  },
  "segments": [
    { "id": "Unknown", "name": "Unknown", "label_values": [0], "role": "unknown" },
    { "id": "Left-Hippocampus", "name": "Left hippocampus", "label_values": [17],
      "color": "#dcd814",
      "designations": [{ "scheme": "FreeSurferColorLUT", "code": "17", "meaning": "Left-Hippocampus" }] }
  ]
}
```

On export to `.seg.nrrd` the unknown segment is not written and its voxels stay 0 (§6.1). On export to a DICOM `LABELMAP`, 0 and 17 are preserved as Segment Numbers and the unknown region becomes an ordinary segment item, with the loss reported (§6.2).

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

**Why the scope narrowed.** Version 0.7 put groups and their claims in the file, and the design explored after it went further: peer extensions for meaning and for rendition, several renditions per file, a rule cascade, a measurement extension. Each piece answered a real need. But a duckn file's job is to let its voxels be interpreted faithfully, and to carry what the imaging formats it exchanges with carry. Measured against that, most of the new machinery was enrichment — and most of it was not even about one file: an atlas hierarchy, a house color scheme, and a cross-walk to SNOMED are properties of a labeling scheme, written once and applied to thousands of files. Putting them in every file duplicates them and freezes them at whatever they were on the day of writing. So the file keeps the segment record that DICOM SEG and `.seg.nrrd` also keep, gains the two things interpretation was missing — overlap without synthetic segments, and the difference between "nothing here" and "not evaluated" — and guarantees the handles by which everything else can point at it. Those handles are the test's third clause, stated openly: `id` and `labeling_scheme` interpret nothing and round-trip nowhere, and they are what lets the rest stay out.

**Why `label_values` is a set, and always an array.** Through 0.6 `label_value` could be an integer, a list of integers, or a list mixing integers with segment ids; 0.7 made it one integer and moved unions into groups, which forced every overlap island to have a segment of its own and made migration synthesize segments named `label_3`. An island is not a segment. It is a cell of a Venn diagram whose whole meaning is which segments contain it, and a list of values per segment says exactly that with no extra entries. The field is plural, always an array, and sorted, so that there is one type, one code path, and one spelling: the 0.6 allowance that `1` and `[1]` were the same thing is what let a migration bug create two owners for one value.

**Why a designated segment covers all of its concept.** Without groups, a hierarchical scheme leaves a choice: does the segment for an interior structure list only the voxels labeled with its own id, or those of its whole subtree? If writers may choose, a designation no longer determines a voxel set — "the volume of structure 184" has two answers and nothing in the file says which — and the designation is the one portable key everything outside the file selects by. So the choice is made: all of the concept. It is also the only reading under which a designation is *exact*, since the voxels valued 184 alone are not the frontal pole, they are the part of it nobody subdivided. The cost is long value lists on broad structures, and files that are larger than their 0.7 equivalents; the benefit is that region queries are answerable from the file alone, and the external hierarchy document carries only the tree.

**Why one owner per value became the special case.** Version 0.7 required that a value resolve to exactly one segment, which is what makes a label table a label table — and what made overlap need groups. With sets, the requirement would forbid the mechanism. It survives as the *definition* of the case every simple consumer wants (§5): when it holds, a layer is one labelmap and one color table with no policy needed; when it does not, draw order decides, and exporters materialize.

**Why groups left the file.** Two things seemed to need them. Overlap is handled by shared values. And a partition claim — these classes are mutually exclusive and exhaustive — is a fact about a binding that needs no group: the segments of a label-table layer are disjoint by construction, and a softmax layer is a partition of its classes by construction. What remains is the tree, and a tree belongs to its scheme. The Allen structure graph ships with the atlas, at a version; a file that names the scheme and version has said which graph applies, and one external definition keyed by code serves every such file.

**Why `labeling_scheme`.** It is the smallest thing that makes the external layer work. Without it, an external hierarchy must be matched to a file by guesswork; with it, the file says "my segments are classes of this scheme at this version," each segment carries its class as an exact designation, and everything written for the scheme joins by code. The carrying is a *should* rather than a *must* because real files have segments the scheme did not produce — a remainder, a structure a reader added by hand — and a rule that such files cannot declare their scheme would only mean the declaration was omitted. It records a definition, not a process: nothing about the run, the weights, or the inputs, which are provenance.

**Why schemes are identified by `uri`.** A scheme key is a local abbreviation, and the whole external layer joins files to documents by code. Two documents agree that `SCT` and `SNOMED` are one system only if something other than the key says so; `uri` is that thing, compared as a string, in the manner of a FHIR code system's canonical URL.

**Why designations are exact.** A designation is relied on as identity by every consumer that reads one. The alternative considered — a `relation` on each designation, or a `mappings` field beside them — is sound, and is where inexact correspondences will be recorded; but no reference format carries them, nothing about interpreting the voxels depends on them, and they are typically added by someone other than the producer, later. So in the file the rule is simply that a designation is exact or is not written. Exact identifications in further ontologies are designations like any other; laterality in schemes that lack it is reached exactly through a cross-scheme `modifier`, as DICOM does it.

**Why roles, and why two.** Background and unknown differ in what a reader may conclude: one is a negative that counts, the other is no claim and is excluded. No amount of styling recovers the distinction once both are painted with the same value, and it changes the result of every comparison and measurement, so it is interpretation and belongs in the file. It is a `role` enumeration rather than 0.7's `background` boolean so that the two are structurally exclusive. Reasons for being unknown are meanings and go where meanings go, which is what keeps the enumeration from growing. The reference formats mostly cannot say it — DICOM's `LABELMAP` can mark a background through its pixel padding value, and nothing marks "not evaluated" — so exporters report the loss, and the `.seg.nrrd` exporter keeps the role in a tag.

**Why undescribed values are excluded but are not "unknown."** Version 0.6 called them implementation-defined, which meant a validator could not tell a legitimate implicit island from a bug. Making description a writer's obligation makes the rule checkable; giving readers a defined, conservative fallback keeps damaged files usable. They are kept distinct from the unknown role because one is a statement and the other is a defect: a stylesheet that grays out what was not evaluated should not also quietly gray out what a writer forgot.

**Why `color` is a string on the segment, in three forms.** DICOM SEG and `.seg.nrrd` each carry one recommended color per segment, so the file carries one. It is a CSS string because a triple of numbers is not a color until something says what space it is in, and no earlier version did; and because DICOM's CIELab *is* CSS's `lab()`, a DICOM color is transcribed losslessly rather than pushed through a conversion that implementations have got wrong for two decades. The written forms are limited to the three every reader must accept, so that no conforming writer can hand a conforming reader a color it must discard. More than one rendition, opacity and visibility, and coloring by group are real needs and are external.

**Why `segments` order is the draw order.** Overlapping segments with one color each need an order to be viewable at all, 3D Slicer already uses list order, and a default that lives in the file means two viewers with no stylesheet agree. It is a display default, plus the ordinal position the format mappings have always used; no rule of §5 depends on it.

**Why ids are tokens.** An id is the one part of a file that other documents embed, in a reference that also contains a Zarr path and must survive a URL fragment, a shell argument, and a CSV cell. Stating the alphabet positively costs nothing for ids as they actually occur, and moves free text to `name`, where it was always meant to be. The price is paid at the edges: a converter derives a token from a source id that is not one, and an id cannot survive a format that has nowhere to keep it.

**Why `seg`, not `segmentation`.** The data model — layers, `source_representation`, representation conversion state — is inherited from 3D Slicer's `.seg.nrrd`, and the short name echoes that lineage. The Slicer-specific state is confined to `metadata.slicer`, so the first-class fields remain platform-neutral.

**Why `name`, not "label."** Every community this convention serves uses "label" for something different — an integer in imaging, a string in the semantic web, both in DICOM. The imaging sense cannot be moved, because "label map" and "label value" are the field's terms, so in this convention *label* means the integer exclusively, and the string is a `name`: what SKOS calls `prefLabel`, what schema.org calls `name`, what DICOM calls `SegmentLabel`. A coded entry's `meaning` is a different thing again — the terminology's rendering of a code, DICOM's `CodeMeaning`.

**Why `dicom` is separate from `designations`.** The DICOM Segmentation IOD has a specific classification structure (category → type → modifier, plus anatomic region → modifier) that does not map onto a flat list of codes, and it requires attributes — an algorithm type — that are about how a segment was made rather than what it is. Mixing these into `designations` would either force the DICOM structure onto non-DICOM uses, as `.seg.nrrd` does, or lose what DICOM round-tripping needs.

**Why `segments` is an array, not a map.** Segments have a natural order — creation order, UI order, draw order. An array preserves it; `id` provides stable lookup.
