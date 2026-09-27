# duckn convention 2.0 — draft specification

**Status:** draft for review, revision 2, 2026-09-27. Not implemented. Supersedes the two
world-frame proposals of 2026-09-26/27, whose decisions it records in §15. Revision 2 answers
the first adversarial round (§17).

2.0 keeps NRRD's principles and replaces the vocabulary that made them hard to read. It is a
breaking change, made once; a 2.0 reader reads every 1.x file (§13).

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
geometry and OME-Zarr use it. The vocabulary keeps the two apart; the names a file gives its
dimensions and its axes are separate namespaces and may coincide (a dimension named `x` stepping
along the world axis named `x`).

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
| `intent` | §2.2 | What the array represents as a whole. |
| `unit_systems` | unchanged from 1.x | The registry of unit systems used by structured units (units spec). |
| `extensions` | §2.3 | Domain metadata, per extension. |

Every field is optional. **Absent means unknown**: a field is left out rather than given a
default or a sentinel, and a reader never assumes what a file does not state. The array's
`shape`, `data_type` and `dimension_names` are Zarr's, and a reader uses them with this metadata.

### 2.1 `version`

The version of this convention, `"major.minor"`. It is the only version number a reader acts
on. A minor version only adds; a major version breaks. Software that implements the convention
has its own release numbers, which say nothing about a file.

### 2.2 `intent`

What the array represents as a whole. Defined values; others may be used and read as unknown:

| Value | The array holds |
|---|---|
| `"label-map"` | integer labels, one class per value; resampled nearest-neighbor only (§8) |
| `"probability-map"` | per-sample probabilities |
| `"statistical-map"` | a statistical test's output |
| `"diffusion-tensor"` | diffusion tensors, on a matrix `components` dimension |
| `"diffusion-weighted"` | a series of diffusion-weighted measurements, on a `list` dimension; the gradients are the `dwmri` extension's |
| `"signed-distance"` | the signed distance to a surface, in `values.unit`, negative inside |
| `"displacement-field"` | a displacement `d` on a `vector` dimension: the position `p` corresponds to `p + d(p)` |
| `"velocity-field"` | velocities on a `vector` dimension |

### 2.3 `extensions`

Each extension's top-level block carries its `version`. A dimension may carry a block for the
same extension (§5); it has no version of its own and is read under the top-level block's.
Extensions keep their own specifications, each revised for 2.0 separately (§16).

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
| `name` | The frame's identity, when known: arrays whose worlds have the same `name` share one set of physical coordinates. Extension-qualified (`"dicom:<FrameOfReferenceUID>"`, `"nifti:mni152"`) or ad hoc (no colon). An extension defines the names under its prefix. |
| `transforms` | How this world relates to other frames (§7). |

**`name` stays one string with a colon** (the owner's decision, 2026-09-27). A name is an
identifier and must work as a key (in a JSON object, a lookup table, a URL), which an object
cannot. Its one parse, the extension prefix, happens in one resolver.

### 3.1 A world axis

| Field | Meaning |
|---|---|
| `name` | A short identifier, unique among this world's axes (`"x"`, `"t"`). Transforms address axes by it (§7): an axis a transform acts on must have one. |
| `unit` | The unit of this coordinate. Every `origin`, `step` and per-sample component along this axis is in it. A string is a UCUM code (case-sensitive: `mm`, `um`, `s`, `ms`, `Hz`, `[ppm]`); a structured unit follows the units spec. |
| `positive` | For a spatial axis: the direction in which coordinates increase. One of `left`, `right`, `anterior`, `posterior`, `superior`, `inferior`. Absent: unknown. |

**What an axis measures follows from its unit.** An axis whose unit is a UCUM length is spatial;
a UCUM time, temporal; any other unit (a frequency, `[ppm]` for a chemical shift) makes it some
other continuous coordinate. An axis with `positive` is spatial. An axis with neither, or with a
unit string that is not UCUM, measures something unknown.

**The `positive` terms** are relative to the body in the standard anatomical position
(Terminologia Anatomica 2's notes on *anterior/posterior* and *superior/inferior*), the frame of
DICOM's patient-based coordinates and of NIfTI's RAS. They form three pairs — left/right,
anterior/posterior, superior/inferior — and a world uses at most one term of each pair. A term
names where values *grow*, not a span from one end to the other: `"positive": "left"` means
coordinates increase toward the patient's left, as CF's `positive: "up"` and ISO 19111's axis
direction state it. The opposite end is the pair's other term.

**Handedness.** When exactly three axes carry `positive`, one from each pair, map each term to
its LPS unit vector (left +x, right −x, posterior +y, anterior −y, superior +z, inferior −z) in
axis order: the determinant of the matrix whose columns are those vectors is +1 (right-handed)
or −1 (left-handed). Otherwise the handedness is not stated. It is never written.

**A time axis** increases toward later. Its zero is the writer's: the origin's time component
gives the first sample's time on it (§4).

### 3.2 Examples of worlds

| World | `axes` |
|---|---|
| DICOM patient coordinates (LPS) | `left`, `posterior`, `superior`, each in `mm` |
| NIfTI (RAS) | `right`, `anterior`, `superior`, each in `mm` |
| A time series in LPS | the three above, plus `{ "name": "t", "unit": "s" }` |
| A microscope stage, orientation unknown | three axes in `um`, no `positive` |
| An MR spectroscopic image | three spatial axes plus `{ "name": "delta", "unit": "[ppm]" }` |
| A 2D slide | two axes in `um` |

---

## 4. `origin`

The world position of the first sample (index 0 on every dimension), one component per world
axis. For a time axis it is the first sample's time; a writer with no other zero measures time
from the start of the acquisition. The origin is a point, not a property of the world: arrays
that share a world have their own origins.

The position does not depend on `centering` (§5.1): the origin is where the first sample *is*.

**Without an origin**, the steps still give the geometry relative to the first sample (spacing,
direction, extent), but not where the array is in the world.

---

## 5. `dimensions`

One object per array dimension, in the order of `shape` and `dimension_names`. A dimension's
name is Zarr's `dimension_names` entry and is not repeated here. Elsewhere in this metadata a
dimension is referred to by its index in this list (§6).

Each dimension has at most one of `step` (§5.1) and `components` (§5.2). A dimension with
neither states nothing about what moving along it means.

### 5.1 A dimension that moves through the world

```json
{ "step": [0, 0.26, 2.49], "centering": "cell", "thickness": 3.0 }
```

| Field | Meaning |
|---|---|
| `step` | The world displacement of one increment of this dimension's index: direction and spacing together, one component per world axis. Not a unit vector. |
| `centering` | What a sample stands for (below). Absent: unknown. |
| `thickness` | The extent of what was measured to produce each sample, centered on its position (below). |
| `samples` | Per-sample variation the uniform model cannot state (§5.4). |
| `extensions` | Per-dimension extension metadata (§2.3). |

**`centering`** says what a sample stands for, and so where the grid ends. It never changes a
sample's position.

- `"cell"`: each sample stands for a cell, the interval of one step centered on its position. The
  grid extends half a step beyond the first and last samples. With irregular positions (§5.4)
  the cells meet halfway between neighbors, and the outer ones extend as far past their samples
  as they reach inward. An acquired image's pixel or voxel is a cell; so is a time frame that
  integrated over its interval, whose position is the interval's middle.
- `"node"`: each sample stands for its position itself: a node of a grid, an instant in time.
  The grid ends at the first and last samples. A field sampled at grid points is node-centered,
  and so is a series whose samples are instants (fMRI volumes timed by their acquisition).

It applies along the step as a whole, so on a step that moves through space and time at once
(§5.1, below) a cell also spans time; `thickness`, not the cell, states what was measured.

**`thickness`** is centered on the sample's position. For a spatial dimension it is measured
perpendicular to the steps of the array's other spatial dimensions: a slice's thickness normal
to the slice plane, as DICOM states it, even on a sheared grid. For any other dimension it is
measured along the step: a time frame's duration. It is distinct from the spacing, which is the
step's length.

A step's non-zero components say what the dimension moves through: a dimension stepping only
along spatial axes is spatial, one stepping along a time axis is temporal, and a step may move
through space and time at once (fMRI slice timing: each slice is 3 mm higher and 0.055 s
later).

The steps of the dimensions that have one are **linearly independent**: two indices never land
on one position. There may be fewer of them than world axes (a single 2D slice in a 3D world).

**A dimension of length 1** may have a step. With no second sample its length is not a spacing:
it states only a direction, usually carrying `thickness`. A single slice with a known thickness
is written this way, with a length-1 dimension along its normal.

### 5.2 A dimension that holds the parts of one value

```json
{ "components": "RGB-color", "color_space": "srgb" }
```

| Field | Meaning |
|---|---|
| `components` | What the values along this dimension are, from the table below. |
| `color_space` | For `RGB-color`, `RGBA-color` and `XYZ-color`: what the components mean (§5.3). |
| `frame` | For the spatial kinds: the basis the components are written in (§5.3). |
| `samples` | Per-sample metadata (§5.4). |
| `extensions` | Per-dimension extension metadata (§2.3). |

The vocabulary is NRRD's range kinds, word for word, so a NRRD file's `kinds` map without a
table. Where a size is given, the dimension's length in `shape` must equal it.

| `components` | Size | Spatial | The values along the dimension are |
|---|---|---|---|
| `"list"` | any | | a list of values with no further structure (the volumes of a diffusion series, the layers of a segmentation, fluorescence channels) |
| `"point"` | world | yes | the coordinates of a point |
| `"vector"` | world | yes | a contravariant vector: a displacement, a velocity |
| `"covariant-vector"` | world | yes | a covariant vector: a gradient |
| `"normal"` | world | yes | a unit-length covariant vector |
| `"stub"` | 1 | | a single-sample placeholder |
| `"scalar"` | 1 | | one scalar, stated explicitly |
| `"complex"` | 2 | | real, imaginary |
| `"2-vector"` | 2 | | any 2-vector, not a spatial one |
| `"3-color"` | 3 | | a generic 3-component color |
| `"RGB-color"` | 3 | | red, green, blue |
| `"HSV-color"` | 3 | | hue, saturation, value |
| `"XYZ-color"` | 3 | | CIE XYZ |
| `"4-color"` | 4 | | a generic 4-component color |
| `"RGBA-color"` | 4 | | red, green, blue, alpha (alpha straight, not premultiplied) |
| `"3-vector"` | 3 | | any 3-vector, not a spatial one |
| `"3-gradient"` | 3 | yes | a covariant 3-vector |
| `"3-normal"` | 3 | yes | a unit-length covariant 3-vector |
| `"4-vector"` | 4 | | any 4-vector, not a spatial one |
| `"quaternion"` | 4 | | w, x, y, z; w real, no normalization assumed |
| `"2D-symmetric-matrix"` | 3 | yes | Mxx Mxy Myy |
| `"2D-masked-symmetric-matrix"` | 4 | yes | mask Mxx Mxy Myy |
| `"2D-matrix"` | 4 | yes | Mxx Mxy Myx Myy |
| `"2D-masked-matrix"` | 5 | yes | mask Mxx Mxy Myx Myy |
| `"3D-symmetric-matrix"` | 6 | yes | Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-masked-symmetric-matrix"` | 7 | yes | mask Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-matrix"` | 9 | yes | Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |
| `"3D-masked-matrix"` | 10 | yes | mask Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |

"world" size: the number of spatial world axes. A spatial vector (a displacement, a velocity) is
a `vector`; `2-vector`, `3-vector` and `4-vector` are for triples that are not coordinates in
space. NRRD's three *domain* kinds (`domain`, `space`, `time`) are not components: a dimension
that samples a continuous coordinate has a `step` (§5.1).

### 5.3 `color_space` and `frame`

**`color_space`** is one of CSS Color 4's predefined spaces: `srgb`, `srgb-linear`,
`display-p3`, `display-p3-linear`, `a98-rgb`, `prophoto-rgb` and `rec2020` for `RGB-color` and
`RGBA-color`; `xyz-d50` and `xyz-d65` for `XYZ-color`. Components run from 0 to 1, and the color
space says how the quantity (the stored values after `values.transforms`, §6) is read as
components: a quantity in an unsigned integer type spans that type's full range (in a `uint8`
array read through `transforms: []`, 255 is 1.0); a floating-point quantity is the components
themselves. A color space is never stated by an ICC profile. Absent: unknown.

**`frame`** gives the basis a spatial kind's components are written in: a square matrix with
one row and one column per spatial world axis, in their order. It is 1.x's `measurement_frame`,
moved onto the dimension it describes. With F the frame, the components in the world's spatial
axes are:

| Kinds | In world axes |
|---|---|
| `point`, `vector` | F · v |
| `covariant-vector`, `normal`, `3-gradient`, `3-normal` | F⁻ᵀ · v |
| the matrix kinds | F · M · Fᵀ (the mask, where present, is unchanged) |

For a rotation, F⁻ᵀ = F. Only the spatial kinds take a `frame`, and only when their spatial size
equals the world's number of spatial axes. **Absent: the basis is unknown.** A writer whose
components are already in the world's axes writes the identity matrix, stating it.

### 5.4 `samples`

An array with one entry per position along the dimension, for what the uniform model cannot
state. Its length equals the dimension's length. Every field is optional; `{}` means "the
uniform defaults".

| Field | Meaning |
|---|---|
| `position` | Where this sample sits along the dimension, as a multiple of the step: the sample is at `position · step` from the origin instead of `index · step`. Real-valued, and may be negative (irregular slice spacing; irregular frame times, with a step of one time unit). |
| `origin` | This sample's world position along this dimension: it replaces `origin + index · step` for this dimension, and the other dimensions' `index · step` still add (per-slice positions that no uniform step fits). |
| `steps` | At this sample, the step of every dimension, in dimension order (`null` for one without a step): non-parallel slices. Was `directions`. |
| `thickness` | This sample's thickness (§5.1). |
| `metadata` | Open per-sample metadata, keyed by application or standard (a DICOM slice's tags). |

A sample has at most one of `position` and `origin`. A position with a time component (a slice
time, a frame time) is written in `position` on a temporal dimension, or as a time component of
`origin` or of the step on a space-time one.

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
| `unit` | What the values measure: a string or a structured unit. Was `sample_units`. A string is shown as written (`HU`); UCUM codes are recommended, and `[arb'U]` states arbitrary units. |
| `transforms` | An ordered list mapping stored values to the quantity, applied first to last. Was `value_transforms`. |

**`transforms: []` states that the stored values are the quantity. An absent `transforms`
states nothing.** In 2.0 this is the only rule; 1.x files keep their own (§13).

The transform types are 1.2's, with one parameter renamed:

| Type | Parameters | Quantity |
|---|---|---|
| `linear` | `slope`, `intercept` | stored · slope + intercept |
| `lut` | `values`, `first_value` (default 0) | `values[stored − first_value]`, clamped to the table |
| `axis_linear` | `dimension` (an index into `dimensions`), `slope`, `intercept` | stored · slope[i] + intercept[i] at position i along that dimension |

In `axis_linear`, `slope` and `intercept` are each a number (the same at every position) or a
list with one entry per position along the dimension (a per-frame decay correction).

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
| `to` | The target frame: `{ "name": … }`, with `axes` describing the target's axes as world-axis objects (§3.1) unless the name's extension defines them. |
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

## 8. Resampling

- Only a dimension with a `step` may be interpolated. A `components` dimension never is:
  interpolating across it mixes different quantities. A dimension that states neither is not.
- Values are interpolated as quantities. An affine transform commutes with interpolation, so a
  reader may interpolate stored values under `linear`; under `lut`, or `axis_linear` along the
  dimension being interpolated, it applies the transform first.
- A `label-map` (§2.2), and the binary labelmaps of the `seg` extension, are resampled
  nearest-neighbor only.
- Vectors and tensors are interpolated component by component in one basis; a reader brings them
  into the world's axes (§5.3) before resampling onto a grid with other directions.

## 9. Consistency rules

1. `origin`, every `step`, every `samples[i].origin` and every vector in `samples[i].steps` have
   one component per world axis.
2. A dimension has at most one of `step` and `components`; `centering` and `thickness` belong
   to a dimension with a `step`; `color_space` and `frame` to one with `components`.
3. The steps are linearly independent.
4. A `components` kind's size matches the dimension's length in `shape`.
5. World axis names are unique. A world uses at most one term of each `positive` pair.
6. `frame` is square, one row and column per spatial world axis, on a spatial kind (§5.3).
7. A `color_space` fits its kind (§5.3).
8. A transform's `on` names axes of this world; its affine has one column per such axis plus
   one, and one row per target axis.
9. `lut` is non-empty and first in `values.transforms`; `axis_linear` names an existing
   dimension, and each list-valued parameter has one entry per position along it.
10. `samples`, where present, has one entry per position along its dimension; no entry has both
    `position` and `origin`.

**A file that breaks a rule is invalid.** A reader refuses its duckn metadata rather than guess
which part to trust, and may still give access to the Zarr array's raw values.

## 10. What a reader derives

- **The index-to-world affine**: its columns are the steps, its last column the origin.
- **The grid's extent**, from `centering` (§5.1).
- **LPS or RAS, and handedness**, from `positive` (§3.1), through one function per language
  (Python in duckn, JS in `duckn-spatial`): the permutation and signs taking this world's
  spatial axes to LPS. A world whose spatial axes lack `positive` has no orientation, and the
  function says so.
- **What each axis and dimension is**: an axis from its unit (§3.1), a dimension from its step.

---

## 11. Groups

As in 1.2: a Zarr group may carry `duckn` with `version`, `intent` and `extensions` only, and
every member array is a complete duckn array on its own. A group-level `world` shared by its
members is reserved, not defined.

---

## 12. Examples

### 12.1 An oblique CT

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
    { "step": [0, 0.26, 2.49], "centering": "cell", "thickness": 3.0 },
    { "step": [0, 0.98, 0], "centering": "cell" },
    { "step": [0.98, 0, 0], "centering": "cell" }
  ],
  "values": { "unit": "HU", "transforms": [ { "name": "linear", "parameters": { "slope": 1, "intercept": -1024 } } ] }
}
```

### 12.2 fMRI with slice timing

Volumes are instants (node); each slice is 3 mm higher and 0.055 s later than the one below.

```json
"world": {
  "axes": [
    { "name": "x", "unit": "mm", "positive": "right" },
    { "name": "y", "unit": "mm", "positive": "anterior" },
    { "name": "z", "unit": "mm", "positive": "superior" },
    { "name": "t", "unit": "s" }
  ]
},
"origin": [-96.0, -126.0, -72.0, 0.0],
"dimensions": [
  { "step": [0, 0, 0, 2.0], "centering": "node" },
  { "step": [0, 0, 3.0, 0.055], "centering": "cell" },
  { "step": [0, 3.0, 0, 0], "centering": "cell" },
  { "step": [3.0, 0, 0, 0], "centering": "cell" }
],
"values": { "unit": "[arb'U]", "transforms": [] }
```

### 12.3 An RGB slide

No stage origin is known, so `origin` is absent: the steps give the image's own geometry.

```json
"world": { "axes": [ { "name": "x", "unit": "um" }, { "name": "y", "unit": "um" } ] },
"dimensions": [
  { "step": [0, 0.25], "centering": "cell" },
  { "step": [0.25, 0], "centering": "cell" },
  { "components": "RGB-color", "color_space": "srgb" }
],
"values": { "transforms": [] }
```

### 12.4 A single slice with its thickness

A sagittal DICOM slice stored as a 1 × 256 × 256 array; the length-1 dimension carries the
slice normal and thickness.

```json
"origin": [10.0, -120.0, 90.0],
"dimensions": [
  { "step": [1.0, 0, 0], "centering": "cell", "thickness": 5.0 },
  { "step": [0, 0, -1.0], "centering": "cell" },
  { "step": [0, 1.0, 0], "centering": "cell" }
]
```

### 12.5 A diffusion tensor field

```json
"dimensions": [
  { "step": [0, 0, 2.0], "centering": "cell" },
  { "step": [0, 2.0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0], "centering": "cell" },
  { "components": "3D-symmetric-matrix", "frame": [[0.9848, 0, 0.1736], [0, 1, 0], [-0.1736, 0, 0.9848]] }
],
"intent": "diffusion-tensor"
```

### 12.6 Dynamic PET

Frames are cells: each position is its frame's middle, as a multiple of a one-second step, and
`thickness` is its duration. The activity is stored with one slope per frame.

```json
"dimensions": [
  { "step": [0, 0, 0, 1.0], "centering": "cell",
    "samples": [ { "position": 5, "thickness": 10 }, { "position": 15, "thickness": 10 }, { "position": 25, "thickness": 10 } ] },
  { "step": [0, 0, 2.0, 0], "centering": "cell" },
  { "step": [0, 2.0, 0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0, 0], "centering": "cell" }
],
"values": { "unit": "Bq/mL", "transforms": [
  { "name": "axis_linear", "parameters": { "dimension": 0, "slope": [1.10, 1.12, 1.15], "intercept": 0 } } ] }
```

### 12.7 A registration and a timeline

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
    { "to": { "name": "stimulus", "axes": [ { "name": "t", "unit": "s" } ] }, "on": ["t"],
      "forward": { "affine": [[1.0, -12.5]] } }
  ]
}
```

### 12.8 A diffusion-weighted series

```json
"dimensions": [
  { "components": "list" },
  { "step": [0, 0, 2.0], "centering": "cell" },
  { "step": [0, 2.0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0], "centering": "cell" }
],
"intent": "diffusion-weighted",
"extensions": { "dwmri": { "version": "…" } }
```

The b-values and gradients, and the frame the gradients are written in, are the `dwmri`
extension's: they describe the acquisition of each volume, not the components of a value.

### 12.9 The minimum

```json
"duckn": { "version": "2.0", "dimensions": [ { "components": "list" } ] }
```

A valid file. It says the one dimension holds a list, and nothing else.

---

## 13. Reading 1.x files

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
| `kind: "time"` with a `unit` and no direction | a time axis added to the world with that unit; the dimension steps along it by one time unit, with each sample's time as its `position` where given |
| `kind` of a range kind | `components`, the same word |
| `measurement_frame` | `frame` on each spatial component dimension |
| `samples[i].position` (a distance) | `samples[i].position` divided by the step's length |
| `samples[i].directions` | `samples[i].steps` |
| `sample_units` | `values.unit` |
| `value_transforms` (absent in a 1.0/1.1 file) | `values.transforms: []` (1.0/1.1 meant identity) |
| `value_transforms` (absent in a 1.2 file) | absent (1.2 meant "not stated") |
| `axis_linear`'s `axis` | `dimension` |
| `space_transforms` from `world` | `world.transforms` |
| `space_transforms` from `index`, `axis-aligned`, `axis-aligned-centered` | composed with the placement into a transform from this world (§16) |
| an extension's own `space_transforms` | `world.transforms`, the target qualified by the extension's name |
| `centering`, `thickness`, `color_space`, `intent`, `unit_systems`, `extensions` | unchanged |

## 14. Correspondence

### 14.1 NRRD

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

### 14.2 OME-Zarr 0.6

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

## 15. Why these names

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
- **`dimension` in `axis_linear`**, not `axis`: it names an array dimension, by index.

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

## 16. Open questions

1. **Transforms from derived spaces.** 1.1 allows a transform from `index`, `axis-aligned` or
   `axis-aligned-centered`. 2.0 drops them as sources: each is derived from the placement, so a
   1.x transform from one composes into a transform from this world without loss. Unless a
   workflow needs a transform *stated* relative to the axis-aligned space, which would keep it.
2. **`components: "list"` for a series of separate measurements** (diffusion volumes,
   segmentation layers), or a word of its own.
3. **Extension texts.** `dwmri`, `seg` (its `list`-axis rules become the `list` components
   dimension), `microscopy`, `fits`, `nifti`, `dicom`, `nrrd`, `units` and the transform spec
   each cite 1.x names and need their own 2.0 revision.

## 17. Adversarial rounds

**Round 1 (2026-09-27, revision 1).** Fourteen scenarios with known answers (an oblique CT, fMRI
with slice timing, a whole-slide RGB image, a DTI field in a rotated frame, a DWI series, a
label map, a single 2D slice, MR spectroscopy, a registration to MNI, a microscopy time-lapse,
dynamic PET, a node-centered distance field, a gantry-tilted CT, a displacement field). Two
writers wrote every header from the draft alone; two blind readers, given only the draft and the
headers, answered a fixed questionnaire. The writers agreed on steps, origins and frames in 11 of
14 scenarios, and both expressed slice timing as a space-time step and the gantry tilt as a
sheared step unprompted. Every position the readers computed from what a header stated matched
the ground truth. The defects all four found, and revision 2's answer:

| Defect | Revision 2 |
|---|---|
| §5.3 and §6 disagreed on what `transforms: []` means for color | `[]` is always "the stored values are the quantity"; `color_space` reads the quantity as components |
| An example gave a `list` dimension a `frame` §5.3 forbade | gradients and their frame are the `dwmri` extension's (§12.8) |
| An example stated an origin the facts did not give | origin absent; §4 says what steps alone give |
| Whether a time sample's position is its start or its middle; `cell` in time | §5.1: a cell is centered on its position, a node is an instant; frames are written by their middle |
| `samples[i].position` was a "distance" | a real-valued multiple of the step |
| How `samples[i].origin` combines with the other dimensions | it replaces only its own dimension's contribution |
| `thickness` "along the step" was wrong on a sheared grid, and a single slice had nowhere to state it | perpendicular to the other spatial steps; a length-1 dimension carries a single slice's normal |
| `frame` stated only the vector rule | the rules for vectors, covariant vectors and tensors, and which kinds take one |
| No resampling rule | §8 |
| No reader behavior for a broken rule | a file that breaks one is invalid, and its metadata is refused |
| `axis_linear`'s `axis` named a dimension | `dimension`, an index |
| Handedness had no stated rule; units no stated parsing | §3.1: a determinant over the terms; unit strings on world axes are UCUM |
| Arbitrary units, a distance field's sign, a displacement's direction could not be stated | `[arb'U]`; the `signed-distance` and `displacement-field` intents |
