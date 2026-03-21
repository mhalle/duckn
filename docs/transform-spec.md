# Space Transforms — duckn Convention Amendment

**Convention version:** 1.1
**Status:** Draft

---

## 1. Purpose

This amendment adds a `space_transforms` field to the duckn convention. It declares named coordinate spaces and the affine transforms connecting them to the array's own spatial embedding.

Every duckn array with spatial metadata already has an implicit world coordinate system — defined by `space` (or `space_dimension`), `space_origin`, and per-axis `space_direction` vectors. This amendment adds the ability to say: "this array's world space relates to *this other named space* by *this transform*."

### What this amendment provides

- Named target coordinate spaces, identified by extension-qualified names (e.g., `nifti:mni152`, `fits:icrs`) or ad hoc names for project-specific spaces.
- Affine transforms from a built-in space to each named target, with explicit direction (forward, inverse, or both).
- Optional axis descriptions for target spaces, reusing the existing axis object schema.
- An optional metadata object per transform for provenance, quality metrics, or free-form annotation.
- A uniform syntax that works at both the array level and (in future) the group level.

### What this amendment does not do

This does not define displacement fields, coordinate maps, or multi-hop transform chains. These require array-referenced transforms and group-level graph semantics, which are deferred to a future specification.

This does not redefine or override the convention's intrinsic spatial metadata. The `space_origin`, `space_direction`, `measurement_frame`, and `value_transforms` fields remain authoritative for their respective roles.

---

## 2. Built-in Spaces

The convention defines implicit coordinate spaces for each array with spatial metadata. The following are **spatial** coordinate spaces — they describe positions and are relevant to `space_transforms`:

| Name | Defined by | Description |
|------|-----------|-------------|
| `index` | Zarr `shape` + convention `axes` | Discrete array coordinates |
| `world` | `space` / `space_dimension`, `space_origin`, per-axis `space_direction` | Continuous, possibly oblique physical coordinates |
| `axis-aligned` | Derived from `world` | Axis-aligned physical coordinates: same origin and voxel scale as `world`, axes aligned to the cardinal directions of the declared `space` convention |

Two implicit transforms connect them:

| From | To | Defined by |
|------|----|-----------|
| `index` | `world` | `space_origin` + `space_direction` (adjusted for `centering`) |
| `world` | `axis-aligned` | Derived: rotation component of the array affine |

For arrays whose `space_direction` vectors are already axis-aligned, `world` and `axis-aligned` are identical. The rotation component is extracted via polar decomposition of the linear part of the index-to-world affine. For acquisitions with shear, the polar decomposition yields the closest rotation matrix (in the Frobenius norm sense).

The convention also defines `measurement_frame`, which transforms vector/tensor *component values* from their storage frame to world space. This is not a spatial coordinate space — it operates on the meaning of stored values, not on positions. It is not a valid `from` or `to` target in `space_transforms` and is not part of the spatial transform graph.

Built-in space names (`world`, `axis-aligned`, `index`, `measurement`) are reserved. The first three appear in the `space` field of `from` and `to` objects (§4). `measurement` is reserved because it names a distinct frame (the `measurement_frame` field) that is not a spatial coordinate space. None of these may be used as transform target names.

---

## 3. The `space_transforms` Field

`space_transforms` is a JSON array of transform entries. It appears at the top level of the `"duckn"` object, alongside existing fields like `space_origin`, `measurement_frame`, and `axes`:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [182, 218, 182],
  "data_type": "float32",
  "attributes": {
    "duckn": {
      "version": "1.1",
      "space": "right-anterior-superior",
      "space_origin": [-90.0, -126.0, -72.0],
      "axes": [ ... ],
      "space_transforms": [
        {
          "to": { "name": "nifti:mni152" },
          "forward": { "identity": true }
        }
      ],
      "extensions": {
        "nifti": { "version": "1.0" }
      }
    }
  }
}
```

The field is optional. When absent, the array has no declared transforms beyond its implicit built-in transforms.

---

## 4. Space References

The `from` and `to` fields in each transform entry identify coordinate spaces. A space reference is an object with one of two forms:

### 4.1 Built-in Space Reference

References one of the array's own built-in spaces (§2):

```json
{ "space": "world" }
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `space` | string | **yes** | One of: `"world"`, `"axis-aligned"`, `"index"` |

### 4.2 Named Space Reference

References a named coordinate space by name, optionally with axis descriptions:

```json
{ "name": "nifti:mni152" }
```

```json
{
  "name": "surgical_plan",
  "axes": [
    { "kind": "space", "unit": "mm" },
    { "kind": "space", "unit": "mm" },
    { "kind": "space", "unit": "mm" }
  ]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **yes** | The coordinate space name (see §4.4) |
| `axes` | array of axis objects | no | Axis descriptions for the target space, using the same axis object schema defined by the convention (§3.2 of the duckn convention). Length must equal the space dimension. |

When `axes` is present, it describes the axes of the named space — their kind, unit, centering, and any per-axis extension metadata. All axis fields are optional, as in the convention.

### 4.3 Path-Qualified Space Reference (Future)

For group-level transforms connecting arrays within the same container, a future version will support path-qualified references:

```json
{ "path": "t1", "space": "world" }
```

This form is reserved but not defined in this version. At the array level, `from` and `to` use only the built-in (§4.1) and named (§4.2) forms.

### 4.4 Space Name Rules

Named space identifiers (the `name` field in §4.2) follow these rules:

**Extension-qualified names** contain a colon. The prefix before the colon must be the name of a declared extension (present in the top-level `extensions` object with at least a `version`). The suffix is a space name defined by that extension's specification.

```
nifti:mni152        — MNI space, defined by the NIfTI extension
nifti:talairach     — Talairach space, defined by the NIfTI extension
fits:icrs           — celestial frame, defined by the FITS extension
```

**Ad hoc names** contain no colon. They are defined locally by this array or container and have no external specification.

```
surgical_plan       — project-specific coordinate space
experiment_rig      — lab-specific coordinate space
```

**Reserved:** Names must not match any built-in space name (`world`, `axis-aligned`, `index`, `measurement`).

---

## 5. Transform Entry

Each element of `space_transforms` is an object describing a single transform between two coordinate spaces.

```json
{
  "from": { "space": "world" },
  "to": { "name": "nifti:mni152" },
  "forward": { "identity": true },
  "metadata": {
    "description": "Output of MNI normalization pipeline"
  }
}
```

### 5.1 Fields

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `to` | space reference | **yes** | — | The target space (§4) |
| `from` | space reference | no | `{"space": "world"}` | The source space (§4) |
| `forward` | transform object | conditional | — | Transform from source to target (§6) |
| `inverse` | transform object | conditional | — | Transform from target to source (§6) |
| `metadata` | object | no | — | Freeform provenance and annotation (§5.2) |

At least one of `forward` or `inverse` must be present. Both may be present.

When `from` is omitted, it defaults to `{"space": "world"}` — the array's world space.

### 5.2 `metadata`

An open object for provenance and annotation. The convention does not constrain its contents.

Suggested fields (none required):

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | Human-readable description of the transform |
| `software` | string | Name and version of the software that computed the transform |
| `date` | string | ISO 8601 date/datetime when the transform was computed |
| `method` | string | Registration method or algorithm name |
| `error` | number or object | Quality metric (e.g., RMS error in mm) |

Implementations must preserve unrecognized fields in `metadata` on round-trip.

---

## 6. Transform Objects

A transform object describes the mathematical mapping. It contains exactly one key that identifies its type.

### 6.1 Identity

Asserts that the source and destination are the same coordinate space. No matrix is needed.

```json
{ "identity": true }
```

An identity transform is a registration claim: it says the array's coordinate space *is* the named target space. For example, an array produced by a normalization pipeline that outputs directly in MNI coordinates would declare an identity forward transform to `nifti:mni152`.

### 6.2 Affine

An affine transform specified by its matrix.

```json
{
  "affine": [[ 0.998, -0.052,  0.031, -2.1],
              [ 0.054,  0.997, -0.058,  1.3],
              [-0.028,  0.060,  0.998,  0.7]]
}
```

#### `affine`

A JSON array in row-major (C) order. Each inner array is one row of the matrix. For an N-dimensional space, the matrix has N rows and N+1 columns. Each row has N linear coefficients followed by a translation component.

The matrix maps source coordinates to destination coordinates:

```
destination = affine × [source; 1]
```

where `[source; 1]` is the source coordinate vector with a 1 appended (homogeneous coordinates).

For a 3D space, `affine` is 3×4. For a 2D space, `affine` is 2×3. The number of rows must equal the space dimension. The number of columns must equal the space dimension plus one. (Source and destination spaces always have the same dimensionality — see §8.)

The N×N linear submatrix (all columns except the last) must be non-singular. This ensures the affine mapping is invertible.

### 6.3 Future Transform Types

Future versions may define additional transform object types. A transform object that contains an unrecognized key must be treated as opaque — the reader should preserve it on round-trip but cannot apply it.

Anticipated future types include:

- **Displacement fields** — a reference to a Zarr array within the same container that stores per-voxel displacement vectors.
- **Coordinate maps** — absolute coordinates rather than displacements.
- **Sequences** — ordered composition of multiple transforms.

---

## 7. Matrix Ordering Convention

All matrices in the duckn convention use **row-major (C) order**: each inner JSON array is one row of the matrix. This applies to:

- `affine` matrices in `space_transforms`
- `measurement_frame` at the convention level

```json
[[a00, a01, a02, t0],
 [a10, a11, a12, t1],
 [a20, a21, a22, t2]]
```

corresponds to:

```
     ┌                   ┐
     │ a00  a01  a02  t0 │
M =  │ a10  a11  a12  t1 │
     │ a20  a21  a22  t2 │
     └                   ┘
```

In numpy: `M = np.array(affine)` followed by `M @ np.append(point, 1)`.

This is consistent with numpy, Zarr's default C ordering, and OME-NGFF's matrix convention.

**Change from version 1.0:** The NRRD file format defines its `measurement frame` as an array of column vectors. Convention version 1.0 followed this convention. Version 1.1 changes `measurement_frame` to row-major order for consistency with all other matrices. Each inner array is now a row of the matrix. Implementations converting from NRRD files must transpose the measurement frame matrix when writing convention version 1.1 metadata.

---

## 8. Scope

Space transforms operate on the **spatial dimensions** defined by `space` or `space_dimension`. The matrix dimensions are determined by the space dimension (not the total array dimension). Non-spatial axes (time, range/component axes) are outside the transform's scope.

For a 4D fMRI volume with `space: "right-anterior-superior"` (3D) and a time axis, the space dimension is 3. All `affine` matrices in `space_transforms` are 3×4. The time axis is unaffected.

Future amendments may extend the transform scope to include temporal dimensions.

**Non-uniform spacing.** When per-axis `samples` override the uniform spacing model (e.g., non-uniform slice positions or gantry tilt), the built-in `world` and `index` spaces are defined by the **nominal** affine from `space_origin` and `space_direction`. Per-sample corrections are outside the scope of `space_transforms`. A transform declared from `world` to a named space applies to the nominal world coordinates, not the per-sample-corrected positions.

**Index space.** The `index` space uses raw integer array indices (0, 1, 2, ...). The centering adjustment (half-voxel offset for cell-centered data) is part of the index-to-world transform, not part of the index space itself.

---

## 9. Consistency Rules

- Each transform entry must contain at least one of `forward` or `inverse`.
- When `from` is omitted, it defaults to `{"space": "world"}`.
- At the array level, `from` must be a built-in space reference (`{"space": "..."}`). Path-qualified references are reserved for future group-level usage.
- Extension-qualified names in `to` must reference a declared extension (present in the top-level `extensions` object with at least a `version`).
- Space names must not match built-in space names (`world`, `axis-aligned`, `index`).
- The `affine` matrix must have N rows and N+1 columns, where N equals the space dimension.
- The `affine` matrix must be non-singular. A singular matrix is an error.
- All matrices are stored in row-major (C) order (§7).
- If `axes` is present on a `to` reference, its length must equal the space dimension.
- If both `forward` and `inverse` are present, they should be approximate inverses of each other. Implementations may warn on inconsistency.
- A given `(from, to)` pair must appear at most once across all entries in `space_transforms` on a single array. Different `from` spaces to the same `to` are permitted (e.g., both `world → nifti:mni152` and `axis-aligned → nifti:mni152`).
- `metadata` is optional and open. Implementations must preserve unrecognized fields on round-trip.

---

## 10. Examples

### 10.1 MRI in MNI Space

A T1 structural scan produced directly in MNI space:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [182, 218, 182],
  "data_type": "float32",
  "dimension_names": ["i", "j", "k"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 64] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.1",
      "space": "right-anterior-superior",
      "space_origin": [-90.0, -126.0, -72.0],
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
          "space_direction": [0, 0, 1],
          "unit": "mm"
        }
      ],
      "space_transforms": [
        {
          "to": { "name": "nifti:mni152" },
          "forward": { "identity": true },
          "metadata": {
            "description": "Output of MNI normalization pipeline",
            "software": "ANTs v2.4.0"
          }
        }
      ],
      "extensions": {
        "nifti": {
          "version": "1.0",
          "tags": {
            "sform_code": 4
          }
        }
      }
    }
  }
}
```

The identity transform asserts that this array's world space is MNI152. The NIfTI extension's `sform_code: 4` corroborates this. A library that encounters this entry can treat the array's world coordinates as MNI coordinates without further computation.

### 10.2 Native-Space Scan with Registration to MNI

A subject's T1 in native scanner space, with a linear registration to MNI:

```json
"space_transforms": [
  {
    "to": {
      "name": "nifti:mni152",
      "axes": [
        { "kind": "space", "unit": "mm" },
        { "kind": "space", "unit": "mm" },
        { "kind": "space", "unit": "mm" }
      ]
    },
    "forward": {
      "affine": [[ 0.9834, -0.0512,  0.0307, -2.10],
                  [ 0.0536,  0.9971, -0.0582,  1.30],
                  [-0.0278,  0.0601,  0.9978,  0.70]]
    },
    "metadata": {
      "description": "12-DOF affine registration to MNI152 template",
      "software": "FSL FLIRT 6.0.7",
      "method": "mutual information, 12 DOF",
      "date": "2025-03-15",
      "error": { "rms_mm": 0.83 }
    }
  }
]
```

### 10.3 Both Directions

A registration where both forward and inverse are stored:

```json
"space_transforms": [
  {
    "to": { "name": "nifti:mni152" },
    "forward": {
      "affine": [[ 0.9834, -0.0512,  0.0307, -2.10],
                  [ 0.0536,  0.9971, -0.0582,  1.30],
                  [-0.0278,  0.0601,  0.9978,  0.70]]
    },
    "inverse": {
      "affine": [[ 0.9837,  0.0530, -0.0286,  1.94],
                  [-0.0507,  0.9972,  0.0594, -1.42],
                  [ 0.0315, -0.0571,  0.9979, -0.57]]
    },
    "metadata": {
      "description": "12-DOF affine, forward and inverse stored independently"
    }
  }
]
```

For affine transforms, storing both directions is redundant since the library can invert the matrix. This pattern anticipates future displacement field support where both directions are independently computed and not trivially invertible.

### 10.4 Ad Hoc Surgical Planning Space

A preoperative MRI with a transform to a locally defined surgical coordinate system:

```json
"space_transforms": [
  {
    "to": {
      "name": "surgical_plan",
      "axes": [
        { "kind": "space", "unit": "mm" },
        { "kind": "space", "unit": "mm" },
        { "kind": "space", "unit": "mm" }
      ]
    },
    "forward": {
      "affine": [[1, 0, 0, 20.5],
                  [0, 1, 0, -15.3],
                  [0, 0, 1, 42.0]]
    },
    "metadata": {
      "description": "Translation to planned DBS entry point, same orientation as native RAS"
    }
  }
]
```

The ad hoc name `surgical_plan` has no extension prefix — it is defined only in this context. The `axes` field on `to` describes the target space (3D, millimeters). The affine is a pure translation.

### 10.5 Transform from Axis-Aligned Space

A registration defined relative to the axis-aligned space rather than the oblique world space:

```json
"space_transforms": [
  {
    "from": { "space": "axis-aligned" },
    "to": { "name": "nifti:mni152" },
    "forward": {
      "affine": [[ 0.9834, -0.0512,  0.0307, -2.10],
                  [ 0.0536,  0.9971, -0.0582,  1.30],
                  [-0.0278,  0.0601,  0.9978,  0.70]]
    }
  }
]
```

### 10.6 Multiple Target Spaces

An array registered to both MNI and Talairach:

```json
"space_transforms": [
  {
    "to": { "name": "nifti:mni152" },
    "forward": {
      "affine": [[ 0.9834, -0.0512,  0.0307, -2.10],
                  [ 0.0536,  0.9971, -0.0582,  1.30],
                  [-0.0278,  0.0601,  0.9978,  0.70]]
    }
  },
  {
    "to": { "name": "nifti:talairach" },
    "forward": {
      "affine": [[ 0.9510, -0.0480,  0.0290, -1.85],
                  [ 0.0500,  0.9620, -0.0550,  1.10],
                  [-0.0260,  0.0570,  0.9650,  0.55]]
    }
  }
]
```

Each entry is an independent edge from world space. The library does not infer or compose transforms between `nifti:mni152` and `nifti:talairach`.

### 10.7 FITS Celestial Reference Frame

An astronomical image with a transform to a celestial coordinate frame, including per-axis extension metadata on the target space:

```json
"space_transforms": [
  {
    "to": {
      "name": "fits:icrs",
      "axes": [
        {
          "kind": "space",
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "RA---TAN",
              "crval": 184.5575
            }
          }
        },
        {
          "kind": "space",
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "DEC--TAN",
              "crval": -5.7890
            }
          }
        }
      ]
    },
    "forward": {
      "affine": [[-0.001, 0, 184.5575],
                  [0, 0.001, -5.7890]]
    },
    "metadata": {
      "description": "Linear WCS approximation, accurate near reference pixel"
    }
  }
]
```

The FITS extension defines the `icrs` space. The per-axis extension metadata on the target axes carries the full WCS parameters. The affine is a linear approximation of the projection, valid near the reference pixel.

### 10.8 Declared by a Domain Extension

The NIfTI extension declares a transform to its own named space within its own block:

```json
"extensions": {
  "nifti": {
    "version": "1.0",
    "tags": {
      "sform_code": 4
    },
    "space_transforms": [
      {
        "to": { "name": "mni152" },
        "forward": { "identity": true }
      }
    ]
  }
}
```

The key `"mni152"` is unqualified because it is inside the `nifti` extension block. The library resolves it to `nifti:mni152`. This is equivalent to declaring `"nifti:mni152"` in the convention-level `space_transforms`.

**Extension-level declaration rules:**

- An extension may include a `space_transforms` array within its own block.
- The array uses the same schema as the convention-level `space_transforms` (§5).
- Space names in `to` are unqualified — the extension name is prepended automatically (e.g., `"mni152"` inside the `nifti` block resolves to `nifti:mni152`).
- `from` defaults to `{"space": "world"}`, same as the convention level.
- All consistency rules from §9 apply after name resolution.
- A given fully qualified `(from, to)` pair must not appear in both convention-level and extension-level declarations on the same array.

---

## 11. Design Notes

### 11.1 Why Array Form

`space_transforms` is a JSON array rather than an object keyed by target space name. The array form supports the future group-level case where both `from` and `to` are arbitrary (path-qualified references to different arrays' spaces). An object keyed by target name cannot express this. Using the same syntax at both levels avoids migration.

### 11.2 Why `space_transforms` and Not `transforms`

The field is named `space_transforms` to make the scope explicit: these transforms operate on the spatial dimensions defined by `space` or `space_dimension`. Non-spatial dimensions (time, range/component axes) are outside scope. The `space_` prefix is consistent with other spatial fields in the convention: `space_origin`, `space_direction`, `space_dimension`.

### 11.3 Why Affine and Identity Only

Version 1.1 defines only affine and identity transforms. Displacement fields, coordinate maps, and sequences are deferred. The reasons:

- Affine transforms are self-contained (a matrix in JSON), invertible (matrix inversion), and composable (matrix multiplication). No external array references, no interpolation questions.
- The convention's intrinsic spatial metadata already uses affine machinery. Any implementation that handles the convention already has affine support.
- The naming convention, validation rules, and interaction patterns can be proven out before adding array-referenced transform types.

### 11.4 Relationship to NGFF RFC-5

This amendment covers a subset of NGFF RFC-5's scope (named coordinate spaces, typed transforms) but is simpler because the duckn convention handles the foundational layer:

| RFC-5 concern | duckn equivalent |
|---------------|---------------------|
| Axis definition (name, type, unit) | Per-array `axes[].kind`, `unit` |
| Array space (pixel center/corner) | Per-axis `centering` |
| Pixel-to-physical transform | Per-array `space_origin` + `space_direction` |
| Axis orientation | `space` field (`"right-anterior-superior"`, etc.) |
| Discrete vs. continuous axes | `kind` distinguishes domain (resampleable) from range |
| Axis ordering ambiguity | Does not exist — per-axis metadata is self-describing |
| Named coordinate systems | `to` references with optional `axes` |
| Transform input/output | `from`/`to` space references |
| `scale` and `translation` types | Unnecessary — convention handles this at array level |
| `inputAxes`/`outputAxes` decomposition | Unnecessary — axis `kind` identifies spatial axes |

### 11.5 Space Name Conventions

| Name form | Example | Defined by |
|-----------|---------|-----------|
| Extension-qualified | `nifti:mni152` | The named extension's specification |
| Unqualified (inside extension block) | `mni152` | The enclosing extension; resolves to `extension:name` |
| Ad hoc | `surgical_plan` | This file / local context only |
| Built-in | `world`, `axis-aligned`, `index` | duckn convention (used in `space` field of references, not as target names) |

### 11.6 Convention-Level vs. Extension-Level Declaration

Domain extensions may declare `space_transforms` within their own blocks using unqualified names (§10.8). This allows the NIfTI extension to declare MNI transforms, the FITS extension to declare celestial frame transforms, etc. The convention-level `space_transforms` uses fully qualified names and serves as a general-purpose location for any transform, including to spaces defined by extensions and ad hoc spaces.

Both produce equivalent graph edges. A given fully qualified space name must not appear in both locations on the same array.

### 11.7 Spatial Transforms vs. Value-Frame Transforms

`space_transforms` maps between spatial coordinate systems — where a point is in physical space. This is distinct from two other transform-like concepts in the convention:

- **`measurement_frame`** transforms vector/tensor *component values* from their storage frame to world space. It operates on the meaning of stored values, not on positions. A vector field's components are rotated by the measurement frame; its voxel positions are mapped by `space_transforms`.
- **`gradient_frame`** (DWI extension) identifies the coordinate frame of diffusion gradient vectors. When working in a space reached via `space_transforms` (e.g., MNI), a reader may need to rotate gradient vectors accordingly — but that is a downstream interpretation step, not something `space_transforms` defines.

The boundary is: `space_transforms` says where things are, `measurement_frame` says how vector values are oriented, and `gradient_frame` says which convention the gradients follow. These are orthogonal concerns.

### 11.8 Star Topology

At the array level, `from` is always a built-in space (`world`, `axis-aligned`, or `index`). This means the transform graph is always a star: built-in spaces at the center, named spaces at the leaves. There are no edges between named spaces — you cannot declare a transform from `nifti:mni152` to `nifti:talairach` directly.

This is deliberate. Multi-hop composition and cross-space transforms introduce ambiguity about which path to take. A library that needs `mni152 → talairach` can compose `inverse(world → mni152)` with `world → talairach` using the stored affines.

### 11.9 Forward Compatibility with Group-Level Transforms

The `from` and `to` fields use a structured object form (`{"space": "..."}`, `{"name": "..."}`) rather than bare strings. This reserves space for a future `{"path": "...", "space": "..."}` form that references another array's built-in space by Zarr path. The array-level syntax is a subset of the group-level syntax, ensuring that no migration is needed when group-level transforms are specified.