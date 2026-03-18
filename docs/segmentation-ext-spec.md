# Segmentation Extension for duckn

**Extension name:** `slicerseg`
**Version:** 1.0
**Status:** Draft

---

## 1. Purpose

This document defines the `slicerseg` extension for the duckn convention. It replaces the `.seg.nrrd` metadata encoding — where segment properties were flattened into NRRD key/value pairs with `SegmentN_` prefixes and `~^|&`-delimited substructure — with a clean JSON representation.

The data model is the same. What changes is the encoding: structured objects replace string packing.

A secondary goal is to decouple segment identity from any single ontology. The `.seg.nrrd` `TerminologyEntry` tag hardcodes a DICOM Segmentation IOD classification pattern (category/type/region with SNOMED codes) as *the* way to describe what a segment contains. This extension separates two concerns:

- **Designations:** what the segment *is*, expressed in one or more coding systems (SNOMED-CT, FMA, TA2, NCIt, user-defined labels, etc.)
- **DICOM classification:** the category/type/region pattern needed specifically for DICOM SEG round-tripping

A segment can carry multiple designations from different ontologies simultaneously. The DICOM classification structure is available when needed, but is no longer the mandatory backbone of segment identity.

---

## 2. Data Layout

A segmentation is a Zarr array whose voxel values encode segment membership. For binary labelmaps, these are integer labels; for fractional labelmaps, they are continuous values. Spatial embedding (origin, directions, space) is described by the duckn convention fields as usual.

### Non-overlapping segments

The array has 3 spatial dimensions. Each voxel's integer value identifies which segment it belongs to (0 typically means background / no segment). This is the common case.

### Overlapping segments: layers

The array has a `list` axis (kind `"list"`) plus 3 spatial dimensions. Each position along the list axis is a **layer** — a 3D label volume. Segments that would collide in a single volume are assigned to different layers. Multiple segments may share the same label value if they are in different layers.

### Overlapping segments: label unions

An alternative to layers is to decompose the volume into non-overlapping **islands** and define each semantic segment as the union of one or more islands. The array remains a single 3D volume with no extra axis.

For example, a tumor that partially overlaps the liver can be decomposed into three islands — liver-only voxels (label 1), tumor-only voxels (label 2), and the overlap region (label 3). The "Liver" segment is then defined as `"label_value": [1, 3]` and the "Tumor" segment as `"label_value": [2, 3]`. The overlap region (label 3) appears in both segments' label value lists.

Islands are implicit: they exist as label values in the voxel data and do not require their own segment entries. However, an island *may* have an explicit segment entry if metadata is needed for it — for instance, to name the tumor-liver intersection zone or attach designations to it. In that case, its label value will appear both in its own entry and in the composite segments that include it.

Not all label values in the volume need to be described by a segment entry. Undescribed labels are implementation-defined (e.g., treated as "background" or "unknown"). A strict mode may require every label value to appear in at least one segment, but this is not the default.

The layer and label-union mechanisms are independent and may coexist in the same segmentation. They address overlapping segments through different strategies: layers duplicate the spatial volume; label unions partition it.

### Empty segmentation

Unlike `.seg.nrrd`, a Zarr store does not require non-empty data. An empty segmentation can be represented as a zero-extent array or by providing only the extension metadata with no voxel data. The single-voxel sentinel hack is not needed.

---

## 3. Extension Fields

The `slicerseg` extension is declared at the top level of the `"duckn"` object's `"extensions"` and carries the array-wide segmentation metadata. Per-segment metadata lives in a `"segments"` array within this object.

### 3.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification.

```json
"version": "1.0"
```

#### `source_representation`

The representation type stored in this file.

| Value | Description |
|-------|-------------|
| `"binary-labelmap"` | Integer labels, one value per segment per layer |
| `"fractional-labelmap"` | Continuous values representing partial volume or probability |

```json
"source_representation": "binary-labelmap"
```

#### `contained_representations`

An array of representation names that an application should be prepared to generate from this data.

```json
"contained_representations": ["binary-labelmap", "closed-surface"]
```

#### `conversion_parameters`

An object whose keys are parameter names and whose values are objects with `value` and optionally `description`.

```json
"conversion_parameters": {
  "Smoothing factor": {
    "value": "0.5",
    "description": "Fraction of Gaussian standard deviation relative to voxel size"
  },
  "Decimation factor": {
    "value": "0.0",
    "description": "Target reduction of triangle count (0 = no decimation)"
  }
}
```

This replaces the `&`-and-`|`-delimited `Segmentation_ConversionParameters` string.

#### `reference_extent_offset`

A 3-element array `[i, j, k]` giving the voxel-coordinate offset of this array's origin relative to a reference image grid. Allows reconstructing the original image extent when the segmentation covers only a subregion.

```json
"reference_extent_offset": [100, 50, 0]
```

#### `terminologies`

An object registering the coding systems used in segment designations. Each key is a short identifier for the system (used in `designations[].scheme`); each value is an object with metadata about that system.

```json
"terminologies": {
  "SCT": {
    "name": "SNOMED Clinical Terms",
    "version": "2024-09-01",
    "url": "https://browser.ihtsdotools.org"
  },
  "FMA": {
    "name": "Foundational Model of Anatomy",
    "url": "http://purl.org/sig/ont/fma/"
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

This field serves two purposes: it provides provenance for every coded entry in the file, and it gives readers (human or machine) a resolvable pointer to the source of truth for each coding system. The keys defined here are the valid values for `scheme` in designation objects (§4.1).

If a coding system is used in any segment's `designations`, it should be registered here. Systems not listed in `terminologies` may still appear in designations (the file remains valid), but without the registry entry, readers have less context.

#### `segments`

An array of segment objects. Each describes one semantic region in the segmentation. The array index is the segment's ordinal position — it replaces the `N` in `SegmentN_*`.

---

### 3.2 Segment Object Fields

Each element of the `segments` array is a JSON object with the following fields. All fields are optional except `id` and `label_value`.

#### `id`

A stable, unique identifier for the segment within this segmentation. Does not change when the segment is renamed.

```json
"id": "Segment_1"
```

#### `name`

The human-readable display name. This is the user-facing label, independent of any ontology. It may be user-authored, auto-generated from terminology, or a local nickname for a structure.

```json
"name": "Right kidney"
```

#### `display`

An optional object providing the segment's display name in additional languages, keyed by BCP 47 language tags. Same semantics as `display` on coded entries (§4.1): `name` is the default fallback; `display` provides translations.

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

#### `name_auto_generated`

Boolean. `true` if the name was generated automatically (e.g., derived from terminology); `false` or absent if the user chose it.

#### `color`

Display color as an RGB array with values in [0.0, 1.0].

```json
"color": [0.89, 0.85, 0.78]
```

#### `color_auto_generated`

Boolean. `true` if the color was generated automatically; `false` or absent if the user chose it.

#### `label_value`

The label value or values used to represent this segment in its layer. Required.

When `label_value` is a single integer, the segment occupies all voxels with that value:

```json
"label_value": 1
```

When `label_value` is an array of integers, the segment is the union of all voxels whose value matches any element in the array:

```json
"label_value": [1, 3, 7]
```

A reader should treat a bare integer as equivalent to a single-element array. The array form enables representing overlapping structures in a single volume without layers (see §2, "Overlapping segments: label unions").

#### `layer`

The zero-based index of the layer (position along the `list` axis) that contains this segment. Omit for non-overlapping segmentations where there is only one layer (implicitly 0).

```json
"layer": 0
```

#### `extent`

The bounding box of the non-empty region within the segment, as a 6-element array: `[min_i, max_i, min_j, max_j, min_k, max_k]` in voxel coordinates.

```json
"extent": [45, 102, 30, 98, 12, 55]
```

#### `designations`

An array of coded entries identifying what this segment represents, drawn from any number of coding systems. See §4.1. This is the primary mechanism for segment identity.

#### `dicom`

An optional object providing the DICOM Segmentation IOD classification structure (category, type, type modifier, anatomic region, region modifier). See §4.2. Present only when DICOM SEG interoperability is needed.

#### `tags`

An object for arbitrary key/value metadata associated with this segment. Keys and values are strings. This is the open-ended escape hatch — anything that doesn't have a dedicated field goes here.

```json
"tags": {
  "Status": "reviewed",
  "Operator": "JDoe"
}
```

---

## 4. Segment Identity

Segment identity is modeled in two layers:

1. **Designations** — a flat list of coded entries saying "this segment is *X*" in various ontologies, plus a user-level display name. This is the general-purpose mechanism. Most segments need only this.

2. **DICOM classification** — the specific category/type/modifier/region structure required by DICOM Segmentation IOD. This is needed only for DICOM round-tripping and is expressed as a separate optional object that provides its own coded entries.

The two layers are complementary. A segment can have designations without a DICOM block (common for research or non-clinical use). It can have a DICOM block without standalone designations (if the DICOM classification is the only labeling). Or it can have both, with the DICOM block providing the classification structure and the designations providing broader ontological coverage.

### 4.1 Designations

Each entry in the `designations` array identifies the segment in one coding system:

```json
"designations": [
  {
    "scheme": "SCT",
    "code": "64033007",
    "meaning": "Kidney",
    "url": "https://browser.ihtsdotools.org/?perspective=full&conceptId1=64033007"
  },
  {
    "scheme": "FMA",
    "code": "7203",
    "meaning": "Kidney",
    "url": "http://purl.org/sig/ont/fma/fma7203"
  },
  {
    "scheme": "TA2",
    "code": "5765",
    "meaning": "Kidney",
    "display": {
      "la": "Ren",
      "en": "Kidney"
    }
  }
]
```

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `scheme` | yes | string | Coding system identifier. Should match a key in the top-level `terminologies` registry. |
| `code` | yes | string | Code value within the system |
| `meaning` | yes | string | Primary human-readable label for the code |
| `display` | no | object | Additional display names keyed by BCP 47 language tag (see below) |
| `url` | no | string | Resolvable URL for this specific concept — a browser link, persistent identifier, or ontology IRI |
| `modifier` | no | object | A qualifier on the primary code (e.g., laterality). Same shape: `{scheme, code, meaning, display, url}` |

#### Multilingual display names

The `meaning` field is the default display string — the label a reader should show when no language preference is specified. The optional `display` object provides the same concept's name in additional languages, keyed by [BCP 47](https://www.rfc-editor.org/info/bcp47) language tags:

```json
{
  "scheme": "TA2",
  "code": "5765",
  "meaning": "Kidney",
  "display": {
    "la": "Ren",
    "en": "Kidney",
    "de": "Niere",
    "ja": "腎臓"
  }
}
```

Rules:

- `meaning` is always present and always a plain string. It is the fallback for readers that do not inspect `display`.
- `display` keys are BCP 47 language tags: `"en"`, `"la"`, `"de"`, `"ja"`, `"zh-Hans"`, etc.
- The value of `meaning` should also appear under the appropriate language key in `display` when `display` is present, so that the `display` object is self-contained. A reader using `display` can ignore `meaning`; a reader not using `display` can ignore it.
- `display` on a `modifier` follows the same rules.

This pattern follows FHIR's approach to multilingual coded concepts (CodeSystem designations with language tags) and aligns with BCP 47 usage in JSON-LD, HTML, and HTTP.

For anatomical terminology, the most common case is Latin (`"la"`) alongside a modern clinical language (`"en"`, `"de"`, etc.). TA2, for example, defines both Latin and English names for every structure. FMA uses English. SNOMED uses English with translations maintained by national release centers.

#### Modifiers

The `modifier` field handles the common case where a concept needs qualification — typically laterality, but the pattern is general:

```json
{
  "scheme": "SCT",
  "code": "64033007",
  "meaning": "Kidney",
  "modifier": {
    "scheme": "SCT",
    "code": "24028007",
    "meaning": "Right",
    "display": {
      "en": "Right",
      "la": "Dexter"
    }
  }
}
```

#### Ordering and relationship to `name`

**Ordering:** The first designation in the array is the preferred / primary identification. Readers that can only handle one coded identity should use the first entry.

**Relationship to `name`:** The `name` field on the segment is the user-facing display label. It may echo a designation's `meaning`, or it may be entirely different ("Bob's left kidney"). The two are independent. `name` is what you show in the UI; `designations` are what you use for computation, interoperability, and lookup.

### 4.2 DICOM Classification

The `dicom` object provides the seven-part classification structure defined by the DICOM Segmentation IOD. This is the information needed to write a DICOM SEG object.

```json
"dicom": {
  "category": {
    "scheme": "SCT",
    "code": "49755003",
    "meaning": "Morphologically abnormal structure"
  },
  "type": {
    "scheme": "SCT",
    "code": "4147007",
    "meaning": "Mass"
  },
  "anatomic_region": {
    "scheme": "SCT",
    "code": "23451007",
    "meaning": "Adrenal gland"
  },
  "anatomic_region_modifier": {
    "scheme": "SCT",
    "code": "24028007",
    "meaning": "Right"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `category` | coded entry | Segmentation Category (e.g., "Morphologically abnormal structure", "Tissue", "Body structure") |
| `type` | coded entry | Segmentation Type within the category (e.g., "Mass", "Neoplasm") |
| `type_modifier` | coded entry | Qualifier on the type. Omit if not applicable. |
| `anatomic_region` | coded entry | Anatomic region |
| `anatomic_region_modifier` | coded entry | Qualifier on the region (typically laterality). Omit if not applicable. |

Each coded entry has the same shape as a designation: `{scheme, code, meaning}`, optionally with `display` and `url`. The `scheme` values should match keys in the top-level `terminologies` registry. Following the convention's "absent means unknown" principle, omit optional fields entirely rather than setting them to `null`.

The `.seg.nrrd` `TerminologyEntry` also stored two "context name" strings (e.g., "Segmentation category and type - 3D Slicer General Anatomy list" and "Anatomic codes - DICOM master list"). These named the lookup tables used by 3D Slicer's terminology selector UI. They are application state, not data semantics, and are intentionally omitted here. If an application needs to record which terminology context was active, it can use `tags`.

### 4.3 Relationship Between Designations and DICOM

The `designations` and `dicom` fields are independent — they do not reference each other. The same SNOMED code might appear in both, and that's fine. They serve different purposes:

- `designations` answers: "What is this structure, in any ontology?"
- `dicom` answers: "How should this segment be classified in a DICOM Segmentation IOD?"

When converting to DICOM SEG, a writer should use the `dicom` block if present. When performing ontology-based lookup, matching, or cross-referencing, a reader should use `designations`.

### 4.4 Absence and Omission

Following the duckn convention's "absent means unknown" principle:

- If a segment has no designations, omit the `designations` field. Do not include an empty array.
- If a segment has no DICOM classification, omit the `dicom` field. Do not include an empty object.
- Within the `dicom` object, omit `type_modifier` and `anatomic_region_modifier` when they do not apply. Do not use `null`.
- If no terminology registrations are needed, omit the `terminologies` field entirely.

---

## 5. Consistency Rules

- The length of `segments` is independent of any axis size — multiple segments can share a label value across layers, and multiple segments can exist in the same layer with different label values.
- Where a segment specifies a `layer`, there must be a `list`-kind axis in the array, and the `layer` value must be a valid index into that axis.
- Where a `kind` constraint requires a specific axis size (from the duckn convention), the corresponding `shape` element must match.
- `scheme` values used in `designations` or `dicom` coded entries should have a corresponding key in the top-level `terminologies` registry. This is not strictly required (the file is valid without it), but it provides provenance and discoverability.
- When `label_value` is a single integer: it must be unique within a given layer. Two segments in the same layer with the same scalar `label_value` would be ambiguous.
- When `label_value` is an array: no two segments in the same layer may have identical `label_value` arrays (compared as sets). Individual label values *may* appear in multiple segments' arrays — this is the mechanism for representing overlapping structures. However, the overall set of label values for each segment must be distinct.
- `id` must be unique across all segments in the segmentation.
- Not all label values present in the voxel data need to appear in a segment entry. Undescribed label values are implementation-defined.

---

## 6. Mapping from `.seg.nrrd`

| `.seg.nrrd` field | duckn `slicerseg` extension field |
|---|---|
| `Segmentation_MasterRepresentation` / `Segmentation_SourceRepresentation` | `source_representation` |
| `Segmentation_ContainedRepresentationNames` | `contained_representations` (array) |
| `Segmentation_ConversionParameters` | `conversion_parameters` (object) |
| `Segmentation_ReferenceImageExtentOffset` | `reference_extent_offset` (array) |
| `SegmentN_ID` | `segments[n].id` |
| `SegmentN_Name` | `segments[n].name` |
| — (no `.seg.nrrd` equivalent) | `segments[n].display` (multilingual names) |
| `SegmentN_NameAutoGenerated` | `segments[n].name_auto_generated` (boolean) |
| `SegmentN_Color` | `segments[n].color` (RGB array) |
| `SegmentN_ColorAutoGenerated` | `segments[n].color_auto_generated` (boolean) |
| `SegmentN_LabelValue` | `segments[n].label_value` (integer or array of integers) |
| `SegmentN_Layer` | `segments[n].layer` (integer) |
| `SegmentN_Extent` | `segments[n].extent` (6-element array) |
| `SegmentN_Tags` (minus TerminologyEntry) | `segments[n].tags` (object) |
| `SegmentN_Tags` TerminologyEntry — category/type/modifier/region | `segments[n].dicom` (object) |
| `SegmentN_Tags` TerminologyEntry — type code (e.g., SCT code for the structure) | `segments[n].designations` (first entry) |
| `SegmentN_Tags` TerminologyEntry — context names | Omitted (application state) |

### Parsing Notes

Implementers converting `.seg.nrrd` files should be aware of the following encoding differences:

- **Master vs Source representation**: 3D Slicer renamed `Segmentation_MasterRepresentation` to `Segmentation_SourceRepresentation` around version 5.3. Converters should accept either key.
- **Representation names**: `.seg.nrrd` uses title-case names (e.g., `"Binary labelmap"`, `"Closed surface"`). Normalize to kebab-case (`"binary-labelmap"`, `"closed-surface"`).
- **Pipe-delimited lists**: `Segmentation_ContainedRepresentationNames` and `Segmentation_ConversionParameters` use `|` and `&` as delimiters, often with trailing separators. Split on the delimiter and drop empty elements.
- **Tags string**: `SegmentN_Tags` is a `|`-delimited sequence of `key:value` pairs. Strip the `Segmentation.` prefix from tag keys (e.g., `Segmentation.Status` → `Status`). The `TerminologyEntry` key is parsed into the `dicom` and `designations` fields per the mapping table above.
- **Escaped newlines in descriptions**: `ConversionParameters` description strings may contain literal `\n` escape sequences representing newlines.

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
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [1, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 1, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 2],
          "unit": "mm"
        }
      ],
      "extensions": {
        "slicerseg": {
          "version": "1.0",
          "source_representation": "binary-labelmap",
          "contained_representations": ["binary-labelmap", "closed-surface"],
          "terminologies": {
            "SCT": {
              "name": "SNOMED Clinical Terms",
              "version": "2024-09-01",
              "url": "https://browser.ihtsdotools.org"
            },
            "FMA": {
              "name": "Foundational Model of Anatomy",
              "url": "http://purl.org/sig/ont/fma/"
            },
            "TA2": {
              "name": "Terminologia Anatomica 2nd Edition",
              "url": "https://ta2viewer.openanatomy.org"
            }
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
                  "url": "https://browser.ihtsdotools.org/?perspective=full&conceptId1=64033007",
                  "modifier": {
                    "scheme": "SCT",
                    "code": "24028007",
                    "meaning": "Right"
                  }
                },
                {
                  "scheme": "FMA",
                  "code": "7205",
                  "meaning": "Right kidney",
                  "url": "http://purl.org/sig/ont/fma/fma7205"
                },
                {
                  "scheme": "TA2",
                  "code": "5767",
                  "meaning": "Right kidney",
                  "display": {
                    "la": "Ren dexter",
                    "en": "Right kidney"
                  }
                }
              ],
              "dicom": {
                "category": {
                  "scheme": "SCT",
                  "code": "123037004",
                  "meaning": "Body structure"
                },
                "type": {
                  "scheme": "SCT",
                  "code": "64033007",
                  "meaning": "Kidney"
                },
                "anatomic_region": {
                  "scheme": "SCT",
                  "code": "64033007",
                  "meaning": "Kidney"
                },
                "anatomic_region_modifier": {
                  "scheme": "SCT",
                  "code": "24028007",
                  "meaning": "Right"
                }
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
                  "modifier": {
                    "scheme": "SCT",
                    "code": "7771000",
                    "meaning": "Left"
                  }
                },
                {
                  "scheme": "FMA",
                  "code": "7204",
                  "meaning": "Left kidney",
                  "url": "http://purl.org/sig/ont/fma/fma7204"
                },
                {
                  "scheme": "TA2",
                  "code": "5766",
                  "meaning": "Left kidney",
                  "display": {
                    "la": "Ren sinister",
                    "en": "Left kidney"
                  }
                }
              ],
              "dicom": {
                "category": {
                  "scheme": "SCT",
                  "code": "123037004",
                  "meaning": "Body structure"
                },
                "type": {
                  "scheme": "SCT",
                  "code": "64033007",
                  "meaning": "Kidney"
                },
                "anatomic_region": {
                  "scheme": "SCT",
                  "code": "64033007",
                  "meaning": "Kidney"
                },
                "anatomic_region_modifier": {
                  "scheme": "SCT",
                  "code": "7771000",
                  "meaning": "Left"
                }
              }
            }
          ]
        }
      }
    }
  }
}
```

### 7.2 Overlapping Segments with Layers

A segmentation with two overlapping segments, requiring two layers:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [2, 256, 256, 128],
  "data_type": "uint8",
  "dimension_names": ["layer", "i", "j", "k"],
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
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [1, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 1, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 2],
          "unit": "mm"
        }
      ],
      "extensions": {
        "slicerseg": {
          "version": "1.0",
          "source_representation": "binary-labelmap",
          "segments": [
            {
              "id": "Segment_1",
              "name": "Tumor",
              "label_value": 1,
              "layer": 0,
              "color": [0.8, 0.2, 0.2],
              "designations": [
                {
                  "scheme": "SCT",
                  "code": "108369006",
                  "meaning": "Neoplasm"
                }
              ]
            },
            {
              "id": "Segment_2",
              "name": "Liver",
              "label_value": 1,
              "layer": 1,
              "color": [0.2, 0.6, 0.8],
              "designations": [
                {
                  "scheme": "SCT",
                  "code": "10200004",
                  "meaning": "Liver"
                }
              ]
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

The same tumor-liver overlap from §7.2, represented as label unions in a single 3D volume instead of layers:

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
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [1, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 1, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 2],
          "unit": "mm"
        }
      ],
      "extensions": {
        "slicerseg": {
          "version": "1.0",
          "source_representation": "binary-labelmap",
          "segments": [
            {
              "id": "Segment_1",
              "name": "Tumor",
              "label_value": [2, 3],
              "color": [0.8, 0.2, 0.2],
              "designations": [
                {
                  "scheme": "SCT",
                  "code": "108369006",
                  "meaning": "Neoplasm"
                }
              ]
            },
            {
              "id": "Segment_2",
              "name": "Liver",
              "label_value": [1, 3],
              "color": [0.2, 0.6, 0.8],
              "designations": [
                {
                  "scheme": "SCT",
                  "code": "10200004",
                  "meaning": "Liver"
                }
              ]
            }
          ]
        }
      }
    }
  }
}
```

Label 1 is liver-only voxels, label 2 is tumor-only voxels, and label 3 is the overlap region where both structures are present. The island at label 3 has no explicit segment entry — it exists only as a shared label value in the two composite segments. No layers are needed.

### 7.4 Research Segmentation Without DICOM

A segmentation from a research pipeline using only FMA codes, no DICOM classification needed:

```json
"extensions": {
  "slicerseg": {
    "version": "1.0",
    "source_representation": "binary-labelmap",
    "terminologies": {
      "FMA": {
        "name": "Foundational Model of Anatomy",
        "url": "http://purl.org/sig/ont/fma/"
      }
    },
    "segments": [
      {
        "id": "S1",
        "name": "Left ventricle",
        "label_value": 1,
        "designations": [
          {
            "scheme": "FMA",
            "code": "7101",
            "meaning": "Left ventricle",
            "url": "http://purl.org/sig/ont/fma/fma7101"
          }
        ]
      },
      {
        "id": "S2",
        "name": "Right ventricle",
        "label_value": 2,
        "designations": [
          {
            "scheme": "FMA",
            "code": "7098",
            "meaning": "Right ventricle",
            "url": "http://purl.org/sig/ont/fma/fma7098"
          }
        ]
      }
    ]
  }
}
```

No `dicom` block — the segments are identified purely by FMA designations.

### 7.5 Minimal

A segmentation with the smallest useful metadata:

```json
"extensions": {
  "slicerseg": {
    "version": "1.0",
    "segments": [
      { "id": "S1", "label_value": 1, "name": "Liver" },
      { "id": "S2", "label_value": 2, "name": "Spleen" }
    ]
  }
}
```

---

## 8. Design Notes

**Why `slicerseg`, not `segmentation`.** This extension's data model — layers, `source_representation`, `contained_representations`, `conversion_parameters`, `reference_extent_offset` — is inherited directly from 3D Slicer's `.seg.nrrd` format. Naming it `slicerseg` makes that lineage explicit and reserves the generic `segmentation` (or `seg`) namespace for a future platform-neutral extension that retains the broadly useful parts (segments, designations, label unions, DICOM classification) without the Slicer-specific fields.

**Why `designations` is an array.** A segment is a real anatomical or pathological entity. Different communities identify that entity using different coding systems. A kidney is SNOMED 64033007, FMA 7203, TA2 5765, and NCIt C12415 — simultaneously. An array of coded entries makes this multiplicity explicit and avoids privileging any single ontology. The first entry is the preferred identification.

**Why `dicom` is separate from `designations`.** The DICOM Segmentation IOD has a specific classification structure (category → type → modifier, plus anatomic region → modifier) that doesn't map cleanly to a flat list of codes. It is a *classification* pattern, not just an *identification* pattern. Mixing the two would either force the DICOM structure onto non-DICOM use cases (as `.seg.nrrd` does) or lose the structure needed for DICOM round-tripping. Keeping them separate means each concern has the right shape.

**Why the `terminologies` registry exists.** When a segment carries a code like `SCT:64033007`, a reader benefits from knowing what "SCT" means, what version was used, and where to look it up. The top-level `terminologies` object provides this once, rather than repeating it on every coded entry. The `url` field on individual designations points to the specific *concept*; the `url` in the registry points to the coding *system*.

**Why `name` is independent of designations.** Users name segments in ways that don't match any ontology: "suspect lesion #3", "Bob's left kidney", "ROI for dosimetry". The display name is a user-facing label that should be preserved exactly as given. Ontology codes are for interoperability; `name` is for the human in the loop. The optional `display` dict allows the display name itself to be multilingual — for example, a segmentation created in a German-speaking hospital can carry both the German and English segment names, independent of what any ontology calls the structure.

**Why `color` is here at all.** Color is technically a display hint, which the duckn convention generally avoids. However, segment color is so universally used in segmentation workflows — and so tightly bound to segment identity — that omitting it would force every application to reinvent a color-assignment scheme. It is a recommended display color, not a mandate.

**Why `segments` is an array, not a map.** Segments have a natural ordering (the order in which they were created or appear in the UI). An array preserves this. The `id` field provides stable lookup when ordering is irrelevant.

**Relationship to DICOM SEG.** The `dicom` classification object and the coded entry shape (`scheme`/`code`/`meaning`) are designed to be losslessly convertible to and from DICOM Segmentation IOD segment descriptions. The coded entry triplet maps directly to DICOM's `CodeSequence` items. The Slicer-specific "context name" strings are omitted — they named UI lookup tables, not data semantics.

**Why `label_value` accepts arrays.** Overlapping structures are common in medical imaging — a tumor invading an organ, nested anatomical regions, or probabilistic boundaries. The layer mechanism handles this by duplicating the spatial volume, which is correct but expensive. Label unions offer an alternative: decompose the scene into non-overlapping islands (each with a unique label value in a single volume), then define each semantic segment as the union of one or more islands. The overlap region becomes a shared island. This is lossless, compact, and scales to many overlapping structures without adding dimensions. The two mechanisms coexist because they serve different workflows: layers are natural when segments are authored independently; label unions are natural when the decomposition into non-overlapping regions is computed upfront (e.g., by a segmentation pipeline that produces disjoint partitions).