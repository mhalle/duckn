# duckn convention 2.0 — draft specification

**Status:** draft for review, 2026-09-27. Not implemented. Supersedes the two world-frame
proposals of 2026-09-26/27, whose decisions it records in §14.

2.0 keeps NRRD's principles and replaces the vocabulary that made them hard to read. It is a
breaking change, made once; a 2.0 reader reads every 1.x file (§12).

---

## 1. The model

A duckn array is described by three things, each answering one question:

| Object | Question | Replaces |
|---|---|---|
| `dimensions` | How is the array laid out, and what does moving along each dimension do? | `axes` |
| `world` | Where are the samples, in space and time? | `space`, `space_dimension`, the spatial axes' `unit`, `space_transforms` |
| `values` | What do the stored numbers mean? | `sample_units`, `value_transforms` |

A *dimension* is one of the array's own dimensions: Zarr's word, the entries of `shape` and
`dimension_names`. An *axis* is one of the world's coordinate axes (x, y, z, t): the word as
geometry and OME-Zarr use it. The two are never called by the same name.

Every dimension answers one question, "what happens when I move along it?", in one of three
ways:

- it **moves through the world**, by a vector: its `step`;
- it **holds the parts of one value** (the red, green and blue of a color; the components of a
  vector or tensor; a list of values): its `components`;
- **nothing is stated**.

The position of a sample is

```
position = origin + Σ index_d · step_d        (over the dimensions d that have a step)
```

where `origin` and every `step` have one component per world axis. This is NRRD's model
(`space origin`, `space directions`) unchanged; only the names and the grouping are new.

---

## 2. Top-level fields

```json
"attributes": {
  "duckn": {
    "version": "2.0",
    "world": { ... },
    "origin": [ ... ],
    "dimensions": [ ... ],
    "values": { ... },
    "intent": "...",
    "unit_systems": { ... },
    "extensions": { ... }
  }
}
```

| Field | Section | Meaning |
|---|---|---|
| `version` | §2.1 | The convention version: `"2.0"`. |
| `world` | §3 | The world's axes, and how this world relates to other frames. |
| `origin` | §4 | The world position of the first sample. |
| `dimensions` | §5 | One object per array dimension, in the order of `shape`. |
| `values` | §6 | What the stored numbers mean. |
| `intent` | unchanged from 1.x | What the array represents as a whole (`"label-map"`, `"diffusion-tensor"`, …). |
| `unit_systems` | unchanged from 1.x | The registry of unit systems used by structured units (units spec). |
| `extensions` | unchanged from 1.x | Domain metadata, per extension, each with its own `version`. |

Every field is optional. **Absent means unknown**: a field is left out rather than given a
default or a sentinel, and a reader never assumes what a file does not state.

### 2.1 `version`

The version of this convention, `"major.minor"`. It is the only version number a reader acts
on. A minor version only adds; a major version breaks. Each extension carries its own version in
its own block, and software that implements the convention has its own release numbers, which
say nothing about a file.

---

## 3. `world`

```json
"world": {
  "name": "dicom:1.2.840.113619.2.55.3.604688119.969.1069843699.84",
  "axes": [
    { "name": "x", "unit": "mm", "positive": "left" },
    { "name": "y", "unit": "mm", "positive": "posterior" },
    { "name": "z", "unit": "mm", "positive": "superior" }
  ],
  "transforms": [ ... ]
}
```

| Field | Meaning |
|---|---|
| `axes` | The world's axes, in the order of every `origin`, `step` and per-sample vector's components. Its length is the world's dimension. Required when `world` is present. |
| `name` | The frame's identity, when known: arrays whose worlds have the same `name` share one set of physical coordinates. Extension-qualified (`"dicom:<FrameOfReferenceUID>"`, `"nifti:mni152"`) or ad hoc (no colon). |
| `transforms` | How this world relates to other frames (§7). |

**`name` stays one string with a colon** (the owner's decision, 2026-09-27). A name is an
identifier and must work as a key (in a JSON object, a lookup table, a URL), which an object
cannot. Its one parse, the extension prefix, happens in one resolver.

### 3.1 A world axis

| Field | Meaning |
|---|---|
| `name` | A short identifier, unique among this world's axes (`"x"`, `"t"`). Transforms address axes by it (§7). |
| `unit` | The unit of this coordinate: a string or a structured unit (units spec). Every `origin`, `step` and per-sample component along this axis is in it. |
| `positive` | For a spatial axis: the direction in which coordinates increase. One of `left`, `right`, `anterior`, `posterior`, `superior`, `inferior`. Absent: unknown. |

**What an axis measures follows from its unit.** An axis whose unit is a length is spatial; a
time unit, temporal; any other unit (a frequency, a chemical shift) is some other continuous
coordinate. An axis with `positive` is spatial. An axis with neither measures something unknown.

**The `positive` terms** are relative to the body in the standard anatomical position
(Terminologia Anatomica 2's notes on *anterior/posterior* and *superior/inferior*), the frame of
DICOM's patient-based coordinates and of NIfTI's RAS. They form three pairs — left/right,
anterior/posterior, superior/inferior — and a world uses at most one term of each pair. A term
names where values *grow*, not a span from one end to the other: `"positive": "left"` means
coordinates increase toward the patient's left, as CF's `positive: "up"` and ISO 19111's axis
direction state it. The opposite end is the pair's other term.

**Handedness** follows from the terms and is never stated.

### 3.2 Examples of worlds

| World | `axes` |
|---|---|
| DICOM patient coordinates (LPS) | `left`, `posterior`, `superior`, each in mm |
| NIfTI (RAS) | `right`, `anterior`, `superior`, each in mm |
| A time series in LPS | the three above, plus `{ "name": "t", "unit": "s" }` |
| A microscope stage, orientation unknown | three axes in µm, no `positive` |
| An MR spectroscopic image | three spatial axes plus `{ "name": "delta", "unit": "ppm" }` |
| A 2D slide | two axes in µm |

---

## 4. `origin`

The world position of the first sample (index 0 on every dimension), one component per world
axis. For a time axis it is the time of the first sample, measured from the start of the series.
It is a point, not a property of the world: arrays that share a world have their own origins.

The position does not depend on `centering` (§5.1): the origin is where the first sample *is*,
whether it stands for a cell or a node.

---

## 5. `dimensions`

One object per array dimension, in the order of `shape` and `dimension_names`. A dimension's
name is Zarr's `dimension_names` entry and is not repeated here.

Each dimension has at most one of `step` (§5.1) and `components` (§5.2). A dimension with
neither states nothing about what moving along it means.

### 5.1 A dimension that moves through the world

```json
{ "step": [0, 0.26, 2.49], "centering": "cell", "thickness": 2.5 }
```

| Field | Meaning |
|---|---|
| `step` | The world displacement of one increment of this dimension's index: direction and spacing together, one component per world axis. Not a unit vector. |
| `centering` | `"cell"`: each sample stands for a cell centered on its position, so the grid extends half a step beyond the first and last samples. `"node"`: samples sit on the grid's nodes, and the grid ends at the first and last samples. It sets the grid's extent and how it aligns with another, never a sample's position. Absent: unknown. |
| `thickness` | The extent of what was measured to produce each sample, along the step, in world units: slice thickness for a spatial dimension, an integration window for time. Distinct from the spacing, which is the step's length. |
| `samples` | Per-sample variation the uniform model cannot state (§5.4). |
| `extensions` | Per-dimension extension metadata. |

A step's non-zero components say what the dimension moves through: a dimension stepping only
along spatial axes is spatial, one stepping along a time axis is temporal. A step may move
through space and time at once (a moving-table acquisition).

The steps of the dimensions that have one are **linearly independent**: two indices never land
on one position. There may be fewer of them than world axes: a single 2D slice in a 3D world.

### 5.2 A dimension that holds the parts of one value

```json
{ "components": "RGB-color", "color_space": "srgb" }
```

| Field | Meaning |
|---|---|
| `components` | What the values along this dimension are, from the table below. |
| `color_space` | For `RGB-color`, `RGBA-color` and `XYZ-color`: what the components mean (§5.3). |
| `frame` | For components that are coordinates in space: the basis they are written in (§5.3). |
| `samples` | Per-sample metadata (§5.4). |
| `extensions` | Per-dimension extension metadata. |

The vocabulary is NRRD's range kinds, word for word, so a NRRD file's `kinds` map without a
table. Where a size is given, the dimension's length in `shape` must equal it.

| `components` | Size | The values along the dimension are |
|---|---|---|
| `"list"` | any | a list of values with no further structure (the volumes of a diffusion series, the layers of a segmentation, spectral channels) |
| `"point"` | any | the coordinates of a point |
| `"vector"` | any | the coefficients of a contravariant vector |
| `"covariant-vector"` | any | the coefficients of a covariant vector (a gradient) |
| `"normal"` | any | a unit-length covariant vector |
| `"stub"` | 1 | a single-sample placeholder |
| `"scalar"` | 1 | one scalar, stated explicitly |
| `"complex"` | 2 | real, imaginary |
| `"2-vector"` | 2 | any 2-vector |
| `"3-color"` | 3 | a generic 3-component color |
| `"RGB-color"` | 3 | red, green, blue |
| `"HSV-color"` | 3 | hue, saturation, value |
| `"XYZ-color"` | 3 | CIE XYZ |
| `"4-color"` | 4 | a generic 4-component color |
| `"RGBA-color"` | 4 | red, green, blue, alpha (alpha straight, not premultiplied) |
| `"3-vector"` | 3 | any 3-vector |
| `"3-gradient"` | 3 | a covariant 3-vector |
| `"3-normal"` | 3 | a unit-length covariant 3-vector |
| `"4-vector"` | 4 | any 4-vector |
| `"quaternion"` | 4 | w, x, y, z; w real, no normalization assumed |
| `"2D-symmetric-matrix"` | 3 | Mxx Mxy Myy |
| `"2D-masked-symmetric-matrix"` | 4 | mask Mxx Mxy Myy |
| `"2D-matrix"` | 4 | Mxx Mxy Myx Myy |
| `"2D-masked-matrix"` | 5 | mask Mxx Mxy Myx Myy |
| `"3D-symmetric-matrix"` | 6 | Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-masked-symmetric-matrix"` | 7 | mask Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-matrix"` | 9 | Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |
| `"3D-masked-matrix"` | 10 | mask Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |

NRRD's three *domain* kinds (`domain`, `space`, `time`) are not components: a dimension that
samples a continuous coordinate has a `step` (§5.1).

### 5.3 `color_space` and `frame`

**`color_space`** is one of CSS Color 4's predefined spaces: `srgb`, `srgb-linear`,
`display-p3`, `display-p3-linear`, `a98-rgb`, `prophoto-rgb` and `rec2020` for `RGB-color` and
`RGBA-color`; `xyz-d50` and `xyz-d65` for `XYZ-color`. The components run from 0 to 1: an
unsigned integer array read through `values.transforms: []` spans its type's full range; a
floating-point array holds the components themselves; any other encoding states a mapping in
`values.transforms`. A color space is never stated by an ICC profile. Absent: unknown. (As 1.2.)

**`frame`** is a square matrix over the spatial world axes, one row per axis: a vector `v`
written in this dimension's components is `frame @ v` in the world's spatial axes. It is 1.x's
`measurement_frame`, moved onto the dimension it describes, which a flat NRRD header could not
do. It applies to the kinds whose components are coordinates in space: `point`, `vector`,
`covariant-vector`, `normal`, `3-vector`, `3-gradient`, `3-normal` and the matrix kinds.
**Absent: the basis is unknown.** A writer whose components are already in the world's axes
writes the identity matrix, stating it.

### 5.4 `samples`

An array with one entry per position along the dimension, for what the uniform model cannot
state. Its length equals the dimension's length. Every field is optional; `{}` means "the
uniform defaults".

| Field | Meaning |
|---|---|
| `position` | This sample's coordinate along the dimension's step, as a distance from the origin in world units: irregular slice spacing, irregular frame times. |
| `origin` | This sample's full world position, overriding `origin + index · step` (gantry tilt, per-slice acquisition times in a space-time world). |
| `steps` | The step vectors of every dimension that has one, at this sample (non-parallel slices). Was `directions`. |
| `thickness` | This sample's thickness. |
| `metadata` | Open per-sample metadata, keyed by application or standard (a DICOM slice's tags). |

---

## 6. `values`

```json
"values": {
  "unit": "HU",
  "transforms": [ { "name": "linear", "parameters": { "slope": 1.0, "intercept": -1024.0 } } ]
}
```

| Field | Meaning |
|---|---|
| `unit` | What the values measure: a string or a structured unit. Was `sample_units`. |
| `transforms` | An ordered list mapping stored values to the quantity, applied first to last. Was `value_transforms`. |

The transform types are 1.2's, unchanged: `linear` (`slope`, `intercept`), `lut` (`values`,
`first_value`) and `axis_linear` (`axis`, `slope`, `intercept`: a linear mapping whose
parameters vary along one dimension, such as a per-frame decay correction).

**`transforms: []` states that the stored values are the quantity. An absent `transforms`
states nothing.** In 2.0 this is the only rule; 1.x files keep their own (§12).

---

## 7. `world.transforms`

How this world relates to other frames: registrations, template spaces, other timelines. Every
such relation lives here, including those an extension defines (qualified by its name), so one
fact has one place.

```json
"transforms": [
  { "to": { "name": "nifti:mni152" }, "on": ["x", "y", "z"],
    "forward": { "affine": [[1, 0, 0, -2.5], [0, 1, 0, 4.0], [0, 0, 1, 1.2]] } },
  { "to": { "name": "stimulus" }, "on": ["t"],
    "forward": { "affine": [[1.0, -12.5]] } }
]
```

| Field | Meaning |
|---|---|
| `to` | The target frame: `{ "name": … }`, optionally with `axes` describing the target's axes as world-axis objects (§3.1). |
| `on` | The names of this world's axes the transform acts on. Absent: all of them. |
| `forward` | A transform object mapping this world's coordinates (on `on`) to the target's. |
| `inverse` | A transform object mapping the target's coordinates back. |

At least one of `forward` and `inverse` is present. The source is always this world. A transform
object has exactly one key naming its type:

| Type | Form |
|---|---|
| `identity` | `true` |
| `affine` | rows of an m × (n+1) matrix: n = the axes it acts on, m = the target's axes |
| `sequence`, `displacements` | reserved, with OME-Zarr 0.6's meaning; not defined in 2.0 |

The type names and the row-form affine are OME-Zarr's, so a transform exports as a copy.
Group-level transforms relating one member array's world to another's are reserved, with a
`{ "path": … }` reference as 1.1's transform specification reserved it.

A transform only relates coordinates. `origin` and `step` place the array; `frame` gives the
basis of vector components; neither is a transform here.

---

## 8. Consistency rules

1. `origin`, every `step`, every `samples[i].origin` and every vector in `samples[i].steps` have
   one component per world axis.
2. A dimension has at most one of `step` and `components`; `centering` and `thickness` belong
   to a dimension with a `step`; `color_space` and `frame` to one with `components`.
3. The steps are linearly independent.
4. A `components` kind with a fixed size matches the dimension's length in `shape`.
5. World axis names are unique. A world uses at most one term of each `positive` pair.
6. `frame` is square with one row and column per spatial world axis.
7. A `color_space` fits its kind (§5.3).
8. A transform's `on` names axes of this world; its affine has one column per such axis plus
   one, and one row per target axis.
9. `lut` is non-empty and first in `values.transforms`; `axis_linear` names an existing
   dimension and has one slope and intercept per position along it.
10. `samples`, where present, has one entry per position along its dimension.

## 9. What a reader derives

- **The index-to-world affine**: its columns are the steps, its last column the origin.
- **The grid's extent**, from `centering`: a cell grid extends half a step beyond the outer
  samples, a node grid ends at them.
- **LPS or RAS, and handedness**, from `positive`, through one function per language (Python in
  duckn, JS in `duckn-spatial`): the permutation and signs taking this world's spatial axes to
  LPS. A world whose spatial axes lack `positive` has no orientation, and the function says so.
- **What each axis and dimension is**: an axis from its unit (§3.1), a dimension from its step.

---

## 10. Groups

As in 1.2: a Zarr group may carry `duckn` with `version`, `intent` and `extensions` only, and
every member array is a complete duckn array on its own. A group-level `world` shared by its
members is reserved, not defined.

---

## 11. Examples

### 11.1 An oblique CT

```json
"duckn": {
  "version": "2.0",
  "world": {
    "name": "dicom:1.2.840.113619.2.55.3.604688119.969.1069843699.84",
    "axes": [
      { "name": "x", "unit": "mm", "positive": "left" },
      { "name": "y", "unit": "mm", "positive": "posterior" },
      { "name": "z", "unit": "mm", "positive": "superior" }
    ]
  },
  "origin": [-250.0, -250.0, -400.0],
  "dimensions": [
    { "step": [0, 0.26, 2.49], "centering": "cell", "thickness": 2.5 },
    { "step": [0, 0.98, 0], "centering": "cell" },
    { "step": [0.98, 0, 0], "centering": "cell" }
  ],
  "values": { "unit": "HU", "transforms": [] }
}
```

### 11.2 fMRI

```json
"world": {
  "axes": [
    { "name": "x", "unit": "mm", "positive": "left" },
    { "name": "y", "unit": "mm", "positive": "posterior" },
    { "name": "z", "unit": "mm", "positive": "superior" },
    { "name": "t", "unit": "s" }
  ]
},
"origin": [-96.0, -120.0, -60.0, 0.0],
"dimensions": [
  { "step": [0, 0, 0, 2.0], "centering": "cell" },
  { "step": [0, 0, 3.0, 0], "centering": "cell" },
  { "step": [0, 3.0, 0, 0], "centering": "cell" },
  { "step": [3.0, 0, 0, 0], "centering": "cell" }
]
```

### 11.3 An RGB image

```json
"world": { "axes": [ { "unit": "µm" }, { "unit": "µm" } ] },
"origin": [0.0, 0.0],
"dimensions": [
  { "step": [0, 0.25], "centering": "cell" },
  { "step": [0.25, 0], "centering": "cell" },
  { "components": "RGB-color", "color_space": "srgb" }
],
"values": { "transforms": [] }
```

### 11.4 A diffusion tensor field

```json
"dimensions": [
  { "step": [0, 0, 2.0], "centering": "cell" },
  { "step": [0, 2.0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0], "centering": "cell" },
  { "components": "3D-symmetric-matrix", "frame": [[1, 0, 0], [0, 1, 0], [0, 0, 1]] }
],
"intent": "diffusion-tensor"
```

### 11.5 A diffusion-weighted series

```json
"dimensions": [
  { "step": [0, 0, 2.0], "centering": "cell" },
  { "step": [0, 2.0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0], "centering": "cell" },
  { "components": "list", "extensions": { "dwmri": { "version": "…" } } }
]
```

The gradients stay in the `dwmri` extension; its `gradient_frame: "measurement"` becomes
`"frame"`, naming the list dimension's `frame`.

### 11.6 A registration and a timeline

```json
"world": {
  "axes": [
    { "name": "x", "unit": "mm", "positive": "right" },
    { "name": "y", "unit": "mm", "positive": "anterior" },
    { "name": "z", "unit": "mm", "positive": "superior" },
    { "name": "t", "unit": "s" }
  ],
  "transforms": [
    { "to": { "name": "nifti:mni152" }, "on": ["x", "y", "z"],
      "forward": { "affine": [[1.02, 0, 0, -2.5], [0, 0.98, 0, 4.0], [0, 0, 1.01, 1.2]] } },
    { "to": { "name": "stimulus" }, "on": ["t"], "forward": { "affine": [[1.0, -12.5]] } }
  ]
}
```

### 11.7 The minimum

```json
"duckn": { "version": "2.0", "dimensions": [ { "components": "list" } ] }
```

A valid file. It says the one dimension holds a list, and nothing else.

---

## 12. Reading 1.x files

A 2.0 reader maps every 1.x file in one function; writers write 2.0.

| 1.x | 2.0 |
|---|---|
| `space: "left-posterior-superior"` (or `LPS`) | `world.axes` `left`, `posterior`, `superior` |
| `right-anterior-superior`, `left-anterior-superior` (RAS, LAS) | the matching terms |
| a `-time` space | the three, plus a time axis `{ "name": "t" }` |
| `scanner-xyz`, `3D-right-handed`, `3D-left-handed`, the general 3D names | three axes without `positive` |
| `space_dimension: n` | n axes without `positive` |
| a spatial axis's `unit` | the world axes' `unit` (a file whose spatial axes disagree is refused) |
| `space_origin` | `origin` |
| `axes` | `dimensions` |
| `space_direction` | `step` |
| `kind: "domain" / "space" / "time"` with a direction | implied by the step |
| `kind: "time"` with a `unit` and no direction | a time axis added to the world with that unit; the dimension steps along it by its uniform interval, or by 1 with each sample's `position` |
| `kind` of a range kind | `components`, the same word |
| `measurement_frame` | `frame` on each spatial component dimension |
| `samples[i].directions` | `samples[i].steps` |
| `sample_units` | `values.unit` |
| `value_transforms` (absent in a 1.0/1.1 file) | `values.transforms: []` (1.0/1.1 meant identity) |
| `value_transforms` (absent in a 1.2 file) | absent (1.2 meant "not stated") |
| `space_transforms` from `world` | `world.transforms` |
| `space_transforms` from `index`, `axis-aligned`, `axis-aligned-centered` | composed with the placement into a transform from this world (§15) |
| an extension's own `space_transforms` | `world.transforms`, the target qualified by the extension's name |
| `centering`, `thickness`, `color_space`, `intent`, `unit_systems`, `extensions` | unchanged |

## 13. Correspondence

### 13.1 NRRD

| NRRD | 2.0 |
|---|---|
| `dimension`, `sizes`, `type`, `encoding`, `endian` | Zarr's `shape`, `data_type`, `codecs` |
| `space` | `world.axes[].positive` |
| `space dimension` | the number of `world.axes` |
| `space units` | `world.axes[].unit` |
| `space origin` | `origin` |
| `space directions` | `dimensions[].step` |
| `measurement frame` | `dimensions[].frame` (transposed: NRRD writes columns) |
| `kinds`: domain kinds | a `step` |
| `kinds`: range kinds | `components` |
| `centers` | `centering` |
| `thicknesses` | `thickness` |
| `spacings`, `units` of a domain axis with no `space` | a world axis with that unit, and a step of that spacing along it |
| `labels` | Zarr's `dimension_names` |
| `old min`, `old max`, `content`, `axis mins`, `axis maxs` | the `nrrd` extension |
| key/value pairs | the `keyvalues` extension |

### 13.2 OME-Zarr 0.6

| duckn 2.0 | OME-Zarr 0.6 |
|---|---|
| `world` | a coordinate system `{name, axes}`; `world.name`, or `"world"`, names it |
| a world axis's `name`, `unit` | an axis's `name`, `unit` (UDUNITS-2 spelling on export: `mm` is `millimeter`) |
| what an axis measures (from its unit) | axis `type`: `space`, `time` |
| `positive: "left"` | nothing in 0.6; RFC-4's `orientation: {type: "anatomical", value: "right-to-left"}` in OME 1.0 |
| `origin` and axis-aligned steps | a level's `scale` and `translation` |
| oblique steps | an array-aligned coordinate system (scale, translation), then an `affine` to the world's |
| `world.transforms` | `coordinateTransformations` from this system to the target; an `on` subset is a `byDimension` |
| a `components` dimension | an axis of `type: "channel"`, discrete, in the same coordinate system |
| `values`, `centering`, `components` meaning, `frame` | nothing: these stay duckn's |

OME metadata sits on an image group, duckn on each array. Both may be written: `duckn`
authoritative, the `ome` block derived from it (as NIfTI-Zarr makes NIfTI authoritative).

## 14. Why these names

- **`dimensions`, not `axes`.** NRRD used "axis" for both an array dimension and a world
  direction. Zarr calls the first a dimension, geometry and OME-Zarr call the second an axis.
- **`world`.** FITS's World Coordinate System, VTK's and ITK's world coordinates, Slicer's
  IJK-to-RAS, and 1.x's own built-in `world` space all use it for exactly this: the continuous
  coordinates indices map to, including time and frequency. Rejected: `space` (position only,
  and 1.x's `kind: "space"`), `frame` (collides with time frames and DICOM multi-frame),
  `coordinate_system` (long, and OME's plural means several named systems), `physical`.
- **`step`, not `space_direction`.** 1.x had to warn that its directions "are not unit
  vectors"; in ITK, DICOM and MINC a "direction" is one. A step is the displacement of one index
  increment.
- **`centering`, kept.** The standard term (cell-centered, node-centered grids) for grid
  geometry. `sampling` was rejected: it names acquisition or rate, and `thickness` already says
  what a measurement integrated over.
- **`positive` with one end named.** It says where values grow; naming both ends (`"right-to-
  left"`, `{ "from", "to" }`) reads as a span across the origin, and with three unambiguous pairs
  the second end carries nothing. RFC-4's joined form is written only on export.
- **`components` with NRRD's words.** The range kinds are precise and familiar; only the domain
  kinds were replaced, by a step.
- **`values`, and `transforms` inside what they transform.** `values.transforms` and
  `world.transforms` are distinguished by the object they sit in, as a prefix did before.

**Considered and not adopted (2026-09-26/27):**

- A richer world: a `reference` or `relative_to` vocabulary, display and geographic frames,
  quadruped and part-relative terms, stated handedness, per-axis `label`, `quantity`, `period`,
  a time anchor (`epoch`). None has a writer; each can be added later as an optional field or
  term, and an unknown term reads as "unknown".
- A `negative` field: determined by `positive` for these pairs, and a second statement of one
  fact.
- An absolute time anchor: analyses use relative time; an acquisition date in the geometry sits
  where de-identification tools do not look, and DICOM times often lack a zone.
- OME-Zarr's shape inside duckn, or NRRD fields and structured axes side by side: two statements
  of one fact. The OME view is derived.
- Keeping NRRD's field names in 2.0: the words were the confusion; the principles are kept.

## 15. Open questions

1. **Transforms from derived spaces.** 1.1 allows a transform from `index`, `axis-aligned` or
   `axis-aligned-centered`. 2.0 drops them as sources: each is derived from the placement, so a
   1.x transform from one composes into a transform from this world without loss. Unless a
   workflow needs a transform *stated* relative to the axis-aligned space, which would keep it.
2. **`components: "list"` for a series of separate measurements** (diffusion volumes,
   segmentation layers), or a word of its own.
3. **An axis's quantity from its unit.** An axis with no unit and no `positive` then says
   nothing about what it measures.
4. **Extension texts.** `dwmri` (`gradient_frame`), `seg` (its `list`-axis rules become the
   `list` components dimension), `microscopy`, `fits`, `nifti`, `dicom`, `nrrd`, `units` and
   the transform spec each cite 1.x names and need their own 2.0 revision.
