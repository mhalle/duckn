# Units Specification for duckn

**Status:** Draft
**Applies to:** duckn convention version 1.1+

---

## 1. Purpose

The duckn convention has two unit fields: per-axis `unit` and top-level `sample_units`. In version 1.0, both accept only bare strings (`"mm"`, `"s"`, `"HU"`). This works for the common cases in medical imaging, where the vocabulary is small and universally understood, but it has limitations:

- A bare string is ambiguous. Is `"ms"` milliseconds or something else? Is `"um"` micrometers or a typo?
- There is no machine-readable way to validate dimensional consistency (e.g., verifying that all spatial axes use compatible length units).
- There is no way to link a unit symbol to the formal definition in a unit system, which matters for automated conversion and interoperability with systems that speak UCUM, QUDT, or UDUNITS.

This specification extends both `unit` and `sample_units` to optionally accept a structured object alongside the existing bare string. The bare string remains the default for the common case. The structured form is available when machine-parseable unit identity matters.

---

## 2. Unit Value: String or Object

Wherever the convention accepts a unit — per-axis `unit` and top-level `sample_units` — the value may be either:

**A bare string** (the common case):

```json
"unit": "mm"
```

This is equivalent to version 1.0 behavior. The string is a human-readable symbol with no formal system binding. It is the recommended form when the unit is obvious and no machine interpretation is needed.

**A unit object:**

```json
"unit": {
  "symbol": "mm",
  "scheme": "UCUM",
  "code": "mm"
}
```

This identifies the unit within a formal unit system.

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `symbol` | yes | string | Display symbol — what a human should see. Must be non-empty. |
| `scheme` | yes | string | Unit system identifier. See §3. |
| `code` | yes | string | The canonical code for this unit within the named system. |
| `url` | no | string | Resolvable URL for this specific unit's definition — a persistent identifier, ontology IRI, or documentation page. |

The `symbol`, `scheme`, and `code` fields are required when the object form is used. The point of the object form is to bind a symbol to a formal definition; a partial binding (symbol without scheme, or scheme without code) is not useful.

The `url` field points to the specific *unit*, not the unit *system* (which is what the `url` in the `unit_systems` registry points to). For example:

```json
{
  "symbol": "mm",
  "scheme": "UCUM",
  "code": "mm",
  "url": "https://ucum.org/ucum#mm"
}
```

```json
{
  "symbol": "µm",
  "scheme": "QUDT",
  "code": "MicroM",
  "url": "http://qudt.org/vocab/unit/MicroM"
}
```

QUDT units have stable, dereferenceable URIs by design, making `url` particularly natural for that system. UCUM does not assign per-unit URIs, but a link to the relevant section of the specification or a third-party resolver is still useful. Omit `url` when no meaningful target exists.

A bare string `"X"` is semantically equivalent to `{"symbol": "X"}` with no `scheme` or `code` — it is a display-only symbol with no formal binding.

### Interaction with "absent means unknown"

The convention's existing rule applies: if the unit is unknown or meaningless for an axis, omit the `unit` field entirely. Do not use `null`, an empty string, or an empty object.

---

## 3. Unit Systems

The `scheme` field identifies which unit system the `code` belongs to. The following systems are recognized by this specification.

### 3.1 UCUM (Recommended Default)

| Field | Value |
|-------|-------|
| Scheme identifier | `"UCUM"` |
| Full name | Unified Code for Units of Measure |
| Maintained by | Regenstrief Institute |
| Specification | https://ucum.org/ucum |
| Adopted by | DICOM, HL7 FHIR, HL7 v3, ISO 11240 |

UCUM is the recommended unit system for this convention. It is the dominant standard for machine-parseable units in clinical and biomedical informatics. Its grammar is formally defined, and every code has an unambiguous computational meaning.

UCUM codes are case-sensitive. Examples:

| Quantity | UCUM code | Notes |
|----------|-----------|-------|
| Millimeter | `mm` | |
| Micrometer | `um` | UCUM uses `u` for micro, not `µ` |
| Second | `s` | |
| Millisecond | `ms` | |
| Square millimeters per second | `mm2/s` | Diffusion coefficient |
| Hounsfield unit | `[hnsf'U]` | UCUM annotation syntax |
| Reciprocal millimeter | `mm-1` or `/mm` | Spatial frequency |
| Degrees | `deg` | |
| Dimensionless (ratio) | `1` | |

When the unit system is not specified and a reader needs to guess, UCUM is the safest assumption for data originating from medical imaging workflows.

### 3.2 UDUNITS

| Field | Value |
|-------|-------|
| Scheme identifier | `"UDUNITS"` |
| Full name | UDUNITS-2 |
| Maintained by | Unidata / UCAR |
| Specification | https://www.unidata.ucar.edu/software/udunits/ |
| Adopted by | CF Conventions (NetCDF), Earth system sciences |

UDUNITS is the de facto standard in climate science, atmospheric science, and oceanography. It is the unit system used by the CF (Climate and Forecast) metadata conventions for NetCDF. If duckn is used for geospatial, environmental, or Earth observation data, UDUNITS codes will be natural for those communities.

UDUNITS has a different grammar from UCUM. Examples:

| Quantity | UDUNITS code | Notes |
|----------|-------------|-------|
| Meter | `m` | |
| Kilometer | `km` | |
| Kelvin | `K` | |
| Degrees Celsius | `degC` | Not `°C` |
| Seconds since epoch | `seconds since 1970-01-01` | Time-as-offset encoding |
| Parts per million | `ppm` | |

### 3.3 QUDT

| Field | Value |
|-------|-------|
| Scheme identifier | `"QUDT"` |
| Full name | Quantities, Units, Dimensions, and Types |
| Maintained by | QUDT.org (NASA, TopQuadrant) |
| Specification | https://qudt.org |
| Adopted by | Linked data, engineering metadata, some Earth science |

QUDT is an RDF/OWL ontology where every unit has a URI. It is more heavyweight than UCUM or UDUNITS but is the natural choice in linked-data and semantic-web contexts.

QUDT codes are full URIs or local names from the QUDT namespace. When using local names:

| Quantity | QUDT local name | Full URI |
|----------|----------------|----------|
| Millimeter | `MilliM` | `http://qudt.org/vocab/unit/MilliM` |
| Second | `SEC` | `http://qudt.org/vocab/unit/SEC` |
| Kelvin | `K` | `http://qudt.org/vocab/unit/K` |

The `code` field should use the local name (e.g., `"MilliM"`). A reader can construct the full URI by prepending `http://qudt.org/vocab/unit/`.

### 3.4 Custom and Domain-Specific Systems

The `scheme` field is not restricted to the three systems above. Any string may be used as a scheme identifier. This allows domain-specific unit systems without requiring changes to the convention.

When using a custom scheme, the `code` must be meaningful within that system, and the `symbol` provides the human-readable fallback. A reader that does not recognize the scheme can still display the symbol.

---

## 4. The `unit_systems` Registry

When structured unit objects are used, the top-level `"duckn"` object may include a `unit_systems` field that registers the unit systems referenced in the file. This follows the same pattern as the slicerseg extension's `terminologies` registry.

```json
"unit_systems": {
  "UCUM": {
    "name": "Unified Code for Units of Measure",
    "version": "2.1",
    "url": "https://ucum.org/ucum"
  }
}
```

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | no | string | Full human-readable name of the unit system |
| `version` | no | string | Version of the unit system in use |
| `url` | no | string | URL for the unit system's specification or landing page |

Rules:

- Each key is a scheme identifier (the value used in unit objects' `scheme` field).
- If a scheme is used in any `unit` or `sample_units` object in the file, it should be registered here. The file is valid without the registration, but the registry provides provenance and discoverability.
- When only bare string units are used, `unit_systems` should be omitted entirely.
- When all unit objects use `"UCUM"` as their scheme, the registry may be omitted — UCUM is the default and its identity is well-known. Including it is still good practice when a specific UCUM version matters.

---

## 5. Applying to `sample_units`

The same string-or-object rule applies to the top-level `sample_units` field.

**Bare string** (common case):

```json
"sample_units": "HU"
```

**Structured object:**

```json
"sample_units": {
  "symbol": "HU",
  "scheme": "UCUM",
  "code": "[hnsf'U]"
}
```

This is particularly useful for `sample_units` because sample value units are more varied and less predictable than axis coordinate units. A spatial axis is almost always in millimeters; sample values could be Hounsfield units, diffusion coefficients, statistical scores, parts per million, or domain-specific quantities.

---

## 6. Consistency Rules

- When a unit object is used, all three fields (`symbol`, `scheme`, `code`) must be present.
- A bare string and an object must not be mixed for the same semantic purpose within a single file in a way that creates ambiguity. In practice, this is not a concern — each `unit` and `sample_units` field is independent.
- `scheme` values used in unit objects should have a corresponding key in the `unit_systems` registry when present. This is recommended, not required.
- For spatial axes that share a world space, the units should be dimensionally compatible (all lengths, or all times, etc.). This convention does not enforce dimensional analysis, but a reader that understands the unit system can.
- Within a single file, it is valid to mix bare strings and structured objects on different axes. For example, spatial axes might use `"unit": "mm"` while a time axis uses a structured UCUM object.

---

## 7. Examples

### 7.1 Bare Strings (Version 1.0 Compatible)

No change from existing convention behavior:

```json
"sample_units": "HU",
"axes": [
  {
    "kind": "space",
    "space_direction": [1, 0, 0],
    "unit": "mm"
  },
  {
    "kind": "space",
    "space_direction": [0, 1, 0],
    "unit": "mm"
  },
  {
    "kind": "space",
    "space_direction": [0, 0, 2],
    "unit": "mm"
  }
]
```

### 7.2 UCUM Throughout

A CT volume with fully specified UCUM units:

```json
"sample_units": {
  "symbol": "HU",
  "scheme": "UCUM",
  "code": "[hnsf'U]",
  "url": "https://ucum.org/ucum#para-43"
},
"unit_systems": {
  "UCUM": {
    "name": "Unified Code for Units of Measure",
    "version": "2.1",
    "url": "https://ucum.org/ucum"
  }
},
"axes": [
  {
    "kind": "space",
    "space_direction": [0.976, 0, 0],
    "unit": { "symbol": "mm", "scheme": "UCUM", "code": "mm" }
  },
  {
    "kind": "space",
    "space_direction": [0, 0.976, 0],
    "unit": { "symbol": "mm", "scheme": "UCUM", "code": "mm" }
  },
  {
    "kind": "space",
    "space_direction": [0, 0, 1],
    "unit": { "symbol": "mm", "scheme": "UCUM", "code": "mm" }
  }
]
```

### 7.3 Mixed: Spatial Bare Strings, Structured Time Axis

An fMRI time series where the spatial units are obvious but the temporal unit benefits from formal specification:

```json
"axes": [
  {
    "kind": "space",
    "space_direction": [3, 0, 0],
    "unit": "mm"
  },
  {
    "kind": "space",
    "space_direction": [0, 3, 0],
    "unit": "mm"
  },
  {
    "kind": "space",
    "space_direction": [0, 0, 3],
    "unit": "mm"
  },
  {
    "kind": "time",
    "unit": { "symbol": "s", "scheme": "UCUM", "code": "s" }
  }
]
```

### 7.4 Diffusion Tensor with Compound Sample Units

```json
"sample_units": {
  "symbol": "mm²/s",
  "scheme": "UCUM",
  "code": "mm2/s"
},
"intent": "diffusion-tensor",
"axes": [
  {
    "kind": "space",
    "space_direction": [2, 0, 0],
    "unit": { "symbol": "mm", "scheme": "UCUM", "code": "mm" }
  },
  {
    "kind": "space",
    "space_direction": [0, 2, 0],
    "unit": { "symbol": "mm", "scheme": "UCUM", "code": "mm" }
  },
  {
    "kind": "space",
    "space_direction": [0, 0, 2.5],
    "unit": { "symbol": "mm", "scheme": "UCUM", "code": "mm" }
  },
  {
    "kind": "3D-symmetric-matrix"
  }
]
```

### 7.5 UDUNITS for Earth Science Data

A geospatial temperature field using CF-convention-style units:

```json
"sample_units": {
  "symbol": "K",
  "scheme": "UDUNITS",
  "code": "K"
},
"unit_systems": {
  "UDUNITS": {
    "name": "UDUNITS-2",
    "version": "2.2.28",
    "url": "https://www.unidata.ucar.edu/software/udunits/"
  }
},
"axes": [
  {
    "kind": "space",
    "space_direction": [0.25, 0],
    "unit": { "symbol": "°N", "scheme": "UDUNITS", "code": "degrees_north" }
  },
  {
    "kind": "space",
    "space_direction": [0, 0.25],
    "unit": { "symbol": "°E", "scheme": "UDUNITS", "code": "degrees_east" }
  },
  {
    "kind": "time",
    "unit": { "symbol": "hours since 2020-01-01", "scheme": "UDUNITS", "code": "hours since 2020-01-01" }
  }
]
```

### 7.6 QUDT in a Linked-Data Context

```json
"unit_systems": {
  "QUDT": {
    "name": "Quantities, Units, Dimensions, and Types",
    "url": "https://qudt.org"
  }
},
"axes": [
  {
    "kind": "space",
    "space_direction": [0.001, 0, 0],
    "unit": { "symbol": "µm", "scheme": "QUDT", "code": "MicroM", "url": "http://qudt.org/vocab/unit/MicroM" }
  },
  {
    "kind": "space",
    "space_direction": [0, 0.001, 0],
    "unit": { "symbol": "µm", "scheme": "QUDT", "code": "MicroM", "url": "http://qudt.org/vocab/unit/MicroM" }
  },
  {
    "kind": "space",
    "space_direction": [0, 0, 0.005],
    "unit": { "symbol": "µm", "scheme": "QUDT", "code": "MicroM", "url": "http://qudt.org/vocab/unit/MicroM" }
  }
]
```

---

## 8. Design Notes

**Why the object form requires all three fields.** A unit object with only `symbol` is no better than a bare string. A unit object with `scheme` but no `code` says "this is a UCUM unit" without saying which one. The structured form exists to provide a complete, machine-actionable binding. If you don't need that, use a bare string.

**Why `symbol` is separate from `code`.** In many unit systems, the canonical code differs from the conventional display symbol. UCUM uses `u` for micro (not `µ`), brackets for annotation units (`[hnsf'U]`), and caret for exponents (`m2` not `m²`). QUDT uses PascalCase local names (`MilliM`). The `symbol` field is what you show to humans; the `code` field is what you give to a parser. Collapsing these into one field forces a choice between human readability and machine parseability.

**Why UCUM is recommended rather than required.** UCUM is the right default for medical imaging and clinical data, where it is already mandated by DICOM and FHIR. But the duckn convention is not exclusively a medical imaging format. Climate scientists use UDUNITS. Engineering and linked-data workflows use QUDT. Mandating UCUM would force these communities to translate their native unit vocabulary, adding friction for no gain. The recommendation is: use UCUM unless your domain has a better-established alternative.

**Why the `unit_systems` registry is optional.** For the overwhelmingly common case — UCUM units on a medical image — the registry adds verbosity without information. Everyone knows what UCUM is. The registry becomes useful when the file uses a less well-known system, a specific version matters for reproducibility, or multiple systems are used in the same file. Making it optional keeps the common case concise while supporting the uncommon case.

**Why bare strings remain first-class.** The vast majority of duckn files will be medical images with millimeter spatial axes. Writing `"unit": "mm"` is clear, concise, and understood by every reader. Requiring `{"symbol": "mm", "scheme": "UCUM", "code": "mm"}` for this case would be a tax on simplicity with no practical benefit. Structured units are for when the extra precision matters — compound units, unusual quantities, cross-domain interoperability, or automated unit validation.

**Relationship to the slicerseg extension's `terminologies`.** The `unit_systems` registry follows the same pattern: a top-level object registering the coding systems used elsewhere in the file, with keys that match the `scheme` values in data objects. This is deliberate. The convention already has a precedent for "declare your vocabularies in a registry, reference them by short key." Units follow the same design.

**Version compatibility.** This specification is additive — it introduces a new valid form for existing fields and a new optional top-level field. A version 1.0 reader encountering a unit object where it expects a string should either extract the `symbol` field as a fallback or treat the unit as unknown. A version 1.1+ reader handles both forms. This qualifies as a minor version increment.