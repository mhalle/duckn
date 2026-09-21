# Semantic Extension for duckn

**Extension name:** `semantic`
**Version:** 0.1
**Status:** Draft — superseded as an in-file design (see below)

> **Superseded as an in-file design; retained as source material.** This document was written under a design in which meaning and presentation were peer in-file extensions and `seg` was reduced to value-to-entity bindings. That design was not adopted. The in-file format is defined by `segmentation-ext-v08-spec.md` (identity, roles, `label_values`, and one recommended `color` per segment) and `presentation-extension.md` (recommended grayscale windows and inversion). **Nothing in this document describes fields that a duckn file carries, and no reader or writer should implement it as an extension.** It is kept because the external-document layer sketched in §7.2 of the segmentation draft — groups and hierarchy, claims, inexact correspondences, stylesheets — will be specified from this material. Where this document and §7 of that draft disagree, that draft wins. In particular: `seg` 0.8 does **not** reference entities by id; colors are **not** moved into a rendition; there is no in-file `semantic` or `rendition` extension and no in-file default rendition; format mappings for segment colors and display windows are those of the two documents named above, not the ones here; and the coded-entry registry is `seg.terminologies`.

---

## 1. Purpose

This document defines the `semantic` extension for the duckn convention. It carries **domain meaning** — what the things described by an array *are* — separately from how they are encoded in the data and from how they are shown.

A voxel set is a region, a coordinate is a point, and two lines make an angle, whether or not anyone knows what part of the body they belong to. None of them is a liver until something says so. This extension is the place where that is said: it declares **entities**, identifies each with codes in one or more coding systems, and states how entities relate to one another. Other extensions — a segmentation's label values, a fiducial set's points, a probability array's class ranks — **bind** their data to those entities by `id`. The binding supplies the geometry; this extension supplies the meaning.

Through version 0.7 of the segmentation extension this content lived inside `seg`, inherited from the `.seg.nrrd` format, which packs a segment's value, name, codes, and color into one record. Separating it lets the same vocabulary serve every kind of binding, lets it be versioned on its own, and leaves `seg` to say only which values are which entity.

---

## 2. Model

### Three kinds of meaning

A duckn array carries three kinds of meaning, and only one of them is conferred rather than intrinsic.

- **Physical** meaning belongs to the samples: `sample_units` says a value is a Hounsfield unit whether or not anyone knows what organ it lies in. The convention itself carries this.
- **Geometric** meaning belongs to a datum's kind: a set of voxels is a region, a coordinate is a point. Each binding extension carries this for the kind of data it binds.
- **Domain** meaning — *this region is the liver*, *this point is the nasion* — is intrinsic to nothing. It arrives only when a datum is bound to an entity declared here.

Everything anatomical, pathological, or otherwise domain-specific in a duckn store enters through that one door.

### Entities

An **entity** is a real thing that the data describes some part of: an organ, a lesion, a landmark, a tissue class, a region of an atlas. It has a stable `id`, usually a human `name`, and its identity in the world is given by **designations** — codes in registered coding systems. An entity says nothing about voxels or coordinates. Which values or points belong to it is a binding extension's business (§6).

An entity is either a **leaf** or a **group**. A leaf is bound directly by data. A group names other entities as its `members` and is their union; it owns no data of its own, and no binding may reference it. Groups may nest, and an entity may belong to any number of groups, so the membership graph is a directed acyclic graph, not a tree.

### Claims

A group is a union of its members and, by default, claims nothing more. Two optional booleans let it claim more:

- `"disjoint": true` — no two members share any of the data bound to them. The members' volumes add.
- `"exhaustive": true` — the members exhaust the thing the group names: every part of that thing present in the data is bound to some member, and nothing else in the data is that thing.

A group claiming both is a **partition** of what it names — mutually exclusive and collectively exhaustive — which is the case under which statistical statements about the group reduce to statements about disjoint atoms: probabilities sum to one, and the whole is the sum of its parts. The classes of a softmax model are a partition of the model's domain, background included; the eight Couinaud segments are a partition of the liver.

`exhaustive` may also appear on a leaf, where it claims that the leaf's bound data is all of its concept present in the volume. A liver segment from a scan cropped mid-organ is exactly liver — every voxel in it is liver — but it is not exhaustive of the liver. Coverage is a claim; it is never expressed by weakening the designation (§4).

Whether a claim holds is checked against bindings and data, not from this extension alone. `disjoint` is checked structurally by each binding extension where its kind of data allows it; `exhaustive` is a claim about a concept and is checked against data, by comparing with another entity that denotes the same concept (§5, §6).

### What this extension does not do

It does not describe voxels, points, or values: bindings do. It does not describe appearance — color, opacity, draw order — which is a choice about presentation, not a fact about the thing, and belongs to a rendition extension. It does not store measurements. And it asserts nothing *between concepts*: no part-of, no is-a, no laterality hierarchy. Each designation is a statement about one entity and one concept; every relationship among concepts belongs to the terminology that defines them and is reachable through the code. That boundary is what keeps this extension from becoming an ontology.

---

## 3. Extension Fields

The `semantic` extension is declared under the `"duckn"` object's `"extensions"` key, as a peer of every other extension.

```json
{
  "duckn": {
    "version": "1.1",
    "extensions": {
      "semantic": {
        "version": "0.1",
        "entities": [
          { "id": "liver", "name": "Liver" }
        ]
      }
    }
  }
}
```

### 3.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification, as a string.

```json
"version": "0.1"
```

**Version semantics.** While the major version is `0`, the *minor* version may introduce breaking changes; this overrides the duckn convention's default rule that minor increments are additive. From 1.0 onward, major increments signal breaking changes and minor increments are additive. `version` must be a string: a JSON number `0.10` is the float 0.1, and a reader that parsed it that way would mistake a later file for an earlier one.

#### `terminologies`

An object registering the coding systems used in this extension's coded entries. Each key is a short identifier for the system — the value used as `scheme` in designations, mappings, and DICOM classifications — and each value is an object describing that system.

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
  },
  "TotalSegmentator": {
    "name": "TotalSegmentator class labels",
    "version": "2.4",
    "url": "https://github.com/wasserth/TotalSegmentator"
  }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | no | Full human-readable name of the coding system |
| `version` | no | Version of the coding system in use |
| `url` | no | URL for the coding system's browser, specification, or landing page |
| `url_template` | no | Template for concept URLs. The substring `{code}` is replaced with a coded entry's `code` to produce a resolvable link for that concept |

The registry serves two purposes: it gives every coded entry in the file a resolvable source of truth, and it records *which version* of a system the codes were drawn from.

A scheme need not be a public ontology. A segmentation model's class list is a coding system — TotalSegmentator's `liver` is a defined class at a given version of that model — and registering it here is how the *definition* a segmenter used rides along with the data, without any account of the process that ran. The definition is identity; the process is provenance, which is out of scope here and belongs to the `provenance` extension.

Every scheme used in an entity's `designations`, `mappings`, or `dicom` entries **should** be registered here, and a writer that knows the system should register it. Registration is a recommendation, not a requirement: a `scheme` with no registry entry does not make the file invalid, it only leaves the reader with less context. Readers must therefore not reject an unregistered `scheme`, and must not assume the registry enumerates every scheme in the file.

#### `entities`

Required. An array of entity objects (§3.2). The order of the array carries no meaning in this extension. It may be empty for a file that declares a vocabulary and has not yet bound anything to it.

### 3.2 Entity Object Fields

Each element of `entities` is a JSON object. All fields are optional except `id`, and exactly one of the leaf and group forms applies: a group has `members`, a leaf does not.

#### `id`

A stable, unique identifier for the entity within this extension. It does not change when the entity is renamed or the array is reordered. Bindings in other extensions, and `members` in this one, resolve against it.

```json
"id": "kidney_left"
```

An `id` is a token: it must be non-empty and must not contain `.`, `/`, or `#`. Those characters are reserved by the duckn convention's reference syntax, in which the qualified name of an entity is `semantic.entities.<id>` and a reference from a Zarr group into an array separates the array path from the metadata path with `#`. Ids in practice are tokens anyway — `997`, `Segment_1`, `ctx-lh-bankssts`, `kidney_left`. A DICOM UID, which contains dots, belongs in a designation or in `metadata`, not as the handle other things point at.

#### `name`

The human-readable display string, as the author gave it. It is independent of any ontology: it may echo a designation's `meaning`, be a local nickname, or be the only identity a thing has — "suspect lesion #3" has no code and needs none. `name` is what a user reads; `designations` are what a program uses.

```json
"name": "Suspect lesion #3"
```

In the vocabulary of this convention, this field is a *name*, never a *label*: "label" means an integer voxel value throughout duckn (§7).

#### `display`

An optional object providing the display string in additional languages, keyed by BCP 47 language tags. `name` is the default fallback; `display` provides authored alternates.

```json
"name": "Right kidney",
"display": {
  "en": "Right kidney",
  "la": "Ren dexter",
  "de": "Rechte Niere",
  "ja": "右腎"
}
```

When `display` is present, the value of `name` should also appear under the appropriate language key so that `display` is self-contained. These are the author's alternates and are part of what was created; translations chosen by a viewer are a rendition concern and do not belong here.

#### `designations`

An array of coded entries identifying what this entity *is* in external coding systems. Each entry says "this entity is concept *X* in system *Y*," and every entry is an exact identification; a weaker relationship goes in `mappings`. The first entry is the preferred identification. See §4.1.

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
| `modifier` | no | A coded entry qualifying this one, typically laterality. One level of nesting; modifiers do not carry their own modifiers. Its `scheme` need not match the base entry's |

The `scheme` + `code` pair (with `modifier`, when present) is the entity's identity in that system; `meaning` is a rendering. If an embedded `meaning` disagrees with what the coding system says the code means, **the code wins** (§4.1).

#### `mappings`

An object relating this entity to concepts that identify it *inexactly*. Each key is one of four relations, and each value is a non-empty array of coded entries with the same shape as a designation. See §4.2.

```json
"mappings": {
  "closeMatch": [
    { "scheme": "SCT", "code": "108369006", "meaning": "Neoplasm" }
  ],
  "broadMatch": [
    { "scheme": "SCT", "code": "49755003", "meaning": "Morphologically abnormal structure" }
  ]
}
```

| Key | The concept is… |
|-----|-----------------|
| `closeMatch` | the nearest available term for this entity, not the same thing |
| `broadMatch` | broader than this entity |
| `narrowMatch` | narrower than this entity |
| `relatedMatch` | associated with this entity without being similar to it |

There is no `exactMatch` key: exact identification is what `designations` are. Omit `mappings` entirely when there are none, and omit any key whose array would be empty.

#### `dicom`

The DICOM Segmentation IOD classification structure — category, type, type modifier, anatomic region, region modifier. Present only when DICOM interoperability is needed. See §4.3.

```json
"dicom": {
  "category": { "scheme": "SCT", "code": "123037004", "meaning": "Anatomical Structure" },
  "type": { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" },
  "type_modifier": { "scheme": "SCT", "code": "7771000", "meaning": "Left" }
}
```

#### `members`

The entities this **group** is the union of, as an array of entity `id`s. Present on a group, absent on a leaf. Every entry must name an entity in the same `entities` array, and the membership graph must be acyclic (§5). An entity may be a member of any number of groups.

```json
"members": ["liver", "kidney_left", "kidney_right"]
```

`members` is a set. The order of the array carries no meaning in this extension. Another extension may adopt it as a default — a rendition extension may paint members in this order when nothing says otherwise — but nothing about what the group *is* depends on it.

#### `disjoint`

Optional, groups only. `true` claims that no two members share any bound data. Omit when not claimed; never write `false`. Checked structurally by each binding extension where its data allows (§5, §6).

```json
"disjoint": true
```

#### `exhaustive`

Optional. On a group, `true` claims that the members exhaust the thing the group names. On a leaf, `true` claims that the leaf's bound data is all of its concept present in the volume. Omit when not claimed; never write `false`. The claim is about a concept, so it is checked against data rather than metadata, by comparing with an entity that denotes the same concept — identified by a shared designation (§5).

```json
"exhaustive": true
```

#### `metadata`

An open-ended object for application-specific metadata about the entity, keyed by the source application or standard. This extension defines no well-known keys. Application state about a *segmentation* — 3D Slicer's per-segment flags and tags — describes the binding, not the entity, and stays with the segmentation extension.

---

## 4. Identity

An entity's identity in the world has three parts, and they are kept apart because consumers rely on different ones.

1. **Designations** — exact identifications: "this entity *is* concept *X* in system *Y*." The primary mechanism for interoperable identity, and what a consumer may trust without qualification.
2. **Mappings** — inexact correspondences, each labeled with how it falls short of exact.
3. **DICOM classification** — the structured category/type/modifier/region hierarchy that the DICOM Segmentation IOD requires. Needed only for DICOM round-tripping.

### 4.1 Designations

An entity is a real anatomical or pathological thing that different communities identify using different coding systems — a kidney is SNOMED 64033007, FMA 7203, and TA2 5765 simultaneously. Each entry of `designations` captures one such identification, and listing several asserts that, for this entity, they identify the same thing. The first entry is the preferred one.

#### Codes are authoritative; meanings are renderings

The identity carried by a designation is `scheme` + `code` (+ `modifier`, when present). The coding system itself — at the version recorded in `terminologies` — is the source of truth for what that code means.

`meaning` is a snapshot embedded for the reader's convenience: it lets a viewer display something sensible without a terminology service (SNOMED requires a license in many countries; FMA and TA2 lookups require network access), it supplies DICOM's required CodeMeaning on export, and it gives a human a visible cross-check against the code. It is not authoritative: **if the embedded `meaning` disagrees with the coding system, the code wins.** Writers should include `meaning` when they know it; readers must not treat it as identity.

Concept URLs are not embedded per entry. When the registry entry for a scheme provides a `url_template`, readers derive the concept URL by substituting the designation's `code`.

#### Post-coordination via `modifier`

Many coding systems express "left kidney" as a base concept plus a qualifier rather than a single pre-coordinated code, and some — Terminologia Anatomica among them — have no laterality at all. The `modifier` field carries the qualifier as a nested coded entry, and **the modifier's scheme need not match the base entry's**: TA2 "Kidney" qualified by SNOMED "Left" is an exact identification of a left kidney. This mirrors DICOM's modifier code sequences, whose laterality codes are SNOMED regardless of the type code's scheme, so laterality survives round-tripping even when no pre-coordinated concept exists. Modifiers nest one level only.

Getting to an exact identification is a three-step preference, and the first step that succeeds is the one to use:

1. a pre-coordinated code, where the scheme has one;
2. post-coordination via `modifier`;
3. only then a `mappings` entry (§4.2), for a concept no modifier can make exact.

Laterality is never expressed as a mapping. A designation qualified by a modifier is exact; a mapping is what remains when exactness is out of reach.

#### Relationship to `name`

`name` is the user-facing string and may echo a designation's `meaning` or be entirely different ("Bob's left kidney"). The two are independent: `name` is what is shown; `designations` are what is used for computation, interoperability, and lookup.

### 4.2 Mappings

`designations` carries a contract every consumer relies on: an entry *is* the entity. A DICOM exporter takes the first designation as the property type; a stylesheet paints anything designated liver as liver; a lookup by code returns it. Putting a weaker relationship in that list would break the contract for every consumer that did not check for it. So inexact correspondences live in `mappings`, keyed by how they fall short.

The four keys are the mapping relations of SKOS, the W3C's Simple Knowledge Organization System, and the shape — a relation naming a list of targets — is how SKOS itself serializes in JSON-LD. The *names* are borrowed; the *meanings* are defined here, because SKOS relates concept to concept and an entity in a duckn file is an instance — this patient's kidney — related to a concept:

| Key | Definition in this extension | Corresponds to |
|-----|------------------------------|----------------|
| `closeMatch` | the concept is the nearest available term for this entity, but is not the same thing | `skos:closeMatch` |
| `broadMatch` | the concept is broader than this entity: the entity is an instance of something narrower | `skos:broadMatch` |
| `narrowMatch` | the concept is narrower than this entity: the entity includes more than the concept names | `skos:narrowMatch` |
| `relatedMatch` | the concept is associated with this entity without being broader, narrower, or similar | `skos:relatedMatch` |

`narrowMatch` and `relatedMatch` will rarely be written. They are kept so that every SKOS mapping relation has exactly one home, and they cost nothing.

**Ignoring `mappings` can never make a consumer wrong, only less informed.** That property is what the separation buys, and it is why exact identification must never appear here. Within one key's array, order is preference among concepts at the same relation; across keys, order means nothing — the relations already rank themselves.

#### Consumer guidance

This guidance is a recommendation and may be revised without a version change.

A consumer that needs a code for an entity uses `designations`. Finding none, it *may* fall back to a `closeMatch` mapping, then to a `broadMatch`, but only as an explicit choice that it reports: a DICOM exporter that emits a `closeMatch` code as the property type should warn that the exported code overstates what the file claims. A stylesheet or query that wishes to match through mappings says so — for instance by naming the relation it accepts — rather than treating a mapping as a designation. The file stays precise; the consumer chooses how loose to be, and says so.

### 4.3 DICOM Classification

The `dicom` field provides the category/type/modifier/region hierarchy defined by the DICOM Segmentation IOD — the information needed to write a DICOM SEG object.

```json
"dicom": {
  "category": { "scheme": "SCT", "code": "49755003", "meaning": "Morphologically abnormal structure" },
  "type": { "scheme": "SCT", "code": "4147007", "meaning": "Mass" },
  "anatomic_region": { "scheme": "SCT", "code": "23451007", "meaning": "Adrenal gland" },
  "anatomic_region_modifier": { "scheme": "SCT", "code": "24028007", "meaning": "Right" }
}
```

Each entry is a coded entry with the same shape as a designation, minus `modifier`: `scheme` and `code` required, `meaning` optional but recommended.

Every field of `dicom` is itself optional, so that a partial classification can be recorded as it becomes known. Writing a conformant DICOM SEG object additionally requires `category` and `type`, so a writer that finds either missing must fail rather than invent one.

| Field | Required | Description |
|-------|----------|-------------|
| `category` | for DICOM SEG export | Segmentation Category (e.g., "Morphologically abnormal structure", "Tissue") |
| `type` | for DICOM SEG export | Segmentation Type within the category (e.g., "Mass", "Neoplasm") |
| `type_modifier` | no | Qualifier on the type. Omit if not applicable |
| `anatomic_region` | no | Anatomic region |
| `anatomic_region_modifier` | no | Qualifier on the region, typically laterality. Omit if not applicable |

DICOM classification entries use SNOMED CT codes (`"scheme": "SCT"`) by convention, but the scheme is always explicit — legacy files may carry SRT or DCM codes.

When writing a DICOM SEG object, CodeMeaning is required. Writers derive it from the coded entry's `meaning`, falling back to the entity's `name`, and fail if neither is available.

### 4.4 Relationship Among the Three

`designations`, `mappings`, and `dicom` are independent. The same SNOMED concept may appear as a designation and again within the classification. They answer different questions:

- `designations` answers: "What is this thing, in any ontology?"
- `mappings` answers: "What else is it near, and how near?"
- `dicom` answers: "How is it classified in a DICOM Segmentation IOD?"

When converting to DICOM SEG, a writer uses `dicom`. When performing ontology-based lookup, cross-referencing, or styling by code, a reader uses `designations` — and reaches into `mappings` only deliberately (§4.2).

### 4.5 Absence and Omission

Following the duckn convention's "absent means unknown" principle:

- If an entity has no designations, omit `designations`. Do not include an empty array.
- If a coded entry's `meaning` is unknown, omit it. Do not use an empty string or `null`.
- If an entity has no mappings, omit `mappings`; omit any relation key whose array would be empty.
- If an entity has no DICOM classification, omit `dicom`. Within it, omit modifiers that do not apply. Do not use `null`.
- `disjoint` and `exhaustive` are written only as `true`. Absence means the claim is not made; it never means the claim is false.
- If no terminology registrations are needed, omit `terminologies`.

---

## 5. Consistency Rules

**Identity**

1. `id` must be unique within `entities`, non-empty, and must not contain `.`, `/`, or `#`.
2. `scheme` values used in `designations`, `mappings`, or `dicom` entries should have a corresponding key in `terminologies`. This is a recommendation, not a requirement (§3.1).
3. `mappings` uses only the four keys of §4.2, each with a non-empty array. Exact identification never appears in `mappings`.

**Structure**

4. An entity with `members` is a group; an entity without `members` is a leaf. A group's `members` is non-empty.
5. `disjoint` may appear only on a group. `exhaustive` may appear on a group or a leaf.
6. Every entry in `members` must match the `id` of an entity in the same `entities` array.
7. The membership graph must be acyclic. An entity may not directly or transitively contain itself.

**Claims**

8. A group with `"disjoint": true` must have members whose bound data are pairwise disjoint. This extension defines the claim; each binding extension defines the check for its kind of data and states where the check is sound (§6).
9. `exhaustive` is not checked from metadata. Where an entity claiming it and another entity share a designation, a data-aware checker compares their bound data and reports the disagreement.

Rules 1–7 constrain this extension alone and are cheap to check. Rules 8 and 9 require bindings, and rule 9 requires the data itself, which this extension never requires a reader to load.

Writers should validate before serializing. Readers should not assume a file is valid: an extension that violates rule 6 or 7 has no well-defined group extents, and a reader encountering one should report the error rather than resolve memberships partially.

---

## 6. Relationship to Bindings

This extension declares entities; **binding extensions** attach data to them. A binding extension is any extension that maps some kind of datum to an entity `id`:

| Binding extension | Binds | Geometric kind |
|-------------------|-------|----------------|
| `seg` | label values in a labelmap | a region |
| a fiducial extension | points | a location |
| a probability or logit extension | class ranks | a distribution over entities |

Each binding extension must specify, for its kind of data:

- **Reference.** How a binding names an entity — by `id`, resolving against this extension's `entities` in the same array. A binding may not reference a group: a group's extent is the union of its members' extents, derived rather than bound.
- **Extent.** What an entity's bound data *is* for that kind — a set of (layer, value) pairs for a labelmap, a coordinate for a fiducial — so that the union semantics of `members` and the claims of §2 have a concrete meaning.
- **Checks.** How `disjoint` is checked for that kind of data, and where the check is sound. For a labelmap, two entities in the same layer are disjoint if their value sets do not intersect, which is checkable from metadata; across layers the metadata check is vacuous and disjointness is a data claim. A binding extension must say which.
- **Roles.** Any classification of bound data that is not about the entity — a labelmap's background and unknown roles are properties of values, not of concepts — stays in the binding extension.

Every array is self-contained: an array carrying a binding carries its own `semantic` extension. Two arrays in one store that bind the same entities — a labelmap and the probabilities it was derived from — each carry a copy, and their agreement by `id` is the writer's obligation.

The segmentation extension is the first binder. From its version 0.8 it references entities rather than carrying them; the fields this document defines were lifted out of its segment objects, and its own specification, `segmentation-ext-spec.md`, describes the migration of older files.

---

## 7. Terms and Crosswalk

The word *label* is overloaded across the communities this convention serves: an integer voxel value in medical imaging, a human-readable string in the semantic web, and both at once in DICOM, whose `SegmentLabel` is a string. In this convention **"label" means the integer, exclusively**, and every string has another name.

| This convention | Means | SKOS / RDFS | schema.org | DICOM | 3D Slicer |
|-----------------|-------|-------------|------------|-------|-----------|
| label value | an integer voxel value | — | — | SegmentNumber, in a LABELMAP | `LabelValue` |
| `name` | the author's display string for an entity | `skos:prefLabel`, `rdfs:label` | `name` | `SegmentLabel` | `SegmentN_Name` |
| `display` | authored display strings by language | `prefLabel` with language tag, `altLabel` | — | — | — |
| `meaning` | a terminology's rendering of a code | the concept's `prefLabel` | — | `CodeMeaning` | — |
| `code` / `scheme` | identity in a coding system | `skos:notation` / `skos:inScheme` | — | `CodeValue` / `CodingSchemeDesignator` | — |
| `designations` | exact identifications | `skos:exactMatch` | `sameAs` | Segmented Property Type (+ modifier) | terminology entry |
| `mappings` | inexact correspondences | `closeMatch`, `broadMatch`, `narrowMatch`, `relatedMatch` | — | — | — |
| entity | a real thing the data describes part of | a concept's instance | `Thing` | a segment's coded content | a segment's terminology |
| binding | the attachment of data to an entity | — | — | a segment's frames or pixel value | a segment's label value |
| segment | an entity together with its binding | — | — | Segment | Segment |

Two rows deserve a note. `name` sides with schema.org's word for the human string rather than SKOS's, because that is the half that avoids the collision, and the crosswalk tells the SKOS reader what it is looking at. And `designations` corresponds to `skos:exactMatch` rather than *being* it, since SKOS relates concepts and an entity here is an instance; the same caveat applies to every `mappings` key (§4.2).

---

## 8. Examples

### 8.1 A CT abdomen segmentation

The entities behind a labelmap of the liver, a lesion that partially overlaps it, the spleen, and a motion-artifact region. Which values belong to which entity is the segmentation extension's business and is not shown; nothing here mentions a voxel.

```json
{
  "version": "0.1",
  "terminologies": {
    "SCT": {
      "name": "SNOMED Clinical Terms",
      "version": "2025-03",
      "url_template": "http://snomed.info/id/{code}"
    },
    "TotalSegmentator": {
      "name": "TotalSegmentator class labels",
      "version": "2.4",
      "url": "https://github.com/wasserth/TotalSegmentator"
    }
  },
  "entities": [
    { "id": "background", "name": "Background" },
    {
      "id": "liver",
      "name": "Liver",
      "designations": [
        { "scheme": "SCT", "code": "10200004", "meaning": "Liver" },
        { "scheme": "TotalSegmentator", "code": "liver" }
      ],
      "dicom": {
        "category": { "scheme": "SCT", "code": "123037004", "meaning": "Anatomical Structure" },
        "type": { "scheme": "SCT", "code": "10200004", "meaning": "Liver" }
      }
    },
    {
      "id": "spleen",
      "name": "Spleen",
      "designations": [
        { "scheme": "SCT", "code": "78961009", "meaning": "Spleen" },
        { "scheme": "TotalSegmentator", "code": "spleen" }
      ]
    },
    {
      "id": "tumor",
      "name": "Suspect lesion #3",
      "designations": [
        { "scheme": "SCT", "code": "4147007", "meaning": "Mass" }
      ],
      "mappings": {
        "closeMatch": [
          { "scheme": "SCT", "code": "108369006", "meaning": "Neoplasm" }
        ]
      }
    },
    { "id": "artifact", "name": "Motion artifact, not evaluated" },
    {
      "id": "organs",
      "name": "Segmented organs",
      "members": ["liver", "spleen"],
      "disjoint": true
    }
  ]
}
```

The liver and spleen carry the TotalSegmentator class that defined them as a designation in a versioned scheme — the segmenter's *definition*, with no account of the run that produced it. The lesion is exactly a mass; that it may be a neoplasm is a `closeMatch`, because the radiologist did not assert it. The artifact has a name and no code, which is honest. `organs` is a group whose `disjoint` claim the segmentation extension can check from its bindings.

### 8.2 Laterality in a scheme that has none

Terminologia Anatomica does not lateralize. A left kidney is identified exactly three ways here — pre-coordinated in SNOMED, post-coordinated in TA2 with a SNOMED modifier, and classified for DICOM — and inexactly by a concept too broad to be a designation.

```json
{
  "id": "kidney_left",
  "name": "Left kidney",
  "designations": [
    { "scheme": "SCT", "code": "18639004", "meaning": "Left kidney structure" },
    { "scheme": "TA2", "code": "5765", "meaning": "Kidney",
      "modifier": { "scheme": "SCT", "code": "7771000", "meaning": "Left" } }
  ],
  "mappings": {
    "broadMatch": [
      { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" }
    ]
  },
  "dicom": {
    "category": { "scheme": "SCT", "code": "123037004", "meaning": "Anatomical Structure" },
    "type": { "scheme": "SCT", "code": "64033007", "meaning": "Kidney" },
    "type_modifier": { "scheme": "SCT", "code": "7771000", "meaning": "Left" }
  }
}
```

The unlateralized SNOMED kidney appears twice, and the two appearances mean different things. As a `broadMatch` it says "this entity is a kidney, and more specifically than that." Inside `dicom`, qualified by `type_modifier`, it is part of an exact classification. Had the writer wanted an exact SNOMED designation from the unlateralized code, the modifier route would have given one; the mapping shows the fallback only.

### 8.3 A hierarchical atlas

An excerpt of the Allen Mouse Brain Common Coordinate Framework. Leaves carry the structure's own identifier; every interior node is a group over its direct children, and every level is a partition of its parent, so each group claims `disjoint` and `exhaustive`. Most members are elided; in a complete file every `id` named in `members` resolves.

```json
{
  "version": "0.1",
  "terminologies": {
    "CCF": {
      "name": "Allen Mouse Brain Common Coordinate Framework, structure ontology",
      "version": "3",
      "url": "https://atlas.brain-map.org"
    }
  },
  "entities": [
    {
      "id": "997",
      "name": "root",
      "members": ["8", "1009", "73", "1024", "304325711"],
      "disjoint": true,
      "exhaustive": true,
      "designations": [{ "scheme": "CCF", "code": "997", "meaning": "root" }]
    },
    {
      "id": "8",
      "name": "Basic cell groups and regions",
      "members": ["567", "343", "512"],
      "disjoint": true,
      "exhaustive": true,
      "designations": [{ "scheme": "CCF", "code": "8", "meaning": "Basic cell groups and regions" }]
    },
    {
      "id": "315",
      "name": "Isocortex",
      "members": ["184", "500", "453", "1057", "677", "247", "669", "31", "972", "44", "714", "95", "254", "22", "541", "922", "895"],
      "disjoint": true,
      "exhaustive": true,
      "designations": [{ "scheme": "CCF", "code": "315", "meaning": "Isocortex" }]
    },
    {
      "id": "184",
      "name": "Frontal pole, cerebral cortex",
      "members": ["68", "667", "526157192", "526157196", "526322264"],
      "disjoint": true,
      "exhaustive": true,
      "designations": [{ "scheme": "CCF", "code": "184", "meaning": "Frontal pole, cerebral cortex" }]
    },
    {
      "id": "68",
      "name": "Frontal pole, layer 1",
      "designations": [{ "scheme": "CCF", "code": "68", "meaning": "Frontal pole, layer 1" }]
    }
  ]
}
```

A reader that supports groups reconstructs the full hierarchy from `members` alone. A labelmap bound to this vocabulary can be colored or exported at any level of the tree, because every level is a partition; that is a rendition's and an exporter's business, and nothing about it is recorded here.

### 8.4 Minimal

A vocabulary with the smallest useful content.

```json
{
  "version": "0.1",
  "entities": [
    { "id": "S1", "name": "Liver" },
    { "id": "S2", "name": "Spleen" }
  ]
}
```

---

## 9. Design Notes

**Why three kinds of meaning.** Physical meaning belongs to samples and geometric meaning to a datum's kind; both are intrinsic, and the convention and the binding extensions already carry them. Domain meaning is the one kind that is conferred, and it is conferred by exactly one act: binding a datum to an entity. Stating this makes the architecture follow from it. A binding extension supplies a geometric kind; this extension supplies domain meaning; a measurement composes an operation with a binding. A segmentation is, in this light, a broader fiducial — the same target reached through a different geometry.

**Why an entity is what a terminology can name.** The centroid of the liver needs only the semantics of "centroid," an operation with a code of its own, applied to a region that carries the liver's meaning. It becomes an entity in its own right only if the domain has independently named the thing: the hilum of the liver is a Terminologia Anatomica term; the liver's centroid is not. The rule — *an entity is something a terminology can name; everything else is an operation on an entity* — decides the composed-versus-primitive question every time it arises, with the registry as arbiter. It also decides the older question of overlap islands: the intersection of a liver and a tumor is a set operation on two entities and gets no entity, unless the domain names it, "tumor within liver," in which case it does.

**Why a peer extension.** Entities are kind-independent: a labelmap binds values to them, a probability array binds ranks, a fiducial set binds points, and none of those can reach inside another extension to do it. Meaning also changes for a different reason than data does — a binding changes when the segmentation is redrawn, a definition when the concept is revised — and peers version independently where a child would ride its parent's number. The dependency runs one way: bindings reference this extension by `id`, and this extension references nothing.

**Why designations are exact and mappings are separate.** A designation carries a contract: it *is* the entity, and every consumer — DICOM export, styling by code, lookup — relies on it. A `relation` field on the designation would have turned identifiers into edges, and any consumer that did not check the new field would silently over-claim. Separating the weaker relationships into `mappings` gives one property that decides the matter: ignoring `mappings` can never make a consumer wrong, only less informed. SKOS keeps notation and mapping apart in the same way, and DICOM encodes relation by which sequence a code sits in rather than by a field on the code.

**Why SKOS names with local definitions.** The four mapping relations are a W3C vocabulary stable since 2009 and understood wherever knowledge organization is done; inventing four synonyms would help no one. But SKOS relates concept to concept, and an entity here is an instance — this patient's liver — so the names are borrowed and the definitions are written here (§4.2). The crosswalk says *corresponds to*, not *is*.

**Why keyed by relation.** Each key's value is a plain list of coded entries — the same object as a designation — so the extension has one coded-entry shape rather than two, and `designations` is visibly the exact list promoted to its own field. The closed set of four relations belongs in keys, where a schema can enumerate it and "no `exactMatch` here" is structural. It is also how SKOS serializes in JSON-LD, and it is how consumers actually look: a fallback wants `closeMatch` before `broadMatch` by what the relations *mean*, which is a lookup by key, not a filter over a field.

**Why `modifier` is identity, not correspondence.** Post-coordination refines *which concept* is meant — TA2 "Kidney" plus SNOMED "Left" is a left kidney, exactly. That is why it lives on designations and DICOM entries alike, why it may cross schemes, and why laterality is never a mapping. DICOM's own modifier sequences are cross-scheme in exactly this way.

**Why `name` and not `label`.** Every community this convention serves uses "label" for something different — an integer, a string, or both — and the imaging sense cannot be moved, because "label map" and "label value" are the field's terms. So the string is a `name`, which is schema.org's word for the same thing, and the crosswalk in §7 gives the SKOS reader `prefLabel` beside it. `name` and `meaning` then stay distinct, which matters: one is what this author called this entity, the other is what the terminology calls the concept.

**Why `members` is a set.** Membership is a definitional fact and has no order. The one thing order could have meant — which member is painted over which — is a choice about presentation, and it belongs to a rendition. This extension permits another to adopt the array order as a default so that a file with no rendition is still viewable, while asserting nothing by it.

**Why `exhaustive` may appear on a leaf.** Coverage and identity are different questions. A liver segment from a cropped scan is exactly liver and not all of it; weakening its designation to say so would have been wrong about what the voxels are in order to be right about how many there were. A leaf that claims `exhaustive` says its bound data is all of its concept in the volume; one that does not leaves the question open, which is what a cropped scan should do.

**Why no color.** Color is a choice about how to show a thing, and the same entity is shown differently in a clinical view, a publication, and a colorblind-safe palette. It is also the one property that changes without the thing changing. A rendition extension carries it, keyed to entities by `id` or by code, and the same document can be embedded as the author's recommendation or applied from outside as a stylesheet. DICOM's `RecommendedDisplayCIELabValue` and Slicer's segment color both land there, not here.

**Why `entities` is an array, not a map.** Entities have a natural authoring order, and an array preserves it for a human reader. `id` provides stable lookup when order is irrelevant, and this extension assigns the order no meaning, so nothing is lost if a writer sorts it.

**Why the registry can hold a model's class list.** A segmentation model's classes are a coding system: each is defined, at a version, by the model's authors. Registering it lets the definition a segmenter used travel with the data as a designation — identity — without any account of the run, the weights, or the inputs, which is provenance and a separate problem. The two are easy to conflate and must not be: the definition is in scope here, the process is not.

**What is deliberately absent.** Relationships between concepts — part-of, is-a, laterality hierarchies — belong to the terminologies and are reachable through the code; asserting them here would make this extension an ontology, and a poor one. Provenance is the `provenance` extension's. Appearance is a rendition's. Measurements are a quantitative extension's. Which values, points, or ranks belong to an entity is every binding extension's own.
