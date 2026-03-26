# duckn: Axis-Rich Array Metadata Convention for Zarr V3

**Status:** Draft proposal
**Version:** 1.1

---

## 1. Purpose

duckn builds on the semantic richness of the NRRD file format — a nested convention layered inside Zarr V3, hence the name (short for "turducken").

This document defines a metadata convention for Zarr V3 arrays that encodes the semantic richness of the NRRD file format — structured axis metadata, spatial orientation, measurement frames — within Zarr's standard `attributes` mechanism.

The result is a valid Zarr V3 store that any Zarr reader can open and access as raw array data. Readers that understand this convention additionally gain:

- Per-axis semantics: what each axis represents, how its samples are centered, and what units apply
- Spatial embedding: how array coordinates map to a named world coordinate system
- Measurement frame: the coordinate frame in which vector/tensor coefficients are expressed
- Value interpretation: how stored values relate to real-world quantities

**Design principles:**

- **The storage format is Zarr.** There are no new file types, no custom parsers. All convention metadata lives in `attributes` of a standard `zarr.json`.
- **Absent means unknown.** If optional information was never specified, it must not be invented. Omit the field entirely. (Inherited from NRRD.)
- **Each axis is a coherent object.** Per-axis properties are bundled together, not scattered across parallel arrays.
- **Memory layout and spatial embedding are orthogonal.** Axis ordering describes storage; the spatial fields describe the world. These are independent concerns.

---

## 2. Relationship to Zarr V3

The following array-level concerns are handled entirely by Zarr and are **not** part of this convention:

| Concern | Zarr V3 field |
|---------|---------------|
| Number of dimensions | `len(shape)` |
| Samples per axis | `shape` |
| Element type | `data_type` |
| Byte ordering | `codecs` (bytes codec) |
| Compression | `codecs` |
| Chunking | `chunk_grid` |
| Default value for missing chunks | `fill_value` |
| Dimension names | `dimension_names` |
| Chunk key layout | `chunk_key_encoding` |

This convention defines the content of a single JSON object stored under the key `"duckn"` within the Zarr array's `attributes`:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [128, 128, 60, 6],
  "data_type": "float32",
  "dimension_names": ["i", "j", "k", "component"],
  "chunk_grid": { ... },
  "codecs": [ ... ],
  "fill_value": 0.0,
  "attributes": {
    "duckn": {
      ...
    }
  }
}
```

A Zarr reader that does not recognize the `"duckn"` key simply ignores it. The array remains fully accessible.

---

## 3. Convention Fields

All fields within the `"duckn"` object are optional. Their presence or absence carries meaning: a missing field means the information is unknown or inapplicable. Fields must not be included with null or sentinel values to represent "don't know" — omit them instead.

### 3.1 Top-Level Fields

These describe the array as a whole.

#### `version`

The version of this convention. Format is `"major.minor"`.

```json
"version": "1.0"
```

A major version increment indicates breaking changes — a reader for version 1.x must not attempt to interpret version 2.x metadata. A minor version increment indicates additive changes (new optional fields, new `kind` values, new transform types). A reader for version 1.0 can safely read version 1.3 and ignore unknown fields.

This field should always be present.

#### `space`

Names the world coordinate system in which the array is spatially embedded. Each space name encodes the positive direction of each axis, fully specifying the coordinate frame.

**Naming convention:** space names follow the pattern `{+X}-{+Y}-{+Z}`, describing the positive direction of each axis. For medical spaces, directions are relative to the patient; for general spaces, directions are relative to the default camera/viewer orientation.

Abbreviations (e.g., `"RAS"`, `"LPS"`) are accepted on read and normalized to the full name. Writers should always use the full name.

**Medical / patient-based spaces:**

| Value | Abbrev | Dim | +X | +Y | +Z | Handedness | Used by |
|-------|--------|-----|----|----|-----|-----------|---------|
| `"right-anterior-superior"` | RAS | 3 | Right | Anterior | Superior | Right | NIfTI, 3D Slicer, FreeSurfer |
| `"left-anterior-superior"` | LAS | 3 | Left | Anterior | Superior | Left | Analyze 7.5 |
| `"left-posterior-superior"` | LPS | 3 | Left | Posterior | Superior | Right | DICOM, ITK, VTK |

**Scanner and instrument spaces:**

| Value | Dim | Description |
|-------|-----|-------------|
| `"scanner-xyz"` | 3 | Scanner-based, right-handed (ACR/NEMA 2.0) |

**General 3D spaces:**

Each describes the positive direction of +X, +Y, +Z relative to the default viewer orientation (viewer facing the screen, head up).

| Value | Dim | +X | +Y | +Z | Handedness | Used by |
|-------|-----|----|----|-----|-----------|---------|
| `"right-up-back"` | 3 | Right | Up | Toward viewer | Right | Three.js, OpenGL |
| `"right-up-forward"` | 3 | Right | Up | Into screen | Left | Babylon.js, DirectX, Unity |
| `"right-forward-up"` | 3 | Right | Forward (into screen) | Up | Right | Blender, CAD, engineering |
| `"right-down-forward"` | 3 | Right | Down | Into screen | Right | Vulkan, screen space |
| `"forward-right-up"` | 3 | Forward | Right | Up | Left | Unreal Engine |
| `"east-north-up"` | 3 | East | North | Up | Right | Geospatial, surveying |

**Generic (no axis semantics):**

| Value | Dim | Description |
|-------|-----|-------------|
| `"3D-right-handed"` | 3 | Generic right-handed 3D space, axis directions unspecified |
| `"3D-left-handed"` | 3 | Generic left-handed 3D space, axis directions unspecified |

**Time variants:** Any 3D space name may be suffixed with `"-time"` to produce a 4D space (e.g., `"right-anterior-superior-time"`, `"right-up-back-time"`). The fourth dimension is always time.

The space identifier names the coordinate system and its basis vectors. It does **not** imply anything about axis ordering. Axes can be reordered independently of the spatial embedding.

When the world space is not one of the named spaces above, use `space_dimension` instead.

#### `space_dimension`

An integer giving the dimension of the world space, used when no named `space` applies. Exactly one of `space` or `space_dimension` should be present when spatial embedding is defined. Must not be used together with `space`.

#### `space_origin`

A vector (JSON array of numbers) giving the world-space position of the first sample in the array — the sample at the lowest memory address.

- Independent of centering convention (node vs. cell)
- Independent of axis ordering
- Number of components must equal the space dimension

```json
"space_origin": [-127.0, -127.0, 0.0]
```

#### `measurement_frame`

A matrix (JSON array of row vectors) that transforms coordinates in the measurement frame to coordinates in world space. Each inner array is one row of the matrix — consistent with C order used throughout the convention.

```json
"measurement_frame": [[1,0,0], [0,1,0], [0,0,1]]
```

To apply the transform: `world = measurement_frame @ measurement_coords` (matrix × column vector).

The matrix is always square with side length equal to the space dimension, regardless of which axes carry vector/tensor kinds. This addresses the distinction between the coordinate frame used to measure vector/tensor coefficients and the world space in which the image orientation is defined.

**Note**: NRRD stores the measurement frame as column vectors. The duckn convention stores it as row vectors (transposed relative to NRRD) for consistency with the rest of the convention.

#### `sample_units`

A string giving the units of the scalar values stored in the array. Describes the stored values themselves, not the coordinates of any axis.

```json
"sample_units": "HU"
```

#### `value_transforms`

An ordered list of transforms that convert stored array values to values in `sample_units`. Applied in order, first to last.

Each transform is an object with `name` and (optionally) `parameters`:

```json
"value_transforms": [
  { "name": "linear", "parameters": { "slope": 1.0, "intercept": -1024.0 } }
]
```

**Direction convention:** transforms map from stored values to real-world values.
For `linear`: `real_value = stored_value * slope + intercept`.

If `value_transforms` is absent, stored values are the real values (in `sample_units` if specified).

**Defined transforms:**

| Name | Parameters | Formula (stored → real) |
|------|-----------|-------------------------|
| `linear` | `slope`, `intercept` | `real = stored * slope + intercept` |

Additional transforms may be defined in future versions. A reader that encounters an unknown transform name should treat the value mapping as unknown — the raw stored data remains accessible, but its physical interpretation is undefined.

#### `intent`

A string identifying what the array represents as a whole — its semantic purpose, not its structure.

Suggested vocabulary (not exhaustive):

| Value | Description |
|-------|-------------|
| `"label-map"` | Integer label segmentation |
| `"probability-map"` | Voxel-wise probabilities |
| `"displacement-field"` | Spatial displacement vectors |
| `"diffusion-tensor"` | Diffusion tensor field |
| `"statistical-map"` | Statistical test output |
| `"velocity-field"` | Velocity vector field |

Intent describes purpose; `kind` on each axis describes structure. The two are complementary.

#### `extensions`

An object containing domain-specific metadata that depends on NRRD convention semantics to be interpretable. Each key is an extension name, and its value is a JSON object defined by that extension's own specification.

```json
"extensions": {
  "dwmri": {
    "version": "1.0",
    "schema": "https://example.org/dwmri/v1.0/schema.json",
    "b_values": [0, 1000, 1000],
    "protocol": "DTI"
  }
}
```

Rules:

- Each extension object must contain a `"version"` field (format `"major.minor"`), following the same compatibility rules as the convention version. Version semantics (major = breaking, minor = additive) are defined by each extension's specification.
- If an extension is used anywhere in the `"duckn"` object — including only on per-axis `extensions` — it must have an entry in the top-level `extensions` with at least a `"version"`. The top-level entry is the declaration; per-axis entries are the data.
- Each extension object may contain an optional `"schema"` field: a URL pointing to a schema or specification document for the extension. What the URL resolves to is up to the extension author — a JSON Schema for machine validation, a human-readable specification, or both. Readers may use this for validation or surface it to users as documentation. If absent, the reader has only the extension name and version to work with.
- Extension names must be unique. A registry of well-known extension names may be established separately.
- Keys at the top level of the `"duckn"` object (outside `"extensions"`) are reserved for this convention. Domain-specific metadata must not be added there.
- A reader that encounters an unknown extension name must ignore it.
- Extensions may depend on NRRD convention fields such as `measurement_frame` or `space`. These dependencies should be documented in the extension's specification.

Extensions also appear per-axis; see §3.2.

### 3.2 Per-Axis Fields: The `axes` Array

The `axes` field is a JSON array with one object per array dimension, in the same order as `shape` and `dimension_names`. Each axis object may contain any combination of the following fields.

```json
"axes": [
  { ... },
  { ... },
  { ... }
]
```

The length of `axes` must equal the number of dimensions in the array.

#### `kind`

Identifies what the axis represents. The vocabulary is inherited from NRRD, divided into two categories.

**Domain kinds** — independent variables, meaningful to resample or interpolate:

| Kind | Description |
|------|-------------|
| `"domain"` | Generic domain axis |
| `"space"` | Spatial domain axis |
| `"time"` | Temporal domain axis |

**Range kinds** — dependent variables or non-scalar component axes, not meaningful to resample:

| Kind | Required axis size | Description |
|------|-------------------|-------------|
| `"list"` | — | Generic non-scalar components |
| `"point"` | — | Coordinates of a point |
| `"vector"` | — | Contravariant vector coefficients |
| `"covariant-vector"` | — | Covariant vector coefficients (e.g., gradient) |
| `"normal"` | — | Unit-length covariant vector |
| `"stub"` | 1 | Single-sample placeholder |
| `"scalar"` | 1 | Explicitly scalar |
| `"complex"` | 2 | Complex number: real, imaginary |
| `"2-vector"` | 2 | Any 2-vector |
| `"3-color"` | 3 | Generic 3-component color |
| `"RGB-color"` | 3 | Red, green, blue (in order) |
| `"HSV-color"` | 3 | Hue, saturation, value (in order) |
| `"XYZ-color"` | 3 | CIE XYZ coefficients (in order) |
| `"4-color"` | 4 | Generic 4-component color |
| `"RGBA-color"` | 4 | Red, green, blue, alpha (in order) |
| `"3-vector"` | 3 | Any 3-vector |
| `"3-gradient"` | 3 | Covariant 3-vector |
| `"3-normal"` | 3 | Unit-length covariant 3-vector |
| `"4-vector"` | 4 | Any 4-vector |
| `"quaternion"` | 4 | (w, x, y, z); w real, no normalization assumed |
| `"2D-symmetric-matrix"` | 3 | Mxx Mxy Myy |
| `"2D-masked-symmetric-matrix"` | 4 | mask Mxx Mxy Myy |
| `"2D-matrix"` | 4 | Mxx Mxy Myx Myy |
| `"2D-masked-matrix"` | 5 | mask Mxx Mxy Myx Myy |
| `"3D-symmetric-matrix"` | 6 | Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-masked-symmetric-matrix"` | 7 | mask Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-matrix"` | 9 | Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |
| `"3D-masked-matrix"` | 10 | mask Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |

Where a required axis size is listed, the corresponding dimension in `shape` must match.

#### `centering`

Declares whether samples on this axis are indexed by cell centers or cell corners.

| Value | Meaning |
|-------|---------|
| `"cell"` | Each sample represents a cell; the sample's position is the cell center |
| `"node"` | Each sample sits at a grid node (cell corner/boundary) |

For a 1D axis with 4 samples and a given spacing:

```
Cell-centered:     |  0  |  1  |  2  |  3  |
                   +--.--+--.--+--.--+--.--+
                   4 samples, 4 cells, extent = 4 × spacing

Node-centered:     0     1     2     3
                   +-----+-----+-----+
                   4 samples, 3 intervals, extent = 3 × spacing
```

The same number of samples covers a different spatial extent depending on centering. Equivalently, for a fixed extent, the two conventions produce different spacings and different sample positions. Getting this wrong shifts the image by half a voxel.

This affects interpolation (how to handle boundaries), resampling (the relationship between sample count and spatial extent), and bounding box computation. Most imaging formats leave this implicit; this convention makes it explicit and per-axis.

Omit if centering is unknown or meaningless for this axis (e.g., a tensor component axis).

#### `space_direction`

A vector (JSON array of numbers) giving the displacement in world space when incrementing this axis coordinate by one. Encodes both direction and spacing — these are **not** unit vectors.

```json
"space_direction": [0, 0, 2.5]
```

The number of components must equal the space dimension. Present only for axes that are spatially embedded. Omit for non-spatial axes (e.g., color components, tensor coefficients).

#### `thickness`

A number giving the nominal extent of the region measured to produce each sample value along this axis. Distinct from spacing (which is encoded in the magnitude of `space_direction`).

The primary use case is medical imaging where slice thickness and slice spacing differ.

#### `unit`

A string giving the units of the coordinate along this axis. For spatial axes with a `space_direction`, this should be consistent with the world space (e.g., `"mm"`). For non-spatial domain axes (e.g., time), this is the only place to state the unit (e.g., `"s"`, `"ms"`).

Omit for axes where units are meaningless (e.g., color component axes).

#### `samples`

An array with one entry per sample along this axis, describing per-sample variation that the uniform model (single `space_direction`, single `thickness`) cannot represent. If present, its length must equal the axis size in `shape`.

Each entry is an object. All fields are optional — an empty object `{}` means "use the defaults from the axis-level fields."

- **`position`** — a scalar giving this sample's coordinate value along this axis. For spatial axes, this is the distance along `space_direction` from the origin. For temporal axes, this is the timestamp in the axis `unit`. If absent, the position is computed from `space_direction` as usual (uniform spacing).
- **`origin`** — a vector with the same number of components as `space_origin`, giving the full world-space position of this sample. Overrides the position derived from `space_origin` + index × `space_direction`. Use when the origin shifts in multiple spatial dimensions per sample (e.g., gantry tilt, non-parallel slices in cardiac MR).
- **`thickness`** — per-sample thickness. If absent, the sample inherits the axis-level `thickness`.
- **`directions`** — per-sample orientation matrix (array of direction vectors). Overrides the axis-level `space_direction` vectors for all spatial axes at this sample. Use for non-parallel slices (e.g., freehand ultrasound, rotational acquisitions).
- **`metadata`** — per-sample open metadata dict, following the same pattern as segment-level `metadata`. Keyed by application or standard name. For example, a DICOM key could store per-slice acquisition parameters (`InstanceNumber`, `TriggerTime`, etc.).

`position` and `origin` can coexist — `origin` is authoritative for the world position, `position` is the scalar projection along the axis direction (convenience/validation). Either can appear alone, or neither (defaults to uniform spacing).

Example: non-uniform spacing with per-sample thickness:

```json
{
  "kind": "space",
  "centering": "cell",
  "space_direction": [0, 0, 2.5],
  "thickness": 2.5,
  "unit": "mm",
  "samples": [
    { "position": 0.0, "thickness": 3.0 },
    { "position": 2.5 },
    { "position": 5.5, "thickness": 2.0 }
  ]
}
```

Example: per-slice gantry tilt with shifted origins:

```json
{
  "kind": "space",
  "centering": "cell",
  "space_direction": [0, 0, 2.5],
  "unit": "mm",
  "samples": [
    { "origin": [0.0, 0.0, 0.0] },
    { "origin": [0.3, 0.0, 2.5] },
    { "origin": [0.6, 0.0, 5.0] }
  ]
}
```

Example: per-sample DICOM metadata:

```json
"samples": [
  { "position": 0.0, "metadata": {"dicom": {"InstanceNumber": 1, "TriggerTime": 0.0}} },
  { "position": 2.0, "metadata": {"dicom": {"InstanceNumber": 2, "TriggerTime": 42.5}} },
  { "position": 4.0, "metadata": {"dicom": {"InstanceNumber": 3, "TriggerTime": 85.0}} }
]
```

`samples` is allowed on any axis — spatial, temporal, or otherwise. A time axis with irregular temporal sampling can use `position` to specify per-frame timestamps. An empty object `{}` at any index means "this sample uses the uniform defaults."

#### `extensions`

An object containing domain-specific metadata for this axis that depends on NRRD convention semantics. Same structure and rules as the top-level `extensions` (§3.1).

Use per-axis extensions when the metadata describes what a specific axis represents. For example, diffusion-weighted imaging gradient directions belong on the axis whose `kind` is `"list"`, because each position along that axis corresponds to a specific gradient:

```json
{
  "kind": "list",
  "extensions": {
    "dwmri": {
      "b_values": [0, 1000, 1000, 1000, 1000],
      "gradients": [[0,0,0], [1,0,0], [0,1,0], [0,0,1], [0.707,0.707,0]]
    }
  }
}
```

A reader that does not understand the `"dwmri"` extension still knows the axis is a `"list"` and should not be resampled. A reader that understands it additionally knows the physical meaning of each position along the axis, and can interpret the gradient vectors using the array-level `measurement_frame`.

---

## 4. Consistency Rules

- If `space` is present, it implies a space dimension. All `space_direction` vectors, the `space_origin` vector, `measurement_frame` rows (and columns), and any `space_dimension` (if present instead of `space`) must have this number of components.
- The length of `axes` must equal the number of dimensions (`len(shape)`).
- Where a `kind` specifies a required axis size, the corresponding element of `shape` must match.
- If an axis has `samples`, its length must equal the corresponding element of `shape`.
- `space` and `space_dimension` are mutually exclusive. Use at most one.
- For a complete spatial embedding, at least one axis should have a `space_direction`, and `space_origin` should be present. Partial orientation (e.g., directions without an origin) is permitted but limits what downstream processing can do.

---

## 5. Fields Deliberately Excluded

The following NRRD fields are **not** part of this convention, with rationale:

| NRRD field | Reason for exclusion |
|---|---|
| `dimension`, `sizes`, `type` | Redundant with Zarr's `shape` and `data_type` |
| `encoding`, `endian` | Handled by Zarr's `codecs` |
| `block size` | Zarr's type system covers this |
| `data file` | The Zarr store is the data |
| `line skip`, `byte skip` | Artifacts of wrapping foreign file formats |
| `number` | Vestigial in NRRD itself |
| `spacings` | Redundant: recoverable from the magnitude of each axis's `space_direction` |
| `axis mins`, `axis maxs` | Derivable from `space_origin`, per-axis `space_direction`, `shape`, and `centering` |
| `labels` | Zarr's `dimension_names` serves this role |
| `units` (per-axis, as parallel array) | Replaced by `unit` within each axis object |
| `space units` (as parallel array) | Replaced by `unit` within each axis object |
| `min`, `max` | Unreliable cached extremes; not the format's job |
| `old min`, `old max` | Replaced by `value_transforms` |
| `content` | If needed, use a regular Zarr attribute; no special status |

---

## 6. Examples

### 6.1 Scalar MRI Volume

A 256×256×128 16-bit MRI volume in RAS space, 1mm × 1mm × 2mm voxels:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [256, 256, 128],
  "data_type": "int16",
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
      "space": "right-anterior-superior",
      "space_origin": [-127.5, -127.5, 0.0],
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
      ]
    }
  }
}
```

### 6.2 CT Volume with Value Transform

A CT volume stored as unsigned 16-bit integers, with Hounsfield unit rescaling:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [512, 512, 300],
  "data_type": "uint16",
  "dimension_names": ["i", "j", "k"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [128, 128, 64] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "gzip", "configuration": { "level": 5 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "left-posterior-superior",
      "space_origin": [-249.5, -249.5, -150.0],
      "sample_units": "HU",
      "value_transforms": [
        { "name": "linear", "parameters": { "slope": 1.0, "intercept": -1024.0 } }
      ],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.976, 0.0, 0.0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.0, 0.976, 0.0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "thickness": 1.0,
          "space_direction": [0.0, 0.0, 1.0],
          "unit": "mm"
        }
      ]
    }
  }
}
```

### 6.3 Diffusion Tensor Volume

A 128×128×60 volume of 3D symmetric diffusion tensors:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [128, 128, 60, 6],
  "data_type": "float32",
  "dimension_names": ["i", "j", "k", "component"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [32, 32, 15, 6] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0.0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "right-anterior-superior",
      "space_origin": [-127.0, -127.0, 0.0],
      "measurement_frame": [[1,0,0], [0,1,0], [0,0,1]],
      "sample_units": "mm²/s",
      "intent": "diffusion-tensor",
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [2, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 2, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "thickness": 2.5,
          "space_direction": [0, 0, 2.5],
          "unit": "mm"
        },
        {
          "kind": "3D-symmetric-matrix"
        }
      ]
    }
  }
}
```

### 6.4 Diffusion-Weighted Image Series (Extensions)

A 128×128×60 DWI volume with 13 gradient directions, demonstrating per-axis and top-level extensions:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [128, 128, 60, 13],
  "data_type": "int16",
  "dimension_names": ["i", "j", "k", "diffusion"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 20, 13] }
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
      "space_origin": [-128.0, -142.2, 99.7],
      "measurement_frame": [[-1,0,0], [0,1,0], [0,0,1]],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [2, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 2, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "thickness": 2.2,
          "space_direction": [0, 0, 2.2],
          "unit": "mm"
        },
        {
          "kind": "list",
          "extensions": {
            "dwmri": {
              "b_value": 1000,
              "gradients": [
                [0, 0, 0],
                [0.707, 0.0, 0.707],
                [-0.707, 0.0, 0.707],
                [0.0, 0.707, 0.707],
                [0.0, 0.707, -0.707],
                [0.707, 0.707, 0.0],
                [-0.707, 0.707, 0.0],
                [1, 0, 1],
                [-1, 0, 1],
                [0, 1, 1],
                [0, 1, -1],
                [1, 1, 0],
                [-1, 1, 0]
              ]
            }
          }
        }
      ],
      "extensions": {
        "dwmri": {
          "version": "1.0"
        }
      }
    }
  }
}
```

The `"dwmri"` extension is per-axis: it describes what each position along the diffusion axis represents. The gradient vectors are expressed in the coordinate frame defined by the top-level `measurement_frame`. A reader that doesn't understand the `"dwmri"` extension still sees a 4D array with three spatial axes and a `"list"` axis.

### 6.5 RGBA Image

A 640×480 RGBA image with color components contiguous in memory (C order, last dimension fastest):

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [480, 640, 4],
  "data_type": "uint8",
  "dimension_names": ["y", "x", "channel"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [120, 160, 4] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "axes": [
        { "kind": "space", "centering": "node" },
        { "kind": "RGBA-color" }
      ]
    }
  }
}
```

### 6.6 CT with DICOM Provenance

An anonymized CT scan that preserves acquisition metadata via a `dicom` extension. The extension uses a `tags` object to namespace DICOM keywords (PS3.6) separately from extension-level fields. Fields set to `null` indicate values that existed in the source but were redacted during anonymization.

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [300, 512, 512],
  "data_type": "uint16",
  "dimension_names": ["z", "y", "x"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [30, 512, 512] }
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
      "space_origin": [-249.5, -249.5, -150.0],
      "sample_units": "HU",
      "value_transforms": [
        { "name": "linear", "parameters": { "slope": 1.0, "intercept": -1024.0 } }
      ],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 5.0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.703, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.703, 0, 0],
          "unit": "mm"
        }
      ],
      "extensions": {
        "dicom": {
          "version": "1.0",
          "schema": "https://example.org/dicom-zarr/v1.0/schema.json",
          "anonymized": true,
          "tags": {
            "Modality": "CT",
            "SeriesInstanceUID": "1.2.840.113619.2.55.3.604688119.969.1069843699.84",
            "StudyDescription": "CHEST W/O CONTRAST",
            "SeriesDescription": "AXIAL 5mm",
            "InstitutionName": null,
            "Manufacturer": "GE MEDICAL SYSTEMS",
            "ManufacturerModelName": "LightSpeed16",
            "KVP": 120,
            "XRayTubeCurrent": 200,
            "SliceThickness": 5.0,
            "PixelSpacing": [0.703, 0.703],
            "AcquisitionDate": null,
            "PatientName": null,
            "PatientID": null,
            "PatientBirthDate": null
          }
        }
      }
    }
  }
}
```

The `dicom` extension separates its own metadata (`version`, `schema`, `anonymized`) from DICOM's vocabulary (everything inside `tags`). DICOM keywords use PascalCase as defined in PS3.6. Numeric values are stored as JSON numbers rather than DICOM's string encoding — the goal is usability, not round-trip VR fidelity. Spatial fields like `SliceThickness` and `PixelSpacing` overlap with the `duckn` axes; the extension carries the DICOM-native values for provenance, while the axes are authoritative for processing.

### 6.7 Minimal

A Zarr array with only a kind annotation and nothing else:

```json
"attributes": {
  "duckn": {
    "version": "1.0",
    "axes": [
      { "kind": "space" }
    ]
  }
}
```

This is a valid use of the convention. It says "these are spatial axes" and nothing more.

---

## 7. Notes

- **Axis ordering** follows Zarr convention. `axes[i]` describes `shape[i]` and `dimension_names[i]` — nothing more. This convention does not assign any memory-layout semantics to dimension order. NRRD defines axes fastest-to-slowest; this convention does not. In Zarr, the relationship between logical dimension order and in-memory byte layout is determined by the codec pipeline (e.g., the default bytes codec uses C order where the last dimension varies fastest; the transpose codec can change this). The convention describes logical dimensions; storage layout is Zarr's concern.

- **Relationship to OME-NGFF:** The OME-NGFF (OME-Zarr) specification defines its own axis metadata and coordinate transformations for bioimaging. This convention is complementary — it addresses a different set of concerns (centering, kind vocabulary, measurement frame) that OME-NGFF does not cover. Coexistence in the same `attributes` object is possible without conflict, as they use different keys.

- **Extensibility of `kind`:** The kind vocabulary is inherited from NRRD and is expected to be stable. New kinds should be added rarely and with caution, per NRRD's own precedent.

- **Extensibility of `value_transforms`:** Only `linear` is defined in this version. New transform types should be defined in future versions of this convention. A reader that encounters an unknown transform name should treat the value interpretation as unknown, but must still provide access to the raw stored data.

- **No display hints.** This convention describes data semantics, not display preferences. Window/level, colormap, and similar rendering concerns belong in application-specific attributes, not in this convention.