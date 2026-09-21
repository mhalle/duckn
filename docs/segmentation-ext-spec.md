# Segmentation Extension for duckn

**Extension name:** `seg`
**Version:** 0.8
**Status:** Draft. This is the version the released code reads and writes. It is a breaking change from 0.7, which is kept for reference as `archive/segmentation-ext-v07-spec.md`; older files are migrated on load (§6.3).

---

## 1. Purpose

This document defines the `seg` extension for the duckn convention. It replaces the `.seg.nrrd` metadata encoding — where segment properties were flattened into NRRD key/value pairs with `SegmentN_` prefixes and `~^|&`-delimited substructure — with a clean JSON representation.

### Scope

Version 0.8 draws the extension's boundary with a stated test. A field belongs in the file when:

1. a reader needs it to **interpret the voxels faithfully** — which values are which segment, which values mean "nothing here" and which mean "not evaluated"; or
2. **a reference format carries its equivalent**, so that a round trip needs it. The reference formats for this extension are the DICOM Segmentation IOD and 3D Slicer's `.seg.nrrd`: a segment's label, coded property type, algorithm type, and recommended display color; a segment's name, color, and terminology entry; or
3. it is **a handle by which documents outside the file find it**: a segment's `id`, its exact `designations` in any coding system, the registry's `uri` and `version` for each scheme, and the `labeling_scheme` declaration. Some of these interpret no voxel and no reference format carries them. They are in the file because they are what makes it possible for everything else to stay out (§7).

Everything else is enrichment: alternative renditions, groupings and hierarchies, inexact correspondences between a segment and a concept, translations, measurements. Those are real needs, and experience shows several of them are common. But they change for their own reasons, under other authorities, and most of them are properties of a *labeling scheme* rather than of one file — the hierarchy of an atlas applies to every volume labeled with that atlas. They belong in documents outside the file that reference it.

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

Where several segments of a layer list a value, the **topmost** is the last of them in `segments` order. Every question that needs one answer for a value — its color, its name in a tooltip, the segment a lookup returns — is a question about a *(layer, value)* pair, and takes the topmost segment of that layer that can answer it (§3.2 `color`). A value in one layer has nothing to do with the same value in another. Only a lookup by designation ranges over layers (§4.1).

### Overlapping segments: layers

Alternatively the array has a `list` axis (kind `"list"`) plus 3 spatial dimensions. Each position along the list axis is a **layer** — a 3D label volume. Segments that would collide in a single volume are assigned to different layers, and the same value may be used in different layers without any relation between its uses.

The two mechanisms are independent and may coexist. Layers duplicate the volume and are natural when segments are authored independently; shared values partition it and are natural when a pipeline computes the decomposition up front.

### A designated segment covers all of its concept

A segment that carries a designation (§4.1) lists **every described value in its layer whose voxels are that concept** — not some of them. This matters for labeling schemes with a hierarchy. An atlas annotation volume labels each voxel with its most specific structure, so the voxels of "Frontal pole" are spread over the values of its layers *and* a value of its own, for voxels not resolved to any layer. The segment designated "Frontal pole" lists all of those values:

```json
{ "id": "184", "name": "Frontal pole, cerebral cortex", "label_values": [68, 184, 667] }
```

Within a layer, a designation then determines one voxel set, whoever wrote the file: "the volume of structure 184 in this layer" has one answer. Four points make the rule precise.

- It holds for **each** designation a segment carries, so all of a segment's designations are co-extensive in its layer. That is what *exact* means (§4.1): TotalSegmentator's `kidney_left`, SNOMED's left kidney, and TA2's kidney qualified as left may share a segment; `lung_upper_lobe_left` and SNOMED "Lung" may not.
- It is scoped to a layer. The same designation may appear in two layers — two raters' livers — and then the file has two voxel sets for the concept, one per layer. Which to use, or how to combine them, is the consumer's question; the file does not answer it.
- It ranges over **described** values. A concept that extends into the layer's background — "Thorax" in a layer whose 0 is background — cannot be fully covered, because a structure may not list a background value (§5), and so cannot be designated in that layer.
- It is a **writer's obligation that cannot be verified from the file**, even with the voxels: that value 68 is part of concept 184 is a fact of the scheme's hierarchy, which the file does not carry (§7.2). A reader assumes it holds. It asks nothing of a segmenter about concepts it does not output: a model that produces lung lobes and no "lung" segment is conforming.

The unresolved remainder — the voxels valued 184 alone — is not the concept and does not carry its designation; a writer that wants to name it gives it a segment of its own with a `name` and no designation in the scheme.

Two consequences follow. A file for a deep hierarchy holds long value lists on its broad structures, which is the price of answering region queries from the file alone. And where one segment's values contain another's, the containing segment is listed **first** (§5), so that the topmost segment for a value is always the most specific one.

### Background, unknown, and undescribed values

Three kinds of voxel are not part of any structure, and they mean different things.

**Background** is a definite claim: *none of the structures this layer describes is here.* An unlabeled voxel is a negative for every segment in the layer, which is what makes it countable — a voxel labeled in one segmentation and background in another is a disagreement. It is not a claim that nothing at all is there: a liver-only segmentation's background says "not liver" and is silent about the spleen, because the file has no spleen segment to be negative about. By default a layer's background is the value 0 — a Zarr array's `fill_value` is typically 0, so 0 is what every unwritten voxel reads as. A layer whose background is another value, or deserves a name, declares a segment with `"role": "background"`. A segmentation in which 0 is *not* background — FreeSurfer's `aseg`, whose 0 is "Unknown"; an atlas with a real class at 0; a DICOM label map that marks no background — says so with `"implicit_background": false` (§3.1), and then a layer has a background only if a segment declares one.

**Unknown** is the absence of a claim: *this voxel was not classified.* A scan cropped mid-organ, an artifact region, a model that abstains below a confidence threshold, a manual segmentation with three of five lesions done — in each, labeling the voxels as background would assert "nothing here" where the truth is "not evaluated." A segment with `"role": "unknown"` says so. A reader excludes unknown voxels from comparisons and measurements rather than counting them either way; this is the "ignore index" of segmentation benchmarks, and for the same reason. Unknown is always explicit: if unlabeled voxels defaulted to unknown, no comparison could ever penalize over-segmentation. A writer who wants unwritten regions to read as unevaluated sets the array's `fill_value` to an unknown segment's value; `fill_value` is one value for the whole array, so in a layered array every layer has to describe it (§5).

**Undescribed** values are a writer's error with a defined reading. Every value present in a layer's data must be the layer's background value or belong to some segment (§5), so that a reader can always answer "what is value 5?" When one is not, a validator with the data reports it, and a reader does not fail: for the purposes of comparison and measurement it treats the value as it would unknown — excluded — renders it distinctly, and reports the problem. Undescribed is not the unknown *role*, and nothing that selects by role matches it (§7.1). Merging it into background would silently erase something a writer painted; treating it as a structure would be a guess. A writer has two conforming ways to say what such a value is, and they mean different things: a **minimal segment**, `{"id": "v5", "label_values": [5]}` — "a structure I have not named," counted as itself — or the same with `"role": "unknown"`.

### Fractional labelmaps

When `source_representation` is `"fractional-labelmap"`, voxel values are continuous — the fraction of the voxel occupied by a segment, or the probability that it belongs to one — and the integer-equality rule above does not apply.

Each segment therefore needs its own volume of fractional values, so a fractional segmentation **must** carry a `list` axis and assign every segment a distinct `layer`. `label_values` has no work to do and is `[1]` by rule (§5). A segment may carry a `role`, which then describes its whole layer rather than a value: the "none of the above" channel of a softmax model is a background segment, and a per-voxel abstention map is an unknown one. Shared values, background values, `implicit_background`, and undescribed values are defined only for binary labelmaps.

The value range is not constrained here — `[0, 1]` is typical, but an application storing 0–255 or 0–100 should record the scaling with the duckn convention's `value_transforms` rather than inventing a convention in this extension.

### Empty segmentation

Unlike `.seg.nrrd`, a Zarr store does not require non-empty data. An empty segmentation can be represented as a zero-extent array or by providing only the extension metadata with no voxel data. `segments` is still required, but it may be an empty array for a segmentation that describes nothing. This is the one exception to §4.4's practice of omitting empty collections: `segments` is structural, and omitting it would be indistinguishable from a malformed file.

---

## 3. Extension Fields

The `seg` extension is declared under the `"duckn"` object's `"extensions"` key.

### 3.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification, as a string.

```json
"version": "0.8"
```

**Version semantics.** While the major version is `0`, the *minor* version may introduce breaking changes; this overrides the duckn convention's default rule that minor increments are additive. From 1.0 onward, major increments signal breaking changes and minor increments are additive. `version` must be a string: a JSON number `0.10` is the float 0.1, and a reader that parsed it that way would mistake a later file for an earlier one. A missing or unparseable version is an error, never "older than everything." The two parts are decimal integers and are compared as integers, which is the reason the field is a string. A reader for 0.8 refuses, by its version and before looking at its fields, a file that declares a later minor version while the major version is `0`, or any later major version, since it cannot know what changed.

Version 0.8 is a breaking change from 0.7; §6.3 describes what changed and how older files are read.

#### `source_representation`

Optional. The segmentation's primary representation — the one edited by the user and from which others are derived.

| Value | Description |
|-------|-------------|
| `"binary-labelmap"` | Integer-valued voxels; each value identifies segments |
| `"fractional-labelmap"` | Continuous voxel values, one layer per segment |
| `"closed-surface"` | The source is a surface mesh; the array is a derived rasterization |
| `"planar-contour"` | The source is a set of planar contours; the array is derived |

Other values may appear; readers should not fail on an unrecognized one. The rules for **fractional** labelmaps apply when the value is `"fractional-labelmap"`, or when it is absent and the array's data type is floating point. The rules for **binary** labelmaps apply in every other case, including the two derived rasterizations and an unrecognized value.

#### `labeling_scheme`

Optional. The labeling scheme or schemes this segmentation was produced under: a key of `terminologies`, or an array of distinct keys when segments are drawn from more than one scheme — a multi-task model whose layers follow different class lists. A single key and a one-element array mean the same thing.

```json
"labeling_scheme": "TotalSegmentator"
```

A labeling scheme is a coding system whose codes are the classes of a segmenter, an atlas, or a protocol: TotalSegmentator's class list at a version, the Allen CCF structure ontology, FreeSurfer's `aseg` labels. Declaring it says that **where a segment is a class of that scheme, the class is recorded, exactly, as one of its designations** (§5), and the registry entry says *which edition* of the class list that was. Segments the scheme did not produce — a structure added by hand, an unresolved remainder — carry no designation in it. A segment belongs to the scheme whose designation it carries; the array form does not assign schemes to layers.

This is the join that lets documents outside the file apply to it without per-file work. A hierarchy, a color scheme, or a cross-walk written once for "TotalSegmentator 2.4" applies to every file that declares that scheme (§7). It is the scheme's *definition* that is recorded — identity — and not an account of the run that applied it, which is provenance and belongs to the `provenance` extension.

A variant with a different class list — TotalSegmentator's `total` and `total_mr` tasks — is a different scheme and is registered under its own key. A hand-drawn segmentation declares no scheme.

#### `implicit_background`

Optional boolean, binary labelmaps only. When absent, a layer that declares no background segment has background value 0. `false` withdraws that default: a layer then has a background only if one of its segments has the background role, and 0 is a value like any other — it may belong to a structure, or to an unknown segment. Omit when the default applies; never write `true`.

```json
"implicit_background": false
```

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
| `uri` | no | A canonical identifier for the coding system. It is what identifies the system across files, since a key is local to its document (§7.1). Compared byte for byte, with no normalization |
| `version` | no | Version of the coding system in use, compared as an exact string |
| `url` | no | URL for the coding system's browser, specification, or landing page |
| `url_template` | no | Template for concept URLs. The substring `{code}` is replaced with a coded entry's `code` |

A writer registers every scheme it uses in `designations` or `dicom` entries, and should give each a `uri`. A scheme named by `labeling_scheme` has both `uri` and `version`, since those are what an external document is matched against. A reader does not reject a file for an unregistered scheme, and does not assume the registry enumerates every scheme in the file (§5).

#### `segments`

Required. An array of segment objects (§3.2). May be empty for an empty segmentation (§2).

The order of the array matters in three ways, all stated here. It is the segment's ordinal position in format mappings — the `N` in `SegmentN_*` (§6.1), the order of a DICOM `SegmentSequence` (§6.2). It decides the **topmost** segment for a shared value (§2), and with it the recommended draw order, later over earlier. And a segment whose values contain another's comes before it (§5).

#### `metadata`

An open-ended object for application-specific state about the segmentation, keyed by source application or standard. Well-known keys: `slicer` holds 3D Slicer's `contained_representations`, `conversion_parameters`, and `reference_extent_offset`; `dicom` holds object-level DICOM attributes a converter wishes to keep, such as `SegmentationFractionalType`; `duckn` holds what a migration preserved (§6.3) — in particular `omitted`, an array of the original segment objects of groups that have no 0.8 form.

#### `legacy`

Optional. Verbatim source metadata preserved so a file converted *from* another format can be converted back without loss of formatting. For `.seg.nrrd` sources this holds the original key/value strings under a `keyvalues` object. A writer that has not modified the segmentation may replay these strings byte-for-byte; a writer that has modified it must regenerate from the model and must drop the stale entries (§3.3). Readers that are not round-tripping to the source format ignore it.

### 3.2 Segment Object Fields

Each element of `segments` is a JSON object. All fields are optional except `id` and `label_values`.

#### `id`

A unique identifier for the segment within this segmentation, and the handle by which anything outside the file refers to it (§7). It does not change when the segment is renamed or the array reordered.

```json
"id": "kidney_left"
```

An `id` is a **token**: one or more characters from `A`–`Z`, `a`–`z`, `0`–`9`, `_`, and `-`. Ids are case-sensitive. The grammar is stated positively because an id is embedded in other documents' references, in URLs, shell arguments, and CSV cells, where a space, a dot, or a slash makes trouble. Most ids in practice are tokens already — `997`, `Segment_1`, `ctx-lh-bankssts`, `Left-Hippocampus` — but not all: 3D Slicer can generate ids of the form `2.25.<digits>`, and a DICOM `SegmentLabel` is free text. Free text belongs in `name`; converters derive a token (§6).

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

It is always an array, even for one value: `[1]`, never `1`. A value may appear in more than one segment of the same layer; that is how overlap is represented (§2). Every value is representable in the array's data type, and its magnitude does not exceed 2^53 − 1, the largest integer every JSON reader — a JavaScript one included — holds exactly. The **effective value set** of a segment is the set of *(layer, value)* pairs formed from its `layer` and each entry — a value only identifies voxels within a layer.

#### `role`

Optional. One of:

| Value | Meaning | In a comparison or measurement |
|-------|---------|-------------------------------|
| `"background"` | "none of the described structures is here" | counted, as the negative class |
| `"unknown"` | "not classified" | excluded |

Omit for a segment that is a structure.

In a binary labelmap, at most one segment per layer has the background role, and it lists **exactly one** value. Any number of segments may be unknown, each with any number of values. A layer's **background value** is:

1. its background segment's value, if it has one;
2. otherwise 0, unless `implicit_background` is `false`;
3. otherwise none — the layer has no background.

A layer with no background has no negative class: a comparison over it cannot count a false positive against the unlabeled region, and a consumer computing one reports that (§10) rather than giving a score as though it could.

In a fractional labelmap a role describes the segment's whole layer, and none of the above applies.

The *reason* a region is unknown — artifact, outside the field of view, abstained — is a meaning and goes in `name` or `designations`. That is what keeps this field at two values.

#### `layer`

The zero-based index of the layer (position along the `list` axis) holding this segment's values; a non-negative integer, present only in an array that has a `list` axis (§5). An absent `layer` means layer 0, and a writer omits `layer` when it is 0. When an array has more than one `list`-kind axis, `layer` indexes the first.

#### `extent`

The bounding box of the segment's non-empty region, as `[min_i, max_i, min_j, max_j, min_k, max_k]` in voxel coordinates, both bounds inclusive, over the array's three spatial axes in storage order.

```json
"extent": [45, 102, 30, 98, 12, 55]
```

`extent` is a cached index, kept because `.seg.nrrd` carries it. It is advisory: a reader must not rely on it for correctness, a validator may check it against the data, and it does not survive a change to the grid or the voxels (§3.3).

#### `color`

Optional. The producer's **recommended display color**, as a CSS color string. It is a recommendation in the sense DICOM gives the word: a viewer may override it, and a rendition applied from outside the file takes precedence over it.

```json
"color": "#dd8265"
```

A string is used rather than a triple of numbers because the syntax states the color space: `#dd8265` is sRGB by definition, and `lab(64.0631 33.8785 31.5159)` is the same color as CIELab under D50, where `[0.87, 0.51, 0.40]` could be sRGB, linear light, or anything else. No earlier version of this extension said which.

Four forms are written, each with one spelling, and every reader accepts all four. Each exists to write down what a source holds, without converting it:

| Form | Spelling | Written when |
|------|----------|--------------|
| hex | `#rrggbb` — six lowercase hexadecimal digits | the color is sRGB at 8 bits per channel: the common case, and what every color table holds |
| sRGB | `color(srgb r g b)` — three plain numbers in 0–1, each rounded to six significant digits with trailing zeros removed | the color is sRGB and is not 8-bit: a `.seg.nrrd` color set to arbitrary floats (§6.1) |
| CIELab | `lab(L a b)` — three plain numbers with exactly four decimal places | the color's source is CIELab under a D50 white (§6.2), or it is outside sRGB |
| CIE XYZ | `color(xyz-d65 x y z)` — three plain numbers with exactly seven decimal places | the color's source is CIELab computed under a D65 white, which is what most DICOM segmentations in existence contain (§6.2) |

**Writing.** In the three functional forms the numbers are separated by single spaces, carry no percent signs, and are followed by no alpha component; these are also CSS's own canonical serializations. Numbers are written in plain decimal notation, never with an exponent or a leading `+`: a `color(srgb …)` component is what C's `%.6g` produces, except that a value `%.6g` would write with an exponent is written out in full (`0.00001`, not `1e-05`), and a component outside 0–1 is clamped first. A number that would be written as negative zero is written without the sign (`0`, `0.0000`, or `0.0000000`, according to the form). `srgb` is CSS Color Level 4's sRGB with components from 0 to 1, `lab()` is its CIELab, referenced to a D50 white, and `xyz-d65` is its CIE XYZ relative to D65; the last two can state any color exactly. A writer writes no other CSS form — the three-digit `#rgb`, `rgb()`, an alpha component. A color a writer creates or changes is written in the first form of the table whose condition it meets. A color a writer has read and not changed is written in the **same form** it was read in, never converted to another — a `lab()` color stays `lab()` — and in that form's canonical spelling, so a file that held `#DD8265` is rewritten with `#dd8265`. A clamped `color(srgb …)` component is reported.

**Reading.** A reader is more tolerant than a writer. It accepts exactly what CSS's grammar accepts for these four forms — `#rrggbb`, `lab(…)`, `color(srgb …)`, `color(xyz-d65 …)` — with whitespace around the string ignored, hexadecimal digits, function names, and color-space names in any case, whitespace wherever CSS allows it, `xyz` taken as the alias of `xyz-d65` that it is, and numbers written in any way CSS allows a number to be written. Within those forms it does not accept a percentage, `none`, a comma, or an alpha component; and it accepts no other form — not the three-digit `#rgb`, the eight-digit `#rrggbbaa`, another function, or `color()` with another space. What it does not accept it treats as absent, and reports. An absent color means the viewer chooses. A reader holds the value it parsed, as parsed; rounding to a form's canonical spelling happens when a writer writes, is not reported, and can change a value in its last digits (`color(srgb 0.4980395 0 0)` is written `color(srgb 0.49804 0 0)`). Conversion to a display's color space is CSS Color 4's. Where a color in another space must be expressed in sRGB — as floats for `.seg.nrrd`, at 8 bits for a color table — it is converted as CSS Color 4 defines and brought into gamut by CSS Color 4's gamut-mapping algorithm, not by clipping channels, and the change is reported when the color was out of gamut. The gamut-mapped numbers are not guaranteed identical between implementations of that algorithm, and are outside this document's expectation that two implementations produce the same bytes. A `color(srgb …)` color is already sRGB and is never gamut-mapped; its components are clamped to 0–1 where a target requires it, and the clamping reported.

**Which color a voxel takes.** Within a layer, a voxel's recommended color is that of the **topmost segment of the layer listing its value that has a color** (§2). That is a color table with one entry per value, one table per layer. Because a segment whose values contain another's is listed first (§5), a specific structure is drawn over the general one that contains it, and a structure with no color of its own shows the color of the last containing structure before it that has one. How the layers of a layered array are composited, and how unknown and undescribed voxels are shown, is a viewer's choice and is not specified; a reasonable default is ascending layer index, higher over lower, with background voxels transparent.

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

The DICOM Segmentation IOD's per-segment content that has no other field: the classification structure (category, type, type modifier, anatomic region, region modifier) and the algorithm attributes. See §4.2. Present only when DICOM SEG interoperability is needed.

#### `metadata`

An open-ended object for application-specific per-segment metadata, keyed by source application or standard. Well-known keys:

- `slicer` — `name_auto_generated`, `color_auto_generated`, and `tags`;
- `dicom` — DICOM segment attributes this extension gives no meaning to, kept for a round trip: `SegmentDescription`, `TrackingID` and `TrackingUID`, further modifier or region items as arrays of coded entries under `type_modifiers` and `anatomic_regions`, and further algorithm names as an array of strings under `algorithm_names` (§4.2);
- `duckn` — what a conversion or migration preserved: an original `id` that was not a token, `display`, and `designations` set aside (§6).

### 3.3 Derived Arrays

The convention keeps the metadata that describes an array when the array is derived, and drops what described its source (§4.5 of the convention). Most of `seg` describes the array's values and is kept. What is tied to a particular grid or a particular source file is not:

- `extent` on every segment, and `metadata.slicer.reference_extent_offset`, are in voxel coordinates. Any operation that changes the grid or the voxels — crop, resample, reorient, edit — drops them; recomputing `extent` afterward is a separate step a caller may ask for.
- `legacy` is source-format provenance. Any operation that changes the grid, the voxels, or the segments drops it.

And one kind of operation invalidates the extension altogether. A binary labelmap's values are names, not quantities: interpolating them — resampling with any method other than nearest-neighbor, smoothing, arithmetic — produces values that belong to no segment. An operation that does this to a binary labelmap must not carry `seg` forward. Nearest-neighbor resampling, cropping, reorienting, and rechunking keep every other field unchanged.

---

## 4. Segment Identity

Segment identity has two parts:

1. **Designations** — coded entries saying "this segment is concept *X* in system *Y*." The primary mechanism for interoperable identity.
2. **DICOM content** — the structured classification and the algorithm attributes required by the DICOM Segmentation IOD. Needed only for DICOM round-tripping.

### 4.1 Designations

A segment is a real anatomical or pathological thing that different communities identify using different coding systems — a kidney is SNOMED 64033007, FMA 7203, and TA2 5765 simultaneously. Each entry captures one such identification; the first is preferred. When `labeling_scheme` is declared, one of them is the class the segment was produced as.

#### Designations are exact

Every consumer relies on one contract: a designation *is* the segment. A DICOM exporter takes the first designation as the property type; a lookup by code finds the segment; a stylesheet keyed by code paints it. So a designation is written only when the identification is exact, and the segment covers all of that concept in its layer (§2). A segment that is merely *near* a concept, or narrower than one — a mass that may be a neoplasm; "kidney" for a left kidney in a scheme that cannot say left; the unresolved remainder of an atlas structure — does not get that concept as a designation.

Exact identifications in several coding systems are all designations and all belong in the file, whoever added them and when: they are portable keys (§1, §7.1). What is external is the *inexact* correspondence — close, broader, narrower, related — which a designation cannot express without breaking the contract (§7.2).

Within a layer, at most one segment carries a given designation (§5); the same designation may appear in different layers. A lookup by code over a whole file therefore returns one segment per layer at most, and the choice between layers is the consumer's own.

Two designations are **the same** when their schemes are the same coding system — their registrations have equal `uri`s, or, where either scheme is unregistered or has no `uri`, their keys are equal — their `code`s are equal, and their modifiers are both absent or are the same by this test. `meaning` is never compared.

Coverage of the *anatomy* is a different question from identity. A liver segment from a scan cropped mid-organ is exactly liver — every voxel in it is liver, and it lists every liver value in the layer — and is designated so. That it is not all of the patient's liver is said by the unknown role on the region that was not evaluated, never by weakening the designation.

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

The coded entries have the same shape as a designation, minus `modifier`. Every field is optional, so a partial record can be kept as it becomes known. A DICOM writer that finds a needed field missing takes it from its caller or **fails**; it must not invent one. `"AUTOMATIC"` written for a hand-drawn segment is a false statement in a clinical record.

DICOM permits several items in its type-modifier sequence and in its anatomic-region sequence, and several algorithm names; this extension holds one of each. An importer keeps the first and preserves the rest under `metadata.dicom` (§3.2), and reports that it did.

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

### Violations, and what "report" means

Each rule below is marked with what a violation is, and with what a **reader** does about it:

- **error** — the file does not conform. A writer must not produce it, and a validator rejects it. A reader either *continues*, with the reading the rule states, or *refuses* the file; the rule says which. It reports the violation either way.
- **warning** — the file conforms, and something is worth a person's attention.

To **report** is to hand the caller a diagnostic *alongside* the result, and not to fail. Throughout this document, a reader, an importer, a migration, or an exporter that "reports" something returns it this way; one that "fails" or "refuses" does not produce a result. A diagnostic carries a **code** from §10, a severity, and the thing it is about, which §10 fixes for each code: a segment, a *(layer, value)* pair, a scheme, a reference from an external document, a segment of the source file that a migration omitted (by its original id), or the extension itself. Two implementations given the same input report the same codes about the same things; wording, types, and delivery are an implementation's business, provided diagnostics reach the caller rather than a log. An implementation may offer a strict mode that turns reports into failures.

A segment's **effective value set** is the set of *(layer, value)* pairs defined in §3.2. Two segments may have the same effective value set: identity is the `id`, never the voxels.

### Rules

**Structure**

1. *error; reader refuses.* `version` is present, is a string, is `N.N` — two decimal integers, each `0` or a digit string with no leading zero, no sign, and nothing else — and is not later than the reader supports (§3.1).
2. *error; reader refuses.* `layer` is present only when the array has a `list`-kind axis, and is then a non-negative integer that is a valid index into it.
3. *error; reader continues.* **3a** `labeling_scheme`, when an array, has distinct entries (a reader ignores repeats). **3b** `implicit_background`, when present, is `false` (a reader ignores `true`). **3c** `implicit_background` is present only on a binary labelmap (a reader ignores it on a fractional one).

**Identity**

4. `id`. **4a** *error; reader refuses.* `id` is unique across `segments`. **4b** *error; reader continues,* using the id as found. `id` is a token as defined in §3.2.
5. *error for a writer; reader continues.* Every `scheme` used in `designations` or `dicom` is registered in `terminologies`. Every key named by `labeling_scheme` is registered with a `uri` and a `version`. An unregistered scheme is identified by its key alone.
6. *error; reader continues.* When `labeling_scheme` is declared, a segment carries at most one designation in each declared scheme. A reader takes the first as the segment's class.
7. *warning.* When `labeling_scheme` is declared, a segment with no `role` carries a designation in at least one declared scheme. A segment that carries none is one the scheme did not produce, and documents keyed by the scheme will not find it.
8. *error.* `role`, when present, is `"background"` or `"unknown"` (*reader refuses*). `dicom.algorithm_type`, when present, is `"AUTOMATIC"`, `"SEMIAUTOMATIC"`, or `"MANUAL"` (*reader continues*, carrying the value as found; a DICOM exporter fails on it). These are `rule-8a` and `rule-8b`.
9. *error; reader continues.* Within a layer, no two segments carry the same designation (§4.1). A reader's lookup by that designation returns the topmost of them.
10. *Writer's obligation; not verifiable from the file; never reported by a reader.* A segment lists every described value of its layer whose voxels are the concept of each designation it carries (§2). A reader assumes this holds. A checker that has the scheme's hierarchy from outside the file may test the part of it the hierarchy implies — a parent's values contain its children's.

**Values**

11. `label_values`. **11a** *error; reader refuses.* It is a non-empty array of integers, each representable in the array's data type and no greater in magnitude than 2^53 − 1; booleans are not integers. **11b** *error; reader continues,* reading the array as a set. Its entries are distinct and in ascending order.
12. *error; reader continues.* Where one segment's `label_values` strictly contain another's in the same layer, the containing segment comes first in `segments`. A reader takes the order as it finds it.
13. *error; reader refuses; binary labelmaps.* At most one segment per layer has the background role, and it lists exactly one value.
14. *error; reader refuses; binary labelmaps.* A layer's background value (§3.2 `role`) is listed by no segment other than its background segment. A value listed by a segment with a `role` is listed by no other segment of the layer: "nothing here" and "not evaluated" do not overlap a structure, or each other.
15. *error; reader continues; binary labelmaps.* In every layer that has a segment, the Zarr array's `fill_value` is the background value or a value some segment lists. Where it is not, a reader treats unwritten voxels as undescribed.
16. *error for a writer; reader continues; binary labelmaps; requires the voxel data.* Every value present in a layer's data is the layer's background value or is listed by some segment. A reader excludes a value that is not from comparison and measurement (§2).
17. *error; reader refuses; fractional labelmaps.* The array has a `list` axis; every segment has a distinct effective layer, an absent `layer` being 0 (§3.2); every segment's `label_values` is `[1]`.

Rules 1, 3a, 3b, 4–9, 11b and 12 constrain the extension's metadata alone. Rules 2, 3c, 11a, 13–15 and 17 additionally need the array's axes, data type, or `fill_value` — the data type because, when `source_representation` is absent, it decides whether the labelmap is binary or fractional (§3.1). Rule 16 needs the voxel data, which this extension never requires a reader to load. Rule 10 needs knowledge the file does not contain. The convention's own rule that an axis `kind` with a required size matches `shape` applies as it does to any array.

### Three properties a reader checks

These are not constraints on files. They are conditions a reader or exporter tests to decide what it can do.

**The label table.** A layer in which every segment has exactly one entry in `label_values`, and no value is shared, is a classic label table: one row per value, one color per value. Version 0.7 imposed this on every file; 0.8 makes it the special case.

**Disjointness.** Two segments in the same layer whose `label_values` do not intersect share no voxel. Two whose lists do intersect share a voxel only if a shared value occurs in the data. Two segments in different layers may overlap whatever their values. So metadata can prove that same-layer segments are disjoint, and only the data can prove that any two overlap.

**Nested.** A layer is *nested* when, for every value listed in it, the segments listing that value, taken in `segments` order, each have `label_values` that contain the next one's (containment need not be strict). The last of them is the **innermost** segment for that value. Every label table is nested; so is a hierarchical atlas (§2); two segments that merely overlap are not. The label table and nesting are tested on the values *listed*, not on those present in the data.

Writers should validate before serializing. Readers should not assume a file is valid.

---

## 6. Mapping to Other Formats

### 6.1 `.seg.nrrd`

| `.seg.nrrd` field | duckn `seg` extension field |
|---|---|
| `Segmentation_MasterRepresentation` / `Segmentation_SourceRepresentation` | `source_representation` |
| `Segmentation_ContainedRepresentationNames` | `metadata.slicer.contained_representations` (array) |
| `Segmentation_ConversionParameters` | `metadata.slicer.conversion_parameters` (object) |
| `Segmentation_ReferenceImageExtentOffset` | `metadata.slicer.reference_extent_offset` (array), kept as 3D Slicer wrote it, in NRRD's axis order: it is application state this extension gives no meaning to |
| `SegmentN_ID` | `segments[n].id`; the original under `segments[n].metadata.duckn.id` when it was not a token |
| `SegmentN_Name` | `segments[n].name` |
| `SegmentN_NameAutoGenerated` | `segments[n].metadata.slicer.name_auto_generated` (boolean) |
| `SegmentN_Color` | `segments[n].color` (hex, or `color(srgb …)`) |
| `SegmentN_ColorAutoGenerated` | `segments[n].metadata.slicer.color_auto_generated` (boolean) |
| `SegmentN_LabelValue` (one integer) | `segments[n].label_values` (one-entry array) |
| `SegmentN_Layer` | `segments[n].layer` (integer; omitted when 0) |
| `SegmentN_Extent` | `segments[n].extent` (6-element array), its three (min, max) pairs in the opposite order: NRRD lists axes fastest first, and `extent` runs over the array's spatial axes in storage order (§3.2) |
| `SegmentN_Tags` (minus TerminologyEntry and `duckn.role`) | `segments[n].metadata.slicer.tags` (object) |
| `SegmentN_Tags` `duckn.role` | `segments[n].role` |
| `SegmentN_Tags` TerminologyEntry — category/type/modifier/region | `segments[n].dicom` (object) |
| `SegmentN_Tags` TerminologyEntry — type code + type modifier | `segments[n].designations` (first entry, modifier included) |
| `SegmentN_Tags` TerminologyEntry — context names | Omitted (application state) |
| — (no `.seg.nrrd` equivalent) | `labeling_scheme`, `implicit_background`, further `designations`, `dicom.algorithm_*` |

**Ids.** A source id that is a token is kept. The others are derived, deterministically: every source id that is already a token is reserved first; then, in segment order, each remaining id has every run of characters outside the token alphabet replaced by `_`, becomes `Segment_<N>` (`N` being its zero-based index in `segments`) if the result contains no letter or digit, and takes the first of the suffixes `_2`, `_3`, … that makes it differ from every reserved and every already-derived id. The original is kept under `metadata.duckn.id`, and each change is reported. Two imported segments of a layer that carry the same designation are resolved as §6.2 describes. An exporter writes the originals back as `SegmentN_ID` only if restoring **all** of them leaves every id in the file distinct; otherwise it writes the tokens for all.

**Color.** `SegmentN_Color` is three floats in 0–1, which 3D Slicer writes with six significant digits. On import, each channel `x` has a nearest 8-bit value `n` — the integer nearest `255 × x`, halves rounding up. When, for every channel, `n` lies in 0–255 and `x` and `n / 255` are the same once both are formatted with `%.6g`, the color is written as hex from the three `n`. That is a comparison of strings, not a tolerance, and it is what holds for a color Slicer wrote from an 8-bit choice. Otherwise the three numbers are written as `color(srgb …)`, reformatted as §3.2 requires, which leaves anything Slicer wrote unchanged: `0.5 0.333333 0.0784314` becomes `color(srgb 0.5 0.333333 0.0784314)`. On export a hex color is written as `n / 255` per channel and a `color(srgb …)` color as its three numbers, its components clamped to 0–1 and the clamping reported, in both cases exactly as `%.6g` formats them, exponent included, since that is what 3D Slicer writes; so any color Slicer wrote returns byte for byte. The form is not preserved in the other direction: a `color(srgb …)` whose numbers happen to be 8-bit values comes back from `.seg.nrrd` as hex, the same color. A `lab()` or `color(xyz-d65 …)` color is converted to sRGB and brought into gamut (§3.2), and written the same way without being reduced to 8 bits.

**Overlap on export.** 3D Slicer writes one positive `LabelValue` per segment, unique within a layer; its 0 is background and has no segment; and it represents overlap with layers. A source can be written **as it is** — segments in their layers with their values — when every layer is a label table (§5) and every listed value is positive, except that a background segment may list 0. A background segment at 0 is not written on this path either (below). Otherwise the exporter **materializes**, which means writing new voxel data and not only new metadata:

1. Segments are taken in `segments` order, role-bearing ones included. A background segment whose value is 0 is `.seg.nrrd`'s own background and is not written; every other segment is.
2. Each is placed in the first destination layer, counting from 0, in which it overlaps no segment already placed **in that layer**. Two segments from the same source layer overlap when they share a value; two from different source layers overlap when they share a voxel, which the exporter decides from the data it is rewriting.
3. In its destination layer a segment keeps its value if it has exactly one, that value is positive, and it is not yet used there; otherwise it takes the smallest positive integer not yet used in that layer. Its voxels are the union of its source values' voxels.

The result is deterministic given the file and its data. The voxels of every written segment are preserved; the island decomposition, the source layer assignment, and the label values are not (§7.1), and the exporter reports that it materialized. A segmentation with nested value lists (§2) exports to one layer per level of nesting, which can be many full-size volumes, and an exporter reports it. When it materializes, `extent` is recomputed for every written segment, since the exporter is reading the voxels anyway; on the as-it-is path `SegmentN_Extent` is written from `extent` where a segment has one and omitted where it does not. A writer **may** instead replay a file's `legacy` key/values verbatim when it knows the segmentation is unchanged since they were read (§3.1); whether it does, and how it decides, is outside this document's expectation that two implementations produce the same file.

**Indices.** Written segments are numbered `Segment0_`, `Segment1_`, … with no gaps, whatever was skipped: 3D Slicer stops reading at the first missing index.

**Roles on export.** A role-bearing segment is written as an ordinary segment with the tag `duckn.role` set to its role. 3D Slicer keeps tags it does not recognize through a load and save, so the role survives a round trip through it; an importer takes the tag as a `role` only when its value is `background` or `unknown`, and otherwise leaves it among the tags and reports it. A background segment at 0 is not written, and the loss of its `name` and codes, if it had any, is reported. An *unknown* segment at 0 — FreeSurfer's — is written like any other, which forces materialization: its voxels take a positive value, and what 3D Slicer shows as a large painted region is exactly what the file says, a region that was not evaluated.

**A round trip through 3D Slicer** does not preserve label values or layer assignment even for a label table: Slicer collapses and renumbers labelmaps each time it saves. Ids, names, colors, tags, and terminology survive.

**Parsing notes** (unchanged from 0.7): accept either the `Master` or `Source` representation key; normalize representation names to kebab-case; split pipe-delimited lists and drop empty elements; strip the `Segmentation.` prefix from tag keys; treat literal `\n` escape sequences in `ConversionParameters` descriptions as newlines; drop a `Layer` of 0; do not default a missing `LabelValue` to 0: taking such segments in order, assign each the smallest positive integer that no segment of its layer has, whether read or already assigned; keep a two-part coded entry whose meaning is empty.

### 6.2 DICOM Segmentation

| DICOM attribute | duckn `seg` extension field |
|---|---|
| `SegmentNumber` | the pixel value, hence the entry of `label_values`, in a `LABELMAP`; the order of written segments otherwise |
| `SegmentLabel` | `segments[n].name` |
| `SegmentedPropertyCategoryCodeSequence` | `segments[n].dicom.category` |
| `SegmentedPropertyTypeCodeSequence` (+ first modifier item) | `segments[n].dicom.type`, `type_modifier`; and the first of `designations` |
| `AnatomicRegionSequence` (first item, + first modifier item) | `segments[n].dicom.anatomic_region`, `anatomic_region_modifier` |
| `SegmentAlgorithmType`, `SegmentAlgorithmName` (first value) | `segments[n].dicom.algorithm_type`, `algorithm_name` |
| `RecommendedDisplayCIELabValue` | `segments[n].color` |
| `SegmentDescription`, `TrackingID`, `TrackingUID`, further modifier and region items, and other segment attributes | `segments[n].metadata.dicom` (§3.2) |
| `SegmentationType` `BINARY` / `FRACTIONAL` / `LABELMAP` | `source_representation`, and the layer layout |
| `PixelPaddingValue` of a `LABELMAP` | the background segment's value |
| `SegmentsOverlap` | used on import and computed on export, below; not stored |

**Ids.** `SegmentLabel` is free text and always becomes the `name`. The `id` is `Segment_<SegmentNumber>`, which is unique because DICOM requires Segment Numbers to be. DICOM has no field for a duckn `id`, so an id does not survive export to DICOM and re-import (§7.1).

**Import.** An importer takes a reference format's type code as the segment's first designation (the table above, and §6.1's). It cannot know that the identification is exact in this extension's sense — that the segment covers all of its concept (rule 10) — and reports that it has not been established.

- A **`LABELMAP`** is one layer whose `label_values` are the Segment Numbers. DICOM says that in this type no value is background unless `PixelPaddingValue` names it (PS3.3 C.8.20.2.4); so the imported file sets `"implicit_background": false`, and gives the background role to the segment whose number `PixelPaddingValue` names. Where `PixelPaddingValue` names a value that has no segment item, the importer adds a background segment for it — `id` `Segment_<value>`, no `name` — listed first, since the object has said what that value is and leaving it undescribed would break rule 16 for every padded voxel. A segment item at value 0 is then an ordinary structure, as DICOM allows ("the Value 0, if used, will also be described, which might serve as the background," C.8.20.2.3.3). Items that agree in `SegmentLabel`, in the category, type, and anatomic-region sequences with their modifiers — codes compared by coding scheme and code value, never by meaning — and in `SegmentAlgorithmType` and `SegmentAlgorithmName` are one segment that an exporter wrote once per value (below): they are merged into one multi-valued segment, its `id` and every attribute not just named, its color among them, taken from the item with the lowest number, and the merge is reported. The Zarr array needs a `fill_value` that the layer describes (rule 15): the importer uses the background value where there is one, and otherwise the lowest value any item uses. A `LABELMAP` whose photometric interpretation is `PALETTE COLOR` is read the same way; mapping its palette to `color` is not defined in this version, and its segments are left without one.
- A **`BINARY`** object gives each segment its own frames. When `SegmentsOverlap` is `NO`, it is imported as **one layer**, with the Segment Numbers as values. When the attribute is `YES`, `UNDEFINED`, or absent, each segment is placed in its own layer with `label_values: [1]`. An importer may offer, as an option its caller chooses, to examine the frames and combine segments it finds disjoint into one layer, reporting that it did; the result of that option is outside this document's expectation that two implementations produce the same file.
- A **`FRACTIONAL`** object becomes one layer per segment, `label_values: [1]`, `source_representation` `"fractional-labelmap"`. The stored integers are kept as they are, in DICOM's 8-bit unsigned type, and the array's `value_transforms` carries one `linear` transform with `slope` `1 / MaximumFractionalValue` and `intercept` `0`, so that calibrated values are fractions; `sample_units` is not written. `SegmentationFractionalType` is kept under the extension's `metadata.dicom`.

Where two imported segments of a layer carry the same designation — two lesions both typed "Mass" is the ordinary case — rule 9 would be broken, and an importer from either format, this one or `.seg.nrrd`, resolves it as a migration does (§6.3, step 4), reporting each designation it sets aside.

**Choosing a type on export.** DICOM stores `BINARY` and `FRACTIONAL` segmentations under the Segmentation Storage SOP Class, and `LABELMAP` under a separate one, Label Map Segmentation Storage (`1.2.840.10008.5.1.4.1.1.66.7`), added to the standard in 2024. Few deployed readers support the second. An exporter therefore writes **`BINARY` by default** (`FRACTIONAL` for a fractional segmentation), and `LABELMAP` only when its caller asks for it and the segmentation is eligible. No segment is refused on account of overlap.

- **`BINARY`.** Segment Numbers start at 1 and increase by 1, as DICOM requires of this type, so they number the segments written, in `segments` order. Each segment's frames are the union of its values' voxels, and segments may overlap. A frame with no voxel set may be omitted, as DICOM allows, **except that every slice of the array is written at least once**: an importer rebuilds the grid from the positions of the frames it finds, so a slice that is empty in every segment keeps one empty frame, of the first segment written, and the array returns with the shape it had. The same holds for `FRACTIONAL`. DICOM records only presence and absence in this type and has no background class; this extension reads absence as background, so a background segment is not written, and the loss of its `name` and codes is reported. An unknown segment is written as an ordinary segment, and the loss of its role is reported.
- **`FRACTIONAL`.** One segment per layer, numbered from 1, **role-bearing layers included**: a softmax model's background channel holds real probabilities and is written like any other, its role lost and reported. A Segmentation object has no Modality LUT, so `value_transforms` cannot be exported. The exporter applies them to obtain a fraction `f`, clamps it to `[0, 1]`, and writes the integer nearest `f × MaximumFractionalValue`, halves rounding up, where `MaximumFractionalValue` is 255 unless its caller chooses another value that fits 8 bits. `SegmentationFractionalType` — `PROBABILITY` or `OCCUPANCY` — is a distinction this extension does not record; the exporter takes it from the extension's `metadata.dicom` or from its caller.
- **`LABELMAP`.** A segmentation is **eligible** when it is a binary labelmap with a single layer and that layer is nested (§5). The pixel data is written **unchanged**: in this type the pixel values are the Segment Numbers, which DICOM requires to be unique but not to start at 1 or be contiguous — that requirement is made of `BINARY` and `FRACTIONAL` only (PS3.3 C.8.20.2.4). DICOM requires every pixel value present to be described, so the exporter writes one segment item **per value**, described by the innermost segment listing it. Two consequences are reported. A segment that is innermost for no value has no item. And where the innermost segment for a value lists further values, its item stands for only part of it: several items then carry the same description, which an importer merges back (above) when they are identical, and an atlas's interior structure appears only as its unresolved remainder. A label map can hold leaves and remainders, not interior structures as wholes, and re-importing one does not restore the covering rule. Three conditions make the exporter fail rather than guess. Segment Number is a 16-bit unsigned attribute and the pixels are 8- or 16-bit unsigned, so every value must lie in 0–65535; an atlas with larger ids — the Allen CCF has many — must be renumbered first, by the caller's choice, which gives up the correspondence between value and id. Every value present in the data must be described (§5). And the item for a background value needs the category, type, and algorithm attributes DICOM requires of every item, which a background segment's `dicom` field or the caller must supply, since DICOM defines no code for "background"; `PixelPaddingValue` is then set to that value. A layer with no background gets no `PixelPaddingValue`. An unknown segment is written as an ordinary item, and the loss of its role is reported.

`SegmentsOverlap` is `NO` for a `LABELMAP`, as DICOM requires. For the other types an exporter writes `NO` when all written segments come from one layer with disjoint value lists (§5), `YES` when two of them are in the same layer and share a listed value that occurs in the data it is writing, and `UNDEFINED` otherwise. It may offer, as an option, to examine the frames of segments from different layers and write `YES` or `NO` as it finds; like the importer's option, that is outside the expectation of identical output.

`SegmentLabel` is required by DICOM and limited to 64 characters (code points, not bytes): it is the segment's `name`, else the `meaning` of its first designation, else its `id`, truncated if need be and the truncation reported. `TrackingID` and `TrackingUID`, when kept in `metadata.dicom`, are written only as a pair.

The expectation that two implementations produce the same output covers the extension's JSON and a `.seg.nrrd` file. Within a DICOM object it covers the values of the attributes this document specifies, and not UIDs, dates and times, the order of frames, or anything else an implementation generates.

**Color.** `RecommendedDisplayCIELabValue` holds three 16-bit integers, a CIELab color scaled as `L = v × 100 / 65535` and `a = v × 255 / 65535 − 128`, likewise `b`. What they mean depends on who wrote them, and an importer transcribes them into the CSS form that says what they are. In neither case is a color converted.

*As the standard intends.* The attribute is described as being "in PCS-Values" and "encoded as CIELab" (PS3.3, the Segment Description macro), and the encoding's definition notes that "this is the same form of encoding as used for the PCS in ICC Profiles" (PS3.3 C.10.7.1.1, a note). The ICC profile connection space uses a D50 white point (stated in PS3.4 N.2.2.2, which is informative), and so does CSS Color 4's `lab()`; the 16-bit form is ICC version 4's. So `L`, `a`, `b` are written as they stand, to four decimal places, from which the integers are recovered exactly: `[39330, 30580, 41942]` is `"lab(60.0137 -9.0117 35.1984)"`.

*As dcmqi writes them.* dcmqi — and through it 3D Slicer and most public collections, which is to say most segmentation objects in existence — computes CIELab from sRGB by way of CIE XYZ under a **D65** white, and omits the adaptation to D50. Read as `lab()`, such a value is not the color its author chose: the error is zero for grays, a few levels of 255 for muted colors, about 18 for a saturated green, and about 90 for pure blue, and it exceeds 18 levels for roughly a fifth of all sRGB colors. What the numbers *are* is CIELab relative to D65, and CSS has a name for the space they came from. The importer undoes the last step of dcmqi's computation, with dcmqi's constants:

- `fy = (L + 16) / 116`, `fx = fy + a / 500`, `fz = fy − b / 200`;
- for each of `fx`, `fy`, `fz`: `t = f³` if `f³ > 0.008856`, otherwise `t = (f − 16/116) / 7.787`;
- `X = 0.95047 × tx`, `Y = ty`, `Z = 1.08883 × tz`;

and writes `color(xyz-d65 X Y Z)` to seven decimal places. The same integers give `"color(xyz-d65 0.2459822 0.2813858 0.1198888)"`, which is the olive `#969451` the author saw, where the `lab()` reading is `#919450`. Seven decimal places recover the three integers exactly for every possible value, by running the steps backward: `t = X / 0.95047` and so on; `f = ∛t` if `t > 0.008856`, otherwise `7.787 × t + 16/116`; `L = 116 × fy − 16`, `a = 500 × (fx − fy)`, `b = 200 × (fy − fz)`; then the scaling, `L × 65535 / 100` and `(a + 128) × 65535 / 255`, likewise `b`, each taken to the nearest integer, halves rounding up. These constants are dcmqi's and are fixed here because a library's D65 CIELab may use slightly different ones, and the strings must not depend on the library.

*Which reading applies.* An importer reads the values as D65 when the object's `Manufacturer` is `QIICR` — what dcmqi writes; its `SoftwareVersions` is a build hash and identifies nothing — or when any value of `SoftwareVersions` is `cielab-d65`, which is how an exporter following this document marks the encoding (below). Otherwise it reads them as the standard intends. A caller that states which reading to use is obeyed whatever these attributes say. When the attribute is absent the color is left absent.

*Export.* An exporter writes the **D65 encoding by default**: it converts the segment's color to `xyz-d65` as CSS Color 4 defines — a `color(xyz-d65 …)` string needs no conversion — applies the backward steps above, and, when it has written at least one color this way, appends `cielab-d65` as the last value of the object's `SoftwareVersions`, creating the attribute if it is absent and adding nothing if the value is already there. On either encoding an integer that falls outside 0–65535 — a color far outside any display's gamut — is clamped to that range, and the clamping reported. This is a choice to follow the software that reads these objects rather than the text of the standard: written this way, a color displays as intended in 3D Slicer and everything built on dcmqi, and a color imported from a dcmqi file returns byte for byte. A caller may ask instead for the encoding the standard intends, in which case the color is converted to `lab()` as CSS Color 4 defines and scaled and rounded in the same way, and `cielab-d65` is not written. Converting a color between CSS spaces is floating-point arithmetic, and an integer may differ by one between implementations for a rare color; only the transcription of a `color(xyz-d65 …)` or `lab()` string back to the integers it came from is exact.

### 6.3 Reading Older Files

A reader for 0.8 that accepts older files migrates on load, and reports what the migration changed (§5). The steps run in this order, which matters because several of them read what others rewrite. The migrated extension declares `version` `"0.8"` and satisfies rules 1–4, 6–9 and 11–14. This assumes the older file conformed to its own version; what was invalid before may be invalid after. Rule 5 is carried over as it was found: older versions only recommended that a scheme be registered and had no `uri`, a migration cannot invent one, and a violation is reported as any other is. For rules 13 and 14 that rests on the older versions' own rules — 0.6 forbade the value 0 outright, and 0.7 allowed a layer one background leaf with one value that no other leaf could claim. Rule 15 depends on the array's `fill_value`, which a migration does not change; a file that breaks it is reported as any other is.

1. **Pre-0.6 shapes.** No specification of 0.5 survives in this repository; the reference for this step is the released library's pre-0.6 migration (`_migrate_segment_pre_0_6` and `_migrate_extension_pre_0_6` in `src/duckn/models.py`). In outline: a 0.5 `identifiers` object, a map from scheme to `{id, name}`, becomes entries appended to `designations` with `id` as `code` and `name` as `meaning`; the classification under a segment's `metadata.dicom` becomes the `dicom` field; and Slicer's fields move under `metadata.slicer`. A 0.8 reader that does not implement this step refuses a file whose version is below 0.6 rather than guessing.
2. **Simple fields.**

   | Older shape | 0.8 shape |
   |---|---|
   | `label_value: 5` (0.7; 0.6 scalar) | `label_values: [5]` |
   | `label_value: [3, 1, 3]` (0.6 list of integers) | `label_values: [1, 3]`, sorted and without repeats |
   | `background: true` (0.7) | `role: "background"`, **reported** |
   | `layer: 0` | omitted |
   | `color: [r, g, b]` floats | hex or `color(srgb …)`, chosen as in §6.1, **read as sRGB** — the first time that assumption is written down. A color that becomes `color(srgb …)` is rounded to six significant digits, which is finer than any display resolves, and the rounding is not reported |
   | `display` (multilingual names) | kept under `metadata.duckn.display`; translations are now external (§7.2) |
   | `SegmentAlgorithmType`, `SegmentAlgorithmName` under a segment's `metadata.dicom` | `dicom.algorithm_type`, `dicom.algorithm_name` |

3. **Groups are flattened, against the original ids.** A group is a 0.7 segment with `members`, or a 0.6 segment whose `label_value` has string entries, alone or mixed with integers. Its effective values are its own integers together with the values of every segment in its transitive membership. When those all lie in one layer, the group becomes a segment in **that layer** — whatever `layer` it declared itself — whose `label_values` is that set, sorted, **minus every value listed by a role-bearing segment in its transitive membership**, keeping the group's `id`, `name`, `color`, `designations`, `dicom`, and `metadata`, and dropping its `extent`. A group has no 0.8 form when its values span layers, when a member does not resolve, when the members form a cycle, or when nothing is left after the subtraction: it is omitted, its original entry is kept in the extension's `metadata.duckn.omitted` (§3.1), and the omission is reported. `disjoint` and `exhaustive` are dropped, and each discarded claim is reported. Every subtraction of a role-bearing member's values from a group is reported; where the group is designated, it may no longer cover its concept.
4. **Designation collisions are resolved.** Older versions did not forbid two segments of a layer from carrying the same designation (§4.1), and flattening creates more: a migrated group and the leaf that was its unresolved remainder usually share one (§2). For each designation carried by several segments of a layer, the one with the most `label_values` keeps it — the first in order, among equals — and on the others it moves to the segment's `metadata.duckn.designations`, each move reported. The result satisfies rule 9.
5. **Ids are made tokens**, as in §6.1, each original kept under `metadata.duckn.id` and each change reported, since references to the old id from outside the file no longer resolve.
6. **Order.** Rule 12 is established layer by layer, by one procedure. The positions in `segments` occupied by a layer's segments stay that layer's; the segments are re-dealt into those positions in this order: repeatedly take, from the layer's segments not yet placed, the first in their existing order whose `label_values` are strictly contained by those of no other segment not yet placed. Segments of different layers keep their relative order, and a layer that already satisfies rule 12 is unchanged. 0.7 resolved the color of an uncolored leaf from the *first* group in document order that contained it; 0.8 takes the *topmost* containing segment that has a color, which after this step is the most specific one. The two agree unless a 0.7 file listed a broader colored group before a narrower colored one; the migration reports each *(layer, value)* whose color resolves differently.

**What a migration cannot decide.**

- A 0.7 `background: true` may have meant "not evaluated": 0.7 offered FreeSurfer's "Unknown" as its example of a background, and 0.8 calls that region unknown (§8.5). Nothing in a 0.7 file distinguishes the two, so the role migrates as `background`, and every such migration is reported for review.
- Leaves that an earlier migration synthesized for 0.6 islands (`label_3`, named "label 3") are kept. They cannot be told from segments an author wrote, a redundant single-value segment violates nothing, and deleting one could leave a value undescribed.
- Colors that a pre-0.8 DICOM import computed from CIELab were read with a D65 white (§6.2). For the files dcmqi wrote, which is most of them, that reading recovered the author's color, and the migrated color is right, though the source integers cannot be recovered from it. For a file written as the standard intends the reading was wrong, and nothing in a 0.7 file says which kind it was.
- A released importer never captured DICOM's algorithm attributes, so a migrated file usually has no `dicom.algorithm_type`, and a DICOM export of it must be given one by its caller (§4.2).

Migration from 0.6 is nearly the identity, because 0.8's `label_values` is 0.6's list of integers made uniform. A 0.7 hierarchy survives as segments whose value lists are the transitive union of their descendants', which is what §2 asks of a natively written file too: the voxels of "Isocortex" are recoverable from the file alone. The file is *larger* for it — a deep atlas turns a few thousand member ids into many more integers. What is lost is the tree itself, the partition claims, and structures that spanned layers; the first two belong to the scheme and are supplied from outside (§7.2), and the last is a real loss of expressiveness, accepted.

### 6.4 Notes for an Implementation

*Not normative.* Version 0.8 changes what several conveniences of the released Python library can mean, beyond the fields it renames, and an implementation should say so rather than keep their signatures:

- A value no longer resolves to one segment. Lookups by value — `leaf_for`, `SegAccessor.segment(label_value=…)`, `name_for` — are keyed by *(layer, value)*, with `layer` defaulting to 0, and return every segment of that layer listing the value, with a variant returning the topmost. `label_for` returns a list of values.
- `background_value` returns the layer's one background value or nothing; returning 0 for a layer that has no background is a wrong answer, not a default.
- `color_map` returns CSS strings and applies the topmost-with-a-color rule; its `inherit` option goes.
- `parents_of`, `leaves_of`, `coverage_report`, and the group-related properties of a segment view have no counterpart.
- Validation needs the array's axes, data type, and `fill_value` as well as the extension, distinguishes errors from warnings, and returns diagnostics with the codes of §10 (§5); migration returns them too, which means it cannot live inside a model validator that has nowhere to put them.
- A `.seg.nrrd` writer may have to write voxel data (§6.1), and a DICOM SEG writer neither renumbers a `LABELMAP` nor refuses layers (§6.2).
- A resampler that carries `seg` forward applies §3.3: it drops `extent` and `legacy`, and refuses to interpolate a binary labelmap.
- Color needs a parser and converter for CSS Color 4, and should not be hand-written a second time. `coloraide` (Python, pure, no dependencies) and `colorjs.io` (JavaScript, by the CSS Color specification's editors) both parse the four forms and implement CSS's conversions and gamut mapping. Two things remain the implementation's own: restricting what a *reader* accepts to the four forms, since a general parser accepts all of CSS, and formatting what a *writer* emits to the fixed decimal places of §3.2. The D65 CIELab steps of §6.2 are five lines and are written out there so that they do not depend on a library's constants.
- Materialization and `LABELMAP` description each need one pass over the voxels, and can be done chunk by chunk; the cross-layer overlap test of §6.1 is cheapest as one occupancy mask per destination layer rather than pairwise comparison. Checking rule 16 is likewise a pass over the data, and is best offered as an option.

---

## 7. References From Outside the File

### 7.1 What the File Guarantees

Renditions, groupings, inexact correspondences, translations, and measurements live in documents outside the file. For those documents to be possible, this extension guarantees the following. How a reference is *spelled* — how an external document names an array and a segment within it — is that document's specification's business, not this one's; the convention's transform specification reserves a structured object carrying a `path` for referring to another array, and that is the likely model.

**Ids, and how far they are stable.** A segment's `id` is unique and is a token (§3.2), so it can be embedded in any reference syntax without escaping. It does not change when a segment is renamed, when `segments` is reordered, or when a 0.8 file is rewritten as a 0.8 file. It **may change** when an older file is migrated (§6.3), which is reported. It is **not** preserved across conversion to a format with no field for it: a round trip through DICOM loses it (§6.2); one through `.seg.nrrd` keeps it, since that format has an id of its own (§6.1).

**What is visible, and how far it is stable.** A segment's `layer` and `label_values` — its effective value set — are part of what an external document may rely on. They are the extent over which an external grouping is a union, over which disjointness is decided (§5), and from which a color table is built. They are stable as ids are, and **not** across conversion: exporting to `.seg.nrrd` or to DICOM `BINARY` and re-importing keeps every segment's voxels and changes its values and layer (§6). A document that depends on particular values applies to the file it was written against.

**Portable keys.** An id means something only in its own file. Three things mean the same in every file, and an external document may select by them with no file in hand:

- a **designation**. A key of scheme and code matches a segment carrying a designation with that code, in the same coding system, *at any position* in `designations`, whatever its modifier; a key that includes a modifier matches only an equal modifier. Matching is equality of codes, with no reasoning over a terminology's hierarchy. "The same coding system" is decided by `uri`, below, not by the key.
- a **role** — `background` or `unknown`. Selecting by role yields *(layer, value)* pairs: those of each segment with that role, which is also selectable by its id and by any designation it carries, and, for `background`, value 0 of each layer whose background is the implicit one, which has no segment and no id. A role-bearing segment is never an implicit member of a grouping defined over structures.
- **undescribed** — values present in the data that no segment lists (§2). This key is data-dependent: it can be evaluated only by reading the voxels, and a consumer working from metadata alone treats it as matching nothing. It is never matched by the `unknown` role.

The unresolved remainder of a hierarchical structure (§2) carries no designation and so has no portable key; an external document can reach it only by id.

**Scheme identity.** A scheme key such as `SCT` is a name local to one document; one file's `SCT` is another's `SNOMED`. Two keys name the same coding system when their registrations have the same `uri` (§3.1). A consumer reconciling keys by any weaker evidence — equal `name`, equal `url` — may do so and must report that it did.

**Scheme version.** `labeling_scheme` and its registered `version` identify which documents written for a scheme apply to this file. An external document states the versions it applies to, and the check is exact string equality against that list: `"2.4"` and `"2.4.0"` are different versions. A document applied to a version it does not list is reported as such, not silently applied.

**Stale references are reported.** An external document naming an id the file does not have has matched nothing, and that is an error to report. A code that no segment carries is normal — a cropped scan has no left kidney — and a checker reports which members of a grouping were absent (§10) rather than failing.

**The file is never modified** by applying an external document. A writer that embeds a result — a color taken from a stylesheet, written into `color` — has made a new recommendation, which is thereafter the file's own.

### 7.2 Notes on External Documents

*This section is not normative. The external documents are the part of the design most in flux, and their specification is deferred; these notes record what has been settled so far and where the drafts are.*

- **Renditions.** Several named renditions per file, a default, CSS-like rules (selector plus declaration block) with a cascade, `color` / `opacity` / `display` properties, explicit paint order, and flattening to a color table. `rendition-ext-spec.md` drafts this as an in-file extension; under the present scope it is source material for the external stylesheet format, and the file's own `color` values are the floor it cascades over — viewer, then file, then stylesheet. Recommended display windows for grayscale images are a separate matter from this extension, drafted in `presentation-extension.md`.
- **Groups and hierarchy.** A group has an id, a name, designations, and members given as selectors — normally designation codes in a labeling scheme, so that one definition of TotalSegmentator's groupings or the Allen structure graph serves every file that declares the scheme; by segment id for an ad hoc group in one study; or another group. Because a designated segment covers all of its concept (§2), a hierarchy document adds the *tree* — which structure is a child of which — and need not reconstruct any structure's voxels; it is also what makes rule 10 partly checkable. Claims of `disjoint` and `exhaustive` live on the group. `disjoint` is provable within a layer from value sets (§5). `exhaustive` generally is not checkable from one file: a member that matches nothing may be absent from the anatomy, cropped out of the scan, or missed, and nothing in the file says which.
- **Inexact correspondences.** `closeMatch`, `broadMatch`, `narrowMatch`, and `relatedMatch`, keyed by relation with lists of coded entries, in the manner of SKOS — including, perhaps, "remainder of" for the unresolved part of a structure. `semantic-ext-spec.md` drafts these, with the conceptual model — entities, bindings, the three kinds of meaning — that describes the external layer, where things other than segments (fiducials, class ranks) would be bound to the same vocabulary. Exact identifications are designations and stay in the file (§4.1).
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

The layer is a label table (§5). The three designations of each kidney are co-extensive, which is what lets them share a segment; TA2 has no laterality, and the cross-scheme modifier makes its designation exact all the same. The file goes to `.seg.nrrd` with its values unchanged. To DICOM it goes as `BINARY` with Segment Numbers 1 and 2, or as a `LABELMAP` keeping 2 and 3 if the caller asks for one; either way the caller must supply the left kidney's classification and algorithm type, which the file does not have, and for a `LABELMAP` a description of value 0.

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

Value 3 is listed twice and has no entry of its own. The lesion comes after the liver, so it is the topmost segment for value 3: that value takes the lesion's color, and a lookup of value 3 that wants one answer gets the lesion. Neither value list contains the other, so rule 12 asks nothing about their order. The artifact's value is its alone, as rule 14 requires of a role. The lesion is exactly a mass; that it may be a neoplasm is a weaker statement and is not a designation. Background is 0 by default and needs no segment. A Dice score against another reader's segmentation excludes the voxels at 9.

The layer is not a label table, so an export to `.seg.nrrd` materializes it (§6.1): the liver to layer 0 with a new value 1, the lesion — which overlaps the liver — to layer 1 with value 1, and the artifact to layer 0, where it overlaps nothing, keeping its value 9. It is not eligible for a DICOM `LABELMAP`, since the two segments listing value 3 are not nested, and exports as `BINARY`, given the classifications DICOM requires, which this file does not carry and the caller must supply.

### 8.3 Overlap by Layers

The same liver and lesion, authored independently in two layers of an array with a `list` axis. Both use value 1, which is unrelated between layers. Each layer is a label table, so the file goes to `.seg.nrrd` unchanged; it has two layers, so it is not eligible for a `LABELMAP`.

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "segments": [
    { "id": "liver", "name": "Liver", "label_values": [1], "color": "#dd8265" },
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

The segment designated 184 covers **all** of the frontal pole (rule 10): its own value and its layers'. Its values contain theirs, so it is listed first (rule 12); the layers are topmost for their own values, and only the unresolved voxels show 184's color. Those voxels need no segment of their own — value 184 is described — though a writer could add `{"id": "184-unresolved", "name": "Frontal pole, unresolved", "label_values": [184]}`, with no CCF designation, to name them. That 68 and 667 are *children* of 184 is the atlas's structure graph; it is the same for every volume labeled with CCF version 3 and is supplied from outside, keyed by these codes (§7.2).

On export, this file is not a label table, so it goes to `.seg.nrrd` as two layers — 184 in one, with a new value, its layers in the other keeping theirs — and to DICOM, with classifications from the caller, as three overlapping `BINARY` segments. It *is* eligible for a `LABELMAP`, since the layer is nested: the pixel data is written unchanged, values 68 and 667 are described by their layers, value 184 by segment 184, which is innermost for it, and value 0 — the layer's implicit background — by an item whose classification the caller supplies, with `PixelPaddingValue` 0. That item stands for the unresolved voxels only, and the exporter reports that segment 184 is only partly represented. The real CCF also has structure ids in the hundreds of millions, which no `LABELMAP` can hold (§6.2).

### 8.5 A Layer Whose Zero Is "Unknown"

FreeSurfer's `aseg` has no "nothing here" class; its 0 is "Unknown." The file withdraws the default background, and the layer has none, so a comparison over it has no negative class (§3.2 `role`).

```json
{
  "version": "0.8",
  "source_representation": "binary-labelmap",
  "implicit_background": false,
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

On export to `.seg.nrrd` the file is materialized, since a segment other than a background lists 0: the unknown region takes a positive value and keeps its role in a tag (§6.1). A DICOM `LABELMAP` keeps 0 and 17 as Segment Numbers, writes no `PixelPaddingValue`, and describes value 0 as an ordinary item with whatever classification the caller supplies; re-imported, that object has `implicit_background` `false` and a plain segment at 0, which is valid, the unknown role having been lost and reported.

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

**Why the scope narrowed.** Version 0.7 put groups and their claims in the file, and the design explored after it went further: peer extensions for meaning and for rendition, several renditions per file, a rule cascade, a measurement extension. Each piece answered a real need. But a duckn file's job is to let its voxels be interpreted faithfully, and to carry what the imaging formats it exchanges with carry. Measured against that, most of the new machinery was enrichment — and most of it was not even about one file: an atlas hierarchy, a house color scheme, and a cross-walk between ontologies are properties of a labeling scheme, written once and applied to thousands of files. Putting them in every file duplicates them and freezes them at whatever they were on the day of writing. So the file keeps the segment record that DICOM SEG and `.seg.nrrd` also keep, gains the two things interpretation was missing — overlap without synthetic segments, and the difference between "nothing here" and "not evaluated" — and guarantees the handles by which everything else can point at it. Those handles are the test's third clause, stated openly: ids, exact codes, scheme identities, and the labeling scheme interpret nothing and mostly round-trip nowhere, and they are what lets the rest stay out.

**Why `label_values` is a set, and always an array.** Through 0.6 `label_value` could be an integer, a list of integers, or a list mixing integers with segment ids; 0.7 made it one integer and moved unions into groups, which forced every overlap island to have a segment of its own and made migration synthesize segments named `label_3`. An island is not a segment. It is a cell of a Venn diagram whose whole meaning is which segments contain it, and a list of values per segment says exactly that with no extra entries. The field is plural, always an array, and sorted, so that there is one type, one code path, and one spelling: the 0.6 allowance that `1` and `[1]` were the same thing is what let a migration bug create two owners for one value.

**Why the topmost segment answers.** Once a value can belong to several segments, every convenience that used to return "the" segment for a value needs a rule, and there should be one rule. If a color table took the last segment and a lookup took the first, a viewer would paint the overlap red and call it "Liver." So the last segment of the layer, in `segments` order, that can answer is the answer, for color, for name, and for lookup alike, and the ordering rule makes that the most specific structure.

**Why a designated segment covers all of its concept.** Without groups, a hierarchical scheme leaves a choice: does the segment for an interior structure list only the voxels labeled with its own id, or those of its whole subtree? If writers may choose, a designation no longer determines a voxel set — "the volume of structure 184" has two answers and nothing in the file says which — and the designation is the portable key everything outside the file selects by. So the choice is made: all of the concept. It is also the only reading under which a designation is *exact*, since the voxels valued 184 alone are not the frontal pole, they are the part of it nobody subdivided. The rule is honest about its limits. It holds within a layer, because that is the scope of a value. It cannot reach into the background. And it cannot be verified from the file, because the fact that makes 68 part of 184 lives in the scheme; it is stated as a writer's obligation and a reader's assumption, with the one check an external hierarchy makes possible, so that no one goes looking for a validator that cannot exist. The cost is long value lists on broad structures, and files larger than their 0.7 equivalents; the benefit is that region queries are answerable from the file alone, and the external hierarchy document carries only the tree.

**Why a containing segment comes first.** Draw order needs the specific over the general, the topmost rule needs the specific to be last, and the `.seg.nrrd` exporter places segments in order. One metadata-checkable rule — containment of value lists within a layer — gives all three, asks nothing about segments that merely overlap, and is well defined where a scheme's hierarchy is not a tree, since it looks only at the value lists.

**Why one owner per value became the special case.** Version 0.7 required that a value resolve to exactly one segment, which is what makes a label table a label table — and what made overlap need groups. With sets, the requirement would forbid the mechanism. It survives as the *definition* of the case every simple consumer wants (§5): when it holds, a layer is one labelmap and one color table with no policy needed.

**Why groups left the file.** Two things seemed to need them. Overlap is handled by shared values. And a partition claim — these classes are mutually exclusive and exhaustive — is a fact about a binding that needs no group: the segments of a label-table layer are disjoint by construction, and a softmax layer is a partition of its classes by construction. What remains is the tree, and a tree belongs to its scheme. The Allen structure graph ships with the atlas, at a version; a file that names the scheme and version has said which graph applies, and one external definition keyed by code serves every such file. One thing is genuinely lost: a structure whose voxels span layers has no 0.8 form.

**Why `labeling_scheme`.** It is the smallest thing that makes the external layer work. Without it, an external hierarchy must be matched to a file by guesswork; with it, the file says "my segments are classes of this scheme at this version," each such segment carries its class as an exact designation, and everything written for the scheme joins by code. Carrying one is a warning rather than an error because real files have segments the scheme did not produce, and a rule that such files cannot declare their scheme would only mean the declaration was omitted. It records a definition, not a process: nothing about the run, the weights, or the inputs, which are provenance.

**Why schemes are identified by `uri`.** A scheme key is a local abbreviation, and the whole external layer joins files to documents by code. Two documents agree that `SCT` and `SNOMED` are one system only if something other than the key says so; `uri` is that thing, compared byte for byte, in the manner of a FHIR code system's canonical URL.

**Why designations are exact.** A designation is relied on as identity by every consumer that reads one. The alternative considered — a `relation` on each designation, or a `mappings` field beside them — is sound, and is where inexact correspondences will be recorded; but no reference format carries them, nothing about interpreting the voxels depends on them, and they are typically added by someone other than the producer, later. So in the file the rule is simply that a designation is exact or is not written. Exact identifications in further ontologies are designations like any other; laterality in schemes that lack it is reached exactly through a cross-scheme `modifier`, as DICOM does it.

**Why roles, and why two.** Background and unknown differ in what a reader may conclude: one is a negative that counts, the other is no claim and is excluded. No amount of styling recovers the distinction once both are painted with the same value, and it changes the result of every comparison and measurement, so it is interpretation and belongs in the file. It is a `role` enumeration rather than 0.7's `background` boolean so that the two are structurally exclusive. Reasons for being unknown are meanings and go where meanings go, which is what keeps the enumeration from growing. The reference formats mostly cannot say it — DICOM's `LABELMAP` can mark a background through its pixel padding value, and nothing marks "not evaluated" — so exporters report the loss, and the `.seg.nrrd` exporter keeps the role in a tag.

**Why `implicit_background`.** That 0 is background is true of nearly every labelmap and must stay the default, or every existing file changes meaning. But it is not true of all of them: FreeSurfer's 0 is "Unknown," an atlas may have a class at 0, and DICOM says outright that a label map has no background unless its pixel padding value names one. An earlier draft inferred "no background" from an unknown segment listing 0, which left a real class at 0 unrepresentable and made a conforming DICOM label map import as an invalid file. A declaration is the honest form of the exception. It is one flag for the whole array, not a list of layers, because a writer that withdraws the default can always declare the backgrounds it does have.

**Why a background segment has one value.** Neither reference format can express two: `.seg.nrrd`'s background is 0, and DICOM's pixel padding is one value. A region that is "outside the field of view" is not background anyway; it is unknown, and unknown segments may list as many values as they need.

**Why undescribed values are excluded but are not "unknown."** Version 0.6 called them implementation-defined, which meant a validator could not tell a legitimate implicit island from a bug. Making description a writer's obligation makes the rule checkable; giving readers a defined, conservative fallback keeps damaged files usable. They are kept distinct from the unknown role because one is a statement and the other is a defect: a stylesheet that grays out what was not evaluated should not also quietly gray out what a writer forgot.

**Why "report" is defined, with codes.** A format that is strict on write and tolerant on read produces, at every tolerance, something a person should know. If that goes to a log it is lost, and if it is an exception the tolerance is gone. So a report is a diagnostic returned beside the result; every rule says whether breaking it is an error or a warning, and whether a reader carries on and how; and the diagnostics are enumerated (§10), because two implementations cannot be tested against each other on "something was reported."

**Why `color` is a string on the segment, in four spellings.** DICOM SEG and `.seg.nrrd` each carry one recommended color per segment, so the file carries one. It is a CSS string because a triple of numbers is not a color until something says what space it is in, and no earlier version did. Each form exists so that a source's numbers can be written down as what they are, with no conversion: hex for what every color table holds; `color(srgb …)` for 3D Slicer's floats when they are not 8-bit values; `lab()` and `color(xyz-d65 …)` for the two things a DICOM segmentation may hold. CSS's `rgb()` was considered for the second case and dropped: its components run from 0 to 255, and Slicer's six significant digits on a 0-to-1 scale do not survive the change of scale — six decimal places recover about 98% of channel values, where `color(srgb …)` recovers all of them by construction. Each form has one spelling so that two writers produce the same string, and all four are required of every reader so that no conforming writer can hand a conforming reader a color it must discard.

**Why a DICOM color is transcribed, in one of two spaces.** The standard means CIELab under D50, which is CSS's `lab()`, and a value written that way is copied as it stands. But nearly every segmentation object in existence was written by software that computed CIELab under D65 and skipped the adaptation; read as `lab()`, those colors are wrong by as much as a third of the range. An earlier draft proposed to *correct* them on import — invert the faulty computation, round to hex, keep the source integers on the side. That is unnecessary. The numbers are a perfectly good color in a space CSS can name: CIELab relative to D65 is one step from `xyz-d65`, and the step is exact. So both kinds of file are transcribed, each into the form that says what its numbers are; nothing is corrected, nothing is rounded, the source integers are recoverable from the string, and the string itself shows which reading was applied. The reading is chosen by `Manufacturer`, which dcmqi sets to a fixed string.

**Why DICOM export uses the D65 encoding by default.** The standard is clear and almost no reader follows it. A segmentation exported with correct D50 values displays in the wrong colors in 3D Slicer and everything built on dcmqi, which is where these objects are opened. Interoperability is with software, so the default follows the software, the exporter marks what it did so that its own importer reads the values back correctly, and the encoding the standard intends is one option away for the day the ecosystem is fixed.

**Why `BINARY` is the default DICOM export.** A label map is the natural DICOM form of a label table, keeps its values, and is a different SOP Class that little deployed software can open. An exporter that chose it whenever it could would produce objects most recipients cannot read. So the choice is the caller's, the eligibility test is generous — nested value lists, not only label tables, so that an atlas qualifies — and the limits are stated: a 16-bit ceiling on values that real atlases exceed, and no way to write an interior structure as a whole.

**Why ids are tokens, and why references are not spelled here.** An id is the one part of a file that other documents embed. Stating the alphabet positively costs nothing for ids as they usually occur, and moves free text to `name`, where it was always meant to be. An earlier draft went further and defined a reference syntax, with dots and a `#`; but the convention's transform specification already reserves a structured object for referring to arrays, and how an external document names its targets is that document's design. The file's part is to make any spelling safe, which a token does. The price is paid at the edges: a converter derives a token from a source id that is not one, and an id cannot survive a format that has nowhere to keep it.

**Why tracking identifiers stay in `metadata.dicom`.** DICOM's Tracking ID and UID say that a segment here is the same finding as something in another object. That is cross-object identity — the external layer's business — and the attributes are optional in DICOM. They are kept for a round trip and given no meaning. The algorithm type is different: DICOM requires it of every segment, so it is a field, and an exporter that lacks it fails rather than writing `AUTOMATIC` over a radiologist's hand-drawn contour.

**Why `seg` is dropped when a labelmap is interpolated.** Most of the extension describes the array's values and rightly survives derivation. But a label value is a name, and the average of two names is not a name: linear resampling of a labelmap yields values no segment lists, and metadata that still claimed to describe them would be confidently wrong. The cached `extent` and the `legacy` strings are tied to a grid and a source file and go whenever either changes.

**Why `seg`, not `segmentation`.** The data model — layers, `source_representation`, representation conversion state — is inherited from 3D Slicer's `.seg.nrrd`, and the short name echoes that lineage. The Slicer-specific state is confined to `metadata.slicer`, so the first-class fields remain platform-neutral.

**Why `name`, not "label."** Every community this convention serves uses "label" for something different — an integer in imaging, a string in the semantic web, both in DICOM. The imaging sense cannot be moved, because "label map" and "label value" are the field's terms, so in this convention *label* means the integer exclusively, and the string is a `name`: what SKOS calls `prefLabel`, what schema.org calls `name`, what DICOM calls `SegmentLabel`. A coded entry's `meaning` is a different thing again — the terminology's rendering of a code, DICOM's `CodeMeaning`.

**Why `dicom` is separate from `designations`.** The DICOM Segmentation IOD has a specific classification structure (category → type → modifier, plus anatomic region → modifier) that does not map onto a flat list of codes, and it requires attributes — an algorithm type — that are about how a segment was made rather than what it is. Mixing these into `designations` would either force the DICOM structure onto non-DICOM uses, as `.seg.nrrd` does, or lose what DICOM round-tripping needs.

**Why `segments` is an array, not a map.** Segments have a natural order — creation order, UI order, draw order, and now specificity. An array preserves it; `id` provides lookup.

---

## 10. Diagnostics

Every report this document calls for has a code, and every code says what it is about, so that two implementations given the same input report the same codes about the same things.

### Rule violations

A violation of a numbered rule has the code `rule-N`. Rule 10 has none, since nothing can detect it.

| Code | About |
|------|-------|
| `rule-1`, `rule-3a`, `rule-3b`, `rule-3c` | the extension |
| `rule-2`, `rule-4b`, `rule-6`, `rule-7`, `rule-8a`, `rule-8b`, `rule-11a`, `rule-11b` | the segment that breaks the rule |
| `rule-4a` | each segment, after the first in `segments` order, that has the repeated id |
| `rule-5` | the scheme — one diagnostic per scheme, whichever of registration, `uri`, or `version` is missing |
| `rule-9` | each segment of the layer, after the first in `segments` order, that carries the repeated designation |
| `rule-12` | the containing segment that comes after a segment it strictly contains — one diagnostic per such segment, however many it contains |
| `rule-13` | each background segment of the layer after the first; or the background segment that lists more than one value |
| `rule-14` | the *(layer, value)* listed twice |
| `rule-15` | the *(layer, `fill_value`)* that nothing describes, once per layer |
| `rule-16` | the *(layer, value)* present in the data that nothing describes |
| `rule-17` | the segment that breaks the rule; the extension, for a missing `list` axis |

### Other diagnostics

| Code | Raised by | Severity | About | Meaning |
|------|-----------|----------|-------|---------|
| `color-unreadable` | reader | warning | segment | `color` is not in one of the four forms a reader accepts and is treated as absent (§3.2) |
| `color-gamut-mapped` | exporter, or a reader building an sRGB color table | warning | segment | a color was out of the sRGB gamut and was mapped into it (§3.2) |
| `color-clamped` | writer, exporter | warning | segment | a `color(srgb …)` component fell outside 0–1, or a CIELab integer outside 0–65535, and was clamped (§3.2, §6.2) |
| `no-negative-class` | a consumer comparing segmentations | warning | layer, as *(layer, —)* | the layer has no background, so false positives against it cannot be counted (§3.2) |
| `designation-unverified` | importer | warning | segment | a designation was taken from a source format's type code; its exactness is not established (§6.1, §6.2) |
| `designation-set-aside` | migration, importer | warning | segment | a designation moved to `metadata.duckn.designations` to satisfy rule 9 (§6.2, §6.3) |
| `dicom-items-kept` | importer | warning | segment | further modifier, region, or algorithm-name items were kept under `metadata.dicom` (§4.2) |
| `dicom-items-merged` | importer | warning | segment | agreeing `LABELMAP` items were merged into one multi-valued segment (§6.2) |
| `dicom-layers-combined` | importer, optional examination | warning | extension | `BINARY` segments were found disjoint and combined into one layer (§6.2) |
| `role-tag-invalid` | importer | warning | segment | a `duckn.role` tag had a value that is not a role and was left as a tag (§6.1) |
| `role-lost` | exporter | warning | segment | DICOM cannot express the segment's role (§6.2) |
| `background-not-written` | exporter | warning | segment | a background segment's `name` or codes were lost because the target's background has no segment (§6.1, §6.2) |
| `segment-not-represented` | exporter | warning | segment | the segment is innermost for no value and has no `LABELMAP` item (§6.2) |
| `segment-partly-represented` | exporter | warning | segment | the segment's `LABELMAP` item or items stand for only part of it (§6.2) |
| `label-truncated` | exporter | warning | segment | `SegmentLabel` was truncated to 64 characters (§6.2) |
| `nesting-exported-as-layers` | exporter | warning | extension | nested value lists were written as one `.seg.nrrd` layer per level (§6.1) |
| `values-renumbered` | exporter | warning | extension | materialization changed label values or layers (§6.1) |
| `id-changed` | migration, importer | warning | segment | an id was not a token and was replaced (§6.1, §6.3) |
| `migrated-background` | migration | warning | segment | `background: true` became `role: "background"`; it may have meant unknown (§6.3) |
| `migrated-group-omitted` | migration | warning | the omitted group, by its original id, one diagnostic per group | a group had no 0.8 form and was kept under `metadata.duckn.omitted` (§6.3) |
| `migrated-claim-dropped` | migration | warning | segment | a `disjoint` or `exhaustive` claim was discarded (§6.3) |
| `migrated-background-subtracted` | migration | warning | the flattened group's segment, one diagnostic per role-bearing member whose values were removed | a role-bearing member's value was removed from a flattened group (§6.3) |
| `migrated-color-differs` | migration | warning | (layer, value) | the value's color resolves differently than it did in 0.7 (§6.3) |
| `scheme-reconciled-weakly` | consumer of an external document | warning | scheme | two scheme keys were treated as one system without equal `uri`s (§7.1) |
| `scheme-version-mismatch` | consumer of an external document | warning | scheme | a document was applied to a scheme version it does not list (§7.1) |
| `group-member-absent` | consumer of an external document | warning | reference | a member of an external grouping matched no segment (§7.1) |
| `reference-stale` | consumer of an external document | error | reference | a document names an id the file does not have (§7.1) |

Failures — a refused file, an export that cannot be written — are not diagnostics and have no codes here; an implementation's errors should say which rule or which condition of §6.2 was the cause.
