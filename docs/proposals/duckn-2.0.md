# duckn convention 2.0 — draft specification

**Status:** draft for review, revision 5, 2026-09-27. Not implemented. Supersedes the two
world-frame proposals of 2026-09-26/27, whose decisions it records in §16. Revisions 2–4 answer
three adversarial rounds and a study of every extension; revision 5 follows duckn 0.6.1's
converter fixes (§18); the decisions still open are in §19.

2.0 keeps NRRD's principles and replaces the vocabulary that made them hard to read. It is a
breaking change, made once; a 2.0 reader reads every 1.x file (§14).

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
dimensions, the ids of its world axes, and the words of the `components` vocabulary are
separate namespaces and may coincide (a dimension named `x` stepping along the world axis `x`).

Every dimension answers one question, "what happens when I move along it?", in one of three
ways:

- it **moves through the world**, by a vector: its `step`;
- it **holds the parts of one value, or a list of values** (the red, green and blue of a color;
  the components of a vector or tensor; the volumes of a diffusion series): its `components`;
- **nothing is stated**.

The position of a sample is

```
position = origin + Σ index_d · step_d        (over the dimensions d that have a step)
```

where `origin` and every `step` have one component per world axis, and where a dimension's
`samples` may replace its `index_d · step_d` (§5.4). This is NRRD's model (`space origin`,
`space directions`); only the names and the grouping are new.

**The formula places a sample only as far as its dimensions state.** Samples that differ only
along a dimension that states nothing, or along a `components` dimension, have no stated
relation in the world: the formula gives them one point, and that is not a claim that they
coincide (a diffusion series' volumes were not acquired at one instant). A world axis that no
dimension steps along gives every sample the origin's coordinate on it, which is a claim (one
time point, one slice position); a writer includes such an axis only when that is true.

**Identifiers, names and references.** Something that is referred to has an `id`: a stable
key, unique within its scope (a world axis among the world's axes, a sample along its
dimension), never changed by a rename. Wherever a person reads it, it may have a `name`: a
display string in any script. The pair is the segmentation extension's (seg §3.2). An `id` is a
token — one or more of `A`–`Z`, `a`–`z`, `0`–`9`, `_`, `-`, case-sensitive. A **`reference`** is
different: it points to something outside the file that many files share, a frame of reference
(§3.2), so it is not unique to this file and is not an `id`.

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
| `origin` | §4 | The world point at index 0 on every dimension. |
| `dimensions` | §5 | One object per array dimension, in the order of `shape`. |
| `values` | §6 | What the stored numbers mean. |
| `intent` | §2.2 | What the array represents as a whole. |
| `unit_systems` | unchanged from 1.x | The registry of unit systems used by structured units. |
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
| `"label-map"` | integer values that name classes (a value may belong to several classes, as the `seg` extension defines); resampled nearest-neighbor only (§8) |
| `"probability-map"` | per-sample probabilities or fractions (a softmax output, an occupancy) |
| `"statistical-map"` | a statistical test's output |
| `"diffusion-tensor"` | diffusion tensors, on a matrix `components` dimension |
| `"diffusion-weighted"` | a series of diffusion-weighted measurements, on a `list` dimension; the gradients are the `dwmri` extension's |
| `"signed-distance"` | the signed distance to a surface, in `values.unit`, negative inside |
| `"displacement-field"` | a displacement `d` on a `vector` dimension, defined on this array's grid: the point `p` corresponds to `p + d(p)`, in this world's coordinates |
| `"velocity-field"` | velocities on a `vector` dimension |

### 2.3 `extensions`

Each extension's top-level block carries its `version`. A dimension, or a world axis, may carry
a block for the same extension; it has no version of its own, is read under the top-level
block's, and requires that block to be present.

Extensions keep their own specifications, each revised for 2.0 separately (§17). Until an
extension is revised, a 2.0 file may carry its current version, read under its own
specification, with its 1.x names (`axes`, `kind`, `space_direction`) read as their 2.0
counterparts (§14). An extension that depends on the array-level `measurement_frame` (`dwmri`
1.0) cannot: that field has no 2.0 counterpart outside the extension (§17).

**One fact, one home.** An extension states each per-position fact in one place: either in its
dimension-level block or in `samples[i].metadata`, as its specification says, and a reader
looks only there. An extension does not restate what the core states (a frame time, a slice
position, a voxel size).

**References to an extension without its block.** An `intent`, a `world.reference`, a
transform target's `reference` or a transform type that names an extension (`diffusion-weighted`, `dicom:…`,
`fits:…`) does not require that extension's block. Without it the file is incomplete, not
invalid: it says what the array is and leaves the details unstated. An extension block that
breaks its own specification is ignored by readers of that extension, and reported; the core
metadata stays valid.

---

## 3. `world`

```json
"world": {
  "reference": "dicom:1.2.826.0.1.3680043.8.498.10033451",
  "axes": [
    { "id": "x", "unit": "mm", "positive": "left" },
    { "id": "y", "unit": "mm", "positive": "posterior" },
    { "id": "z", "unit": "mm", "positive": "superior" }
  ],
  "transforms": [ ... ]
}
```

| Field | Meaning |
|---|---|
| `axes` | The world's axes, in the order of the components of `origin`, of every `step`, and of `samples[i].origin` and `samples[i].steps`. Its length is the world's dimension. Required when `world` is present. |
| `reference` | The frame of reference this world is measured in, when known (§3.2). |
| `name` | A display name for the frame. |
| `transforms` | How this world relates to other frames (§7). |

### 3.1 A world axis

| Field | Meaning |
|---|---|
| `id` | A token, unique among this world's axes (`"x"`, `"t"`). Transforms address axes by it (§7): an axis a transform acts on must have one. |
| `name` | A display name (`"chemical shift"`). |
| `unit` | The unit of this coordinate: the component along this axis of `origin`, of every `step`, of `samples[i].origin` and `samples[i].steps`, and of `thickness` is in it. (The unit of vector or tensor *values* is `values.unit`, §6.) |
| `positive` | For a spatial axis: the direction in which coordinates increase. One of `left`, `right`, `anterior`, `posterior`, `superior`, `inferior`. Absent: unknown. |
| `extensions` | Per-axis extension metadata (a FITS world axis's WCS keywords), §2.3. |

**Units.** A string is a UCUM code (case-sensitive: `mm`, `um`, `s`, `ms`, `Hz`, `[ppm]`). A
structured unit (units spec) is read through its UCUM code when its `scheme` is UCUM, and is
otherwise unknown unless mapped. A unit that carries a reference time (UDUNITS `hours since
2020-01-01`) is not a world-axis unit: a time axis's zero is its frame's (§3.2). The spatial axes
of one world share one unit.

**What an axis measures follows from its unit.** An axis whose unit is a UCUM length is spatial;
a UCUM time, temporal; any other unit (a frequency, `[ppm]`, an angle in `deg`) makes it some
other continuous coordinate. An axis with `positive` is spatial. An axis with neither, or with a
unit that is not UCUM, measures something unknown. A sky image's right ascension and
declination are angles, not spatial axes: the spatial kinds and `positive` do not apply to them.

**The `positive` terms** are relative to the body in the standard anatomical position
(Terminologia Anatomica 2's notes on *anterior/posterior* and *superior/inferior*), the frame of
DICOM's patient-based coordinates and of NIfTI's RAS. They form three pairs — left/right,
anterior/posterior, superior/inferior — and a world uses at most one term of each pair. A term
names where values *grow*, not a span from one end to the other: `"positive": "left"` means
coordinates increase toward the patient's left, as CF's `positive: "up"` and ISO 19111's axis
direction state it. The opposite end is the pair's other term.

**Handedness.** When exactly three axes carry `positive`, one from each pair, take those three
in axis order, ignoring the others, and map each term to its LPS unit vector (left +x, right −x,
posterior +y, anterior −y, superior +z, inferior −z): the determinant of the matrix whose
columns are those vectors is +1 (right-handed) or −1 (left-handed). Otherwise the handedness is
not stated. It is never written.

**A time axis** increases toward later.

### 3.2 A frame's zero and its identity

Every world axis is measured from a zero that belongs to the **frame**: a patient-based frame's
origin, a scanner's isocenter, a session's time base. `origin` (§4) places this array relative
to those zeros. This is the same in space and in time.

A world's **`reference`** names the frame of reference it is measured in, as DICOM's Frame of
Reference UID does: arrays whose worlds carry the same `reference` are measured in the same
frame, and their coordinates compare directly — positions for a spatial frame, times for a
temporal one. Many arrays share a reference, which is why it is a reference and not an `id`. A
world without one has **local zeros**: its coordinates compare only within the file. A reference
is a token, or an extension's name for a frame, written `<extension>:<token>`
(`dicom:1.2.826.0.1.3680043.8.498.10033451`, `nifti:mni152`), which the extension defines,
including which of the world's axes the frame covers. A DICOM Frame of Reference covers the
spatial axes; a DICOM Synchronization Frame of Reference, the time axis. It stays one string with
a colon (the owner's decision, 2026-09-27): a reference must work as a key — in a JSON object, a lookup
table, a URL — and its one parse, the prefix, happens in one resolver.

**A world whose axes belong to more than one frame** states the others as identity transforms
on the axes they cover (§7):

```json
"world": {
  "reference": "dicom:1.2.826.0.1.3680043.8.498.10033451",
  "axes": [ { "id": "x", ... }, { "id": "y", ... }, { "id": "z", ... }, { "id": "t", "unit": "s" } ],
  "transforms": [
    { "to": { "reference": "dicom-sync:1.2.840.113619.2.55.3.1" }, "on": ["t"], "forward": { "identity": true } }
  ]
}
```

Times shared this way are comparable across arrays without any date in the file: a time base is
an identity, not a moment.

### 3.3 Examples of worlds

| World | `axes` |
|---|---|
| DICOM patient coordinates (LPS) | `left`, `posterior`, `superior`, each in `mm` |
| NIfTI (RAS) | `right`, `anterior`, `superior`, each in `mm` |
| A time series in LPS | the three above, plus `{ "id": "t", "unit": "s" }` |
| A microscope stage, orientation unknown | three axes in `um`, no `positive` |
| An MR spectroscopic image | three spatial axes plus `{ "id": "delta", "unit": "[ppm]", "name": "chemical shift" }` |
| A 2D slide | two axes in `um` |

---

## 4. `origin`

The world point at index 0 on every dimension (position 0, where `samples` places samples by
position, §5.4), one component per world axis, each measured from its frame's zero (§3.2). On a
regular grid that is the first sample; where samples are placed by position it is their common
reference, such as a time zero from which PET frames are placed at their middles.

The origin is a point, not the corner of a cell: it does not depend on `centering` (§5.1).
Arrays that share a frame have their own origins.

**Without an origin**, the steps still give the geometry relative to the first sample (spacing,
direction, extent), but not where the array is in its frame.

---

## 5. `dimensions`

One object per array dimension, in the order of `shape` and `dimension_names`. A dimension's
name is Zarr's `dimension_names` entry and is not repeated here. Elsewhere in this metadata a
dimension is referred to by its index in this list (§6).

Each dimension has at most one of `step` (§5.1) and `components` (§5.2). A dimension with
neither states nothing about what moving along it means, and may carry only `samples` (with
`id`, `name` and `metadata`) and `extensions`.

### 5.1 A dimension that moves through the world

```json
{ "step": [0, 0.26, 2.49], "centering": "cell", "thickness": 3.0 }
```

| Field | Meaning |
|---|---|
| `step` | The world displacement of one increment of this dimension's index: direction and spacing together, one component per world axis. Not a unit vector. |
| `centering` | What a sample stands for in the grid (below). Absent: unknown. |
| `thickness` | The extent of what was measured to produce each sample, in world units, centered on its position (below). |
| `samples` | Per-sample variation the uniform model cannot state (§5.4). |
| `extensions` | Per-dimension extension metadata (§2.3). |

A step's non-zero components say what the dimension moves through: a dimension stepping only
along spatial axes is spatial; along a time axis, temporal; along another continuous axis (a
chemical shift), a dimension of that coordinate. A step may move through space and time at once
(fMRI slice timing: each slice is 3 mm higher and 0.055 s later). The **spacing** of a
dimension is the length of its step's spatial part for a spatial dimension, and the step's
component along its axis otherwise; on a sheared grid the spacing is along the step, and the
separation perpendicular to the other steps is derived.

The steps of the dimensions that have one are **linearly independent**: two indices never land
on one position. There may be fewer of them than world axes (a single 2D slice in a 3D world).

**Two extents.** A grid has two different extents, and a reader keeps them apart:

- **The grid's cells**, from `centering`, partition the grid: they are what resampling and
  bounding boxes use.
  - `"cell"`: each sample stands for the interval of one step centered on its position, so the
    grid extends half a step beyond the first and last samples. With irregular positions (§5.4)
    the cells meet halfway between neighbors, and the outer ones extend as far past their
    samples as they reach inward. An acquired image's pixel or voxel is a cell; so is a time
    frame that integrated over its interval.
  - `"node"`: each sample stands for its position itself: a node of a grid, an instant in time.
    The grid ends at the first and last samples. A field sampled at grid points is
    node-centered, and so is a series whose samples are instants.
- **What each sample measured**, from `thickness`: an interval centered on the sample's position.
  For a spatial dimension it is measured perpendicular to the steps of the array's other spatial
  dimensions (a slice's thickness normal to the slice plane, as DICOM states it, even on a
  sheared grid); for any other dimension, along its step (a time frame's duration).

The two differ whenever the thickness is not the spacing: overlapping CT slices, gaps between
irregular slices, PET frames of unequal length. A frame's time interval is its thickness
interval; its cell is the grid's.

On a step that moves through space and time at once, `centering` describes the step's spatial
part; its time part places each sample at an instant (each fMRI slice is a spatial cell acquired
at one moment).

**A dimension of length 1** may have a step. With no second sample its length is not a spacing,
and it has no cell: the step states only a direction (either sense, any non-zero length), a
writer does not write `centering` on it, and `thickness` alone gives the extent along it. A
single slice with a known thickness is written this way, with a length-1 dimension along its
normal.

### 5.2 A dimension that holds components

```json
{ "components": "RGB-color", "color_space": "srgb" }
```

| Field | Meaning |
|---|---|
| `components` | What the values along this dimension are, from the table below. |
| `color_space` | For `RGB-color`, `RGBA-color` and `XYZ-color`: what the components mean (§5.3). |
| `frame` | For the spatial kinds: the basis the components are written in (§5.3). |
| `samples` | Per-sample `id`, `name` and `metadata` (§5.4). |
| `extensions` | Per-dimension extension metadata (§2.3). |

The vocabulary is NRRD's range kinds, word for word, with NRRD's sizes, so a NRRD file's `kinds`
map without a table. Where a size is given, the dimension's length in `shape` must equal it.

| `components` | Size | Spatial | The values along the dimension are |
|---|---|---|---|
| `"list"` | any | | a list of values with no further structure: the volumes of a diffusion series, the layers of a segmentation, fluorescence channels |
| `"point"` | any | when its size is the world's spatial count | the coordinates of a point |
| `"vector"` | any | when its size is the world's spatial count | a contravariant vector: a displacement, a velocity |
| `"covariant-vector"` | any | when its size is the world's spatial count | a covariant vector: a gradient |
| `"normal"` | any | when its size is the world's spatial count | a unit-length covariant vector |
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

A spatial vector (a displacement, a velocity) is a `vector` of the world's spatial size; where
both `vector`/`covariant-vector` and a sized kind (`3-gradient`) fit, a writer uses the first.
A `vector` of another size (an ITK diffusion series stored as a vector of volumes) is not
spatial and takes no `frame`. NRRD's three *domain* kinds (`domain`, `space`, `time`) are not
components: a dimension that samples a continuous coordinate has a `step` (§5.1).

### 5.3 `color_space` and `frame`

**`color_space`** is one of CSS Color 4's predefined spaces: `srgb`, `srgb-linear`,
`display-p3`, `display-p3-linear`, `a98-rgb`, `prophoto-rgb` and `rec2020` for `RGB-color` and
`RGBA-color`; `xyz-d50` and `xyz-d65` for `XYZ-color`. Components run from 0 to 1, and the color
space says how the quantity (the stored values after `values.transforms`, §6) is read as
components: a quantity in an unsigned integer type spans that type's full range (in a `uint8`
array read through `transforms: []`, 255 is 1.0); a floating-point quantity is the components
themselves. A color space is never stated by an ICC profile. Absent: unknown. A reader may
interpolate a color space's encoded components as they are (most do), or convert to its linear
form first for accuracy.

**`frame`** gives the basis a spatial kind's components are written in: a square matrix,
written as a list of rows (as every matrix in duckn is), with one row and one column per spatial
world axis. Column c is the c-th basis vector of the components, in world axes. It is 1.x's
`measurement_frame`, moved onto the dimension it describes; an extension whose own vectors need
a basis (the `dwmri` gradients) uses the same definition. With F the frame, the components in
the world's spatial axes are:

| Kinds | In world axes |
|---|---|
| `point`, `vector` | F · v |
| `covariant-vector`, `normal`, `3-gradient`, `3-normal` | F⁻ᵀ · v (a `normal` renormalized) |
| the matrix kinds, read as contravariant tensors | F · M · Fᵀ (the mask, where present, is unchanged) |

For a rotation, F⁻ᵀ = F. F need not be a rotation, but it must be invertible. **Absent: the basis
is unknown.** A writer whose components are already in the world's axes writes the identity
matrix, stating it.

### 5.4 `samples`

An array with one entry per position along the dimension, for what the uniform model cannot
state. Its length equals the dimension's length. Every field is optional; `{}` means "the
uniform defaults".

| Field | Meaning |
|---|---|
| `id` | A token naming this position, unique along the dimension: a channel (`"DAPI"`), a volume (`"b0"`). References use it. |
| `name` | A display name for this position (`"DAPI (nuclei)"`). |
| `position` | Where this sample sits along the dimension, as a multiple of the step: the sample is at `position · step` from the origin instead of `index · step`. Real-valued, and may be negative (irregular slice spacing; irregular frame times, with a step of one time unit). |
| `origin` | This sample's world position along this dimension: it replaces `origin + index · step` for this dimension, and the other dimensions' `index · step` still add (per-slice positions no uniform step fits). |
| `steps` | At this sample, the step of every dimension, in dimension order (`null` for one without a step): non-parallel slices. |
| `thickness` | This sample's thickness (§5.1). |
| `metadata` | Open per-sample metadata, keyed by application or standard (a DICOM slice's tags), §2.3. |

`position`, `origin`, `steps` and `thickness` belong to a dimension with a step; `id`, `name`
and `metadata` to any. A sample has at most one of `position` and `origin`.

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
| `unit` | The unit of the *quantity* (the values after `transforms`), never of stored values the transforms have not been applied to. A string is shown as written (`HU`); UCUM codes are recommended, and `[arb'U]` states arbitrary units. Was `sample_units`. |
| `transforms` | An ordered list mapping stored values to the quantity, applied first to last. Was `value_transforms`. |

**`transforms: []` states that the stored values are the quantity; a writer states an identity
this way, never as a `linear` of slope 1 and intercept 0. An absent `transforms` states
nothing**, so with a `unit` and no `transforms` a reader knows what the quantity is but not how
the stored values give it, and must not show stored values in that unit. In 2.0 this is the only
rule; 1.x files keep their own (§14).

The transform types are 1.2's, with one parameter renamed:

| Type | Parameters | Quantity |
|---|---|---|
| `linear` | `slope`, `intercept` | stored · slope + intercept |
| `lut` | `values`, `first_value` (default 0) | `values[stored − first_value]`, clamped to the table |
| `axis_linear` | `dimension` (an index into `dimensions`), `slope`, `intercept` | stored · slope[i] + intercept[i] at position i along that dimension |

In `axis_linear`, `slope` and `intercept` are each a number (the same at every position) or a
list with one entry per position along the dimension (a per-frame decay correction). A mapping
that varies along two dimensions at once (per-instance rescale over both time and slice) has no
form yet, and is left unstated.

---

## 7. `world.transforms`

How this world relates to other frames: registrations, template spaces, other time bases, and
the further frames its own axes belong to (§3.2). Every such relation lives here, including
those an extension defines, so one fact has one place.

```json
"transforms": [
  { "to": { "reference": "nifti:mni152", "axes": [ { "id": "x", "unit": "mm", "positive": "right" }, ... ] },
    "on": ["x", "y", "z"],
    "forward": { "affine": [[1.02, 0, 0, -2.5], [0, 0.98, 0, 4.0], [0, 0, 1.01, 1.2]] },
    "metadata": { "software": "ANTs 2.5", "method": "affine" } },
  { "to": { "reference": "stimulus", "axes": [ { "id": "t", "unit": "s" } ] }, "on": ["t"],
    "forward": { "affine": [[1.0, -12.5]] } }
]
```

| Field | Meaning |
|---|---|
| `to` | The target frame: its `reference`, an optional `name`, and `axes` describing its axes as world-axis objects (§3.1). Without `axes` the target's coordinates are numbers whose units and directions are not stated; a writer states them. |
| `on` | The ids of this world's axes the transform acts on. Absent: all of them. |
| `forward` | A transform object mapping this world's coordinates (on `on`) to the target's. |
| `inverse` | A transform object mapping the target's coordinates back. |
| `metadata` | Open provenance of the transform (the registration's software, method, error), keyed by application or standard. |

At least one of `forward` and `inverse` is present. The source is always this world. A pair of
`to.reference` and `on` appears at most once. A transform object has exactly one key naming its type:

| Type | Form |
|---|---|
| `identity` | `true`: the axes on `on` are the target frame's (§3.2) |
| `affine` | rows of an m × (n+1) matrix: n = the axes it acts on, m = the target's axes |
| `sequence`, `displacements` | reserved, with OME-Zarr 0.6's meaning; not defined in 2.0 |
| `<extension>:<type>` | defined by an extension (a FITS celestial projection) |

A reader that does not know a type keeps it unchanged and does not apply it; the file stays
valid. The type names and the row-form affine are OME-Zarr's, so a transform exports as a copy.
Group-level transforms relating one member array's world to another's are reserved, with a
`{ "path": … }` reference as 1.1's transform specification reserved it.

A transform only relates coordinates. `origin` and `step` place the array; `frame` gives the
basis of vector components; neither is a transform here.

---

## 8. Resampling and derived arrays

- Only a dimension with a `step` may be interpolated. A `components` dimension never is:
  interpolating across it mixes different quantities. A dimension that states neither is not.
- Values are interpolated as quantities. An affine transform commutes with interpolation, so a
  reader may interpolate stored values under `linear` or `[]`; under `lut`, or `axis_linear`
  along the dimension being interpolated, it applies the transform first. With `transforms`
  absent the mapping is not stated, and interpolating stored values is the reader's own
  assumption.
- A `label-map` (§2.2), and the binary labelmaps of the `seg` extension, are resampled
  nearest-neighbor only.
- Vectors and tensors are interpolated component by component in one basis; a reader brings them
  into the world's axes (§5.3) before resampling onto a grid with other directions.

**Derived arrays** (1.x §4.5, carried over): a tool that derives an array — resampling,
filtering, registration — writes what is true of the new array. It keeps the world (its axes,
`id` and transforms) when the coordinates are unchanged, writes the new placement, keeps
`values` only where the quantity is unchanged, and does not carry forward an extension it does
not know, since it cannot tell which of its fields remain true. Registration changes the frame,
and with it the `id`.

## 9. Writers and converters

1.x §4.7, carried over: **every writer states only what is true of the array it writes, and
states it so it cannot be misread.** A writer that cannot vouch for a fact leaves it out. A
converter keeps a source extension only while the array faithfully re-encodes that source, and
makes that extension true of this array. What a writer optimizes for (compact storage, fidelity
to a source, compliance with a standard) is its own choice, and a tool documents the choice it
makes.

## 10. Consistency rules

1. `origin`, every `step`, every `samples[i].origin` and every vector in `samples[i].steps` have
   one component per world axis.
2. A dimension has at most one of `step` and `components`; `centering`, `thickness`,
   `samples[i].position`, `.origin`, `.steps` and `.thickness` belong to a dimension with a
   `step`; `color_space` and `frame` to one with `components`.
3. The steps are linearly independent.
4. A `components` kind's fixed size matches the dimension's length in `shape`.
5. World axis ids are unique, and so are sample ids along a dimension. A world uses at most one
   term of each `positive` pair. The spatial axes share one unit.
6. `frame` is square and invertible, one row and column per spatial world axis, on a spatial
   kind of the world's spatial size (§5.2, §5.3).
7. A `color_space` fits its kind (§5.3).
8. A transform's `on` names axes of this world; its affine has one column per such axis plus
   one, and one row per target axis; a `to.reference` and `on` pair appears once.
9. `lut` is non-empty and first in `values.transforms`; `axis_linear` names an existing
   dimension, and each list-valued parameter has one entry per position along it.
10. `samples`, where present, has one entry per position along its dimension; no entry has both
    `position` and `origin`.
11. An `id` is a token; a `reference` is a token that may carry one extension prefix and colon.

**A file that breaks a rule is invalid.** A reader refuses its duckn metadata rather than guess
which part to trust, and may still give access to the Zarr array's raw values.

## 11. What a reader derives

- **The index-to-world affine**: its columns are the steps, its last column the origin (for a
  regular grid; `samples` override it per position).
- **The grid's cells and each sample's measured interval** (§5.1).
- **LPS or RAS, and handedness**, from `positive` (§3.1), through one function per language
  (Python in duckn, JS in `duckn-spatial`): the permutation and signs taking this world's
  spatial axes to LPS. A world whose spatial axes lack `positive` has no orientation, and the
  function says so.
- **What each axis and dimension is**: an axis from its unit (§3.1), a dimension from its step.

---

## 12. Groups

As in 1.2: a Zarr group may carry `duckn` with `version`, `intent` and `extensions` only, and
every member array is a complete duckn array on its own, read under the version it declares — a
2.0 group may hold 1.x members during migration. A group-level `world` shared by its members is
reserved, not defined.

---

## 13. Examples

### 13.1 An oblique CT

```json
"duckn": {
  "version": "2.0",
  "world": {
    "reference": "dicom:1.2.826.0.1.3680043.8.498.10033451",
    "axes": [
      { "id": "x", "unit": "mm", "positive": "left" },
      { "id": "y", "unit": "mm", "positive": "posterior" },
      { "id": "z", "unit": "mm", "positive": "superior" }
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

### 13.2 fMRI with slice timing

Volumes are instants (node); each slice is 3 mm higher and 0.055 s later than the one below.

```json
"world": {
  "axes": [
    { "id": "x", "unit": "mm", "positive": "right" },
    { "id": "y", "unit": "mm", "positive": "anterior" },
    { "id": "z", "unit": "mm", "positive": "superior" },
    { "id": "t", "unit": "s" }
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

### 13.3 An RGB slide

No stage origin is known, so `origin` is absent: the steps give the image's own geometry.

```json
"world": { "axes": [ { "id": "x", "unit": "um" }, { "id": "y", "unit": "um" } ] },
"dimensions": [
  { "step": [0, 0.25], "centering": "cell" },
  { "step": [0.25, 0], "centering": "cell" },
  { "components": "RGB-color", "color_space": "srgb" }
],
"values": { "transforms": [] }
```

### 13.4 A single slice with its thickness

A sagittal DICOM slice stored as a 1 × 256 × 256 array; the length-1 dimension carries the
slice normal and thickness.

```json
"origin": [10.0, -120.0, 90.0],
"dimensions": [
  { "step": [1.0, 0, 0], "thickness": 5.0 },
  { "step": [0, 0, -1.0], "centering": "cell" },
  { "step": [0, 1.0, 0], "centering": "cell" }
]
```

### 13.5 A diffusion tensor field

```json
"dimensions": [
  { "step": [0, 0, 2.0], "centering": "cell" },
  { "step": [0, 2.0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0], "centering": "cell" },
  { "components": "3D-symmetric-matrix", "frame": [[0.9848, 0, 0.1736], [0, 1, 0], [-0.1736, 0, 0.9848]] }
],
"intent": "diffusion-tensor"
```

### 13.6 Dynamic PET

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

### 13.7 Fluorescence channels

```json
"dimensions": [
  { "components": "list", "samples": [ { "id": "DAPI" }, { "id": "GFP" }, { "id": "mCherry", "name": "mCherry (actin)" } ] },
  { "step": [0, 0, 0.5], "centering": "cell" },
  { "step": [0, 0.1, 0], "centering": "cell" },
  { "step": [0.1, 0, 0], "centering": "cell" }
]
```

### 13.8 A registration, a timeline, and a shared time base

See §3.2 and §7.

### 13.9 A diffusion-weighted series

```json
"dimensions": [
  { "components": "list" },
  { "step": [0, 0, 2.0], "centering": "cell" },
  { "step": [0, 2.0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0], "centering": "cell" }
],
"intent": "diffusion-weighted",
"extensions": { "dwmri": { "version": "2.0" } }
```

The b-values, the gradients, and the frame the gradients are written in are the `dwmri`
extension's (§17): they describe the acquisition of each volume, not the components of a value.

### 13.10 The minimum

```json
"duckn": { "version": "2.0", "dimensions": [ { "components": "list" } ] }
```

A valid file. It says the one dimension holds a list, and nothing else.

---

## 14. Reading 1.x files

A 2.0 reader maps every 1.x file in one function; writers write 2.0.

| 1.x | 2.0 |
|---|---|
| `space: "left-posterior-superior"` (or `LPS`) | `world.axes` `left`, `posterior`, `superior` |
| `right-anterior-superior`, `left-anterior-superior` (RAS, LAS) | the matching terms |
| a `-time` space | the three, plus a time axis `{ "id": "t" }` |
| `scanner-xyz`, `3D-right-handed`, `3D-left-handed`, the general 3D names | three axes without `positive`; a stated handedness is reported as not carried over |
| `space_dimension: n` | n axes without `positive` |
| a spatial axis's `unit` | each world axis takes the unit of the spatial axes whose `space_direction` has a nonzero component along it, normalized to UCUM (`µm`, `micron`, `micrometer` → `um`; `sec` → `s`; `ppm` → `[ppm]`); a world axis no spatial axis steps along takes the unit all spatial axes share, if they share one. Spatial axes in different units are legitimate when each steps along world axes of its own unit (a grid permuted against a world in `mm` and `um`, as NRRD's per-world-axis `space units` state it); a file is refused only when one world axis would take two units (an oblique step across axes in different units) |
| `space_origin` | `origin` |
| `axes` | `dimensions` |
| `space_direction` | `step` |
| `kind: "domain" / "space" / "time"` with a direction | implied by the step |
| `kind: "time"` with per-sample times and no direction | a time axis added to the world; the dimension steps along it by one time unit, each sample's time its `position` |
| `kind: "time"` with no times | a dimension that states nothing; no time axis is added |
| `kind: "domain"` with per-sample positions, a unit and no direction (a NIfTI spectrum in `Hz`, `ppm` or `rad/s`, as duckn 0.6.1 writes it) | a world axis in that unit is added (§3.3's chemical shift; §19 item 3); the dimension steps along it by one unit, each sample's coordinate its `position` |
| `kind: "domain"` with neither positions nor direction | a dimension that states nothing |
| `kind` of a range kind | `components`, the same word |
| `measurement_frame` | `frame` on each spatial components dimension; on a file with none, the `frame` of the extension whose vectors it governs (`dwmri`); otherwise it is reported as not carried over. A 1.0 file's frame is in columns and is transposed. |
| `samples[i].position` (a distance) | `samples[i].position` divided by the spacing |
| `samples[i].position` and `.origin` together | `samples[i].origin` |
| `samples[i].directions` | `samples[i].steps` |
| `sample_units` | `values.unit` |
| `value_transforms` (absent in a 1.0/1.1 file) | `values.transforms: []` (1.0/1.1 meant identity) |
| `value_transforms` (absent in a 1.2 file) | absent (1.2 meant "not stated") |
| `axis_linear`'s `axis` | `dimension` |
| `space_transforms` from `world` | `world.transforms`, with its `metadata` |
| `space_transforms` from `index`, `axis-aligned`, `axis-aligned-centered` | composed with the placement into a transform from this world (§19) |
| an extension's own `space_transforms` | `world.transforms`, the target qualified by the extension's name |
| a 1.x target `to.name` | `to.reference` |
| `centering`, `thickness`, `color_space`, `intent`, `unit_systems`, `extensions` | unchanged |

**Files from duckn 0.6.0 and earlier converters.** The mapping carries what a 1.x file says, and
four converters said wrong things until duckn 0.6.1: an irregularly spaced DICOM series' sample
positions were absolute coordinates along the slice normal, not distances from the origin; a 4D
DICOM series with only Temporal Position Identifier had those indices labeled milliseconds; a
NIfTI vector or tensor file (components in the 5th dimension) gained a one-sample time axis and
lost its components' kind, and a spectrum in `Hz` or `ppm` became time; an NRRD's `space units`
were given to spatial axes by position, not by the world axis each steps along. A reader cannot
detect these, because no 1.x store records which library wrote it (§19 item 8). They are
re-converted from their sources, not repaired in the mapping.

## 15. Correspondence

### 15.1 NRRD

| NRRD | 2.0 |
|---|---|
| `dimension`, `sizes`, `type`, `encoding`, `endian` | Zarr's `shape`, `data_type`, `codecs` |
| `space` | `world.axes[].positive`; export permutes and flips to one of NRRD's three orientation names |
| `space dimension` | the number of `world.axes` |
| `space units` | `world.axes[].unit`, one per world axis |
| `space origin` | `origin` |
| `space directions` | `dimensions[].step` |
| `measurement frame` | `dimensions[].frame` (transposed: NRRD writes columns) |
| `kinds`: domain kinds | a `step` |
| `kinds`: range kinds | `components` |
| `centers` | `centering` |
| `thicknesses` | `thickness` |
| `spacings`, `units`, `axis mins` of a domain axis with no `space` | a world axis with that unit, a step of that spacing along it, and the origin's component from the axis min (§19) |
| `labels` | Zarr's `dimension_names` |
| `old min`, `old max`, `content`, `axis maxs`, `units` of a range axis | the `nrrd` extension |
| key/value pairs | the `keyvalues` extension |

### 15.2 OME-Zarr 0.6

| duckn 2.0 | OME-Zarr 0.6 |
|---|---|
| `world` | a coordinate system `{name, axes}`; `world.reference`, or `"world"`, becomes its name |
| a world axis's `id`, `unit` | an axis's `name`, `unit` (UDUNITS-2 spelling on export: `mm` is `millimeter`) |
| what an axis measures (from its unit) | axis `type`: `space`, `time` |
| `positive: "left"` | nothing in 0.6; RFC-4's `orientation: {type: "anatomical", value: "right-to-left"}` in OME 1.0 |
| `origin` and axis-aligned steps | a level's `scale` and `translation` |
| oblique steps | an array-aligned coordinate system (scale, translation), then an `affine` to the world's |
| `world.transforms` | `coordinateTransformations` from this system to the target; an `on` subset is a `byDimension` |
| a `components` dimension | an axis of `type: "channel"`, discrete, in the same coordinate system |
| `centering` | OME 0.6 fixes the pixel center at the coordinate: `cell`. A `node` grid has no OME form. |
| `values`, `components` meaning, `frame` | nothing: these stay duckn's |

OME 0.6 limits an image to 2–5 axes, at most one channel-like axis, in time/channel/space order;
an array outside those limits has no OME view until OME 1.0. OME metadata sits on an image
group, duckn on each array. Both may be written: `duckn` authoritative, the `ome` block derived
from it (as NIfTI-Zarr makes NIfTI authoritative); a store that began as OME is converted to
duckn once, after which the two must agree.

## 16. Why these names

- **`dimensions`, not `axes`.** NRRD used "axis" for both an array dimension and a world
  direction. Zarr calls the first a dimension, geometry and OME-Zarr call the second an axis.
- **`world`.** FITS's World Coordinate System, VTK's and ITK's world coordinates, Slicer's
  IJK-to-RAS, and 1.x's own built-in `world` space all use it for exactly this: the continuous
  coordinates indices map to, including time and frequency. Rejected: `space` (position only,
  and 1.x's `kind: "space"`), `frame` (collides with time frames and DICOM multi-frame),
  `coordinate_system` (long, and OME's plural means several named systems), `physical`.
- **`step`, not `space_direction`.** 1.x had to warn that its directions "are not unit
  vectors"; in ITK, DICOM and MINC a "direction" is one.
- **`centering`, kept.** The standard term (cell-centered, node-centered grids). `sampling` was
  rejected: it names acquisition or rate, and `thickness` already says what a measurement
  integrated over.
- **`positive` with one end named.** It says where values grow; naming both ends reads as a span
  across the origin, and with three unambiguous pairs the second end carries nothing. RFC-4's
  joined form is written only on export.
- **`components` with NRRD's words and sizes.** Only the domain kinds were replaced, by a step.
- **`values`, and `transforms` inside what they transform.** The containing object does what a
  prefix did.
- **`id` and `name`, as in `seg`, and `reference`.** Keys unique within their scope are `id`s —
  tokens, stable under renaming, usable in URLs and CSV cells; display strings are `name`s. A
  frame of reference is shared by many files, so it is not an id: it is a `reference`, DICOM's
  own word for it ("Frame of Reference"). It keeps its colon so it works as a key.
- **`dimension` in `axis_linear`**: it names an array dimension, by index.
- **One zero rule for space and time.** A frame owns its zeros; a `reference` makes them shared.
  DICOM's Frame of Reference and Synchronization Frame of Reference are the precedent.

**Considered and not adopted (2026-09-26/27):**

- A richer world: a `reference` or `relative_to` vocabulary, display and geographic frames,
  quadruped and part-relative terms, stated handedness, per-axis `quantity`, `period`. None has
  a writer; each can be added later as an optional field or term, and an unknown term reads as
  "unknown".
- A `negative` field: determined by `positive` for these pairs.
- An absolute time anchor (`epoch`): a shared time base is an identity (§3.2), not a date; a
  date in the geometry sits where de-identification tools do not look.
- OME-Zarr's shape inside duckn, or NRRD fields and structured axes side by side: two statements
  of one fact. The OME view is derived.
- Keeping NRRD's field names in 2.0: the words were the confusion; the principles are kept.

## 17. Extensions

Each extension is revised separately; this is what 2.0 asks of each (from the study of
2026-09-27).

| Extension | Revision | What changes |
|---|---|---|
| `dwmri` | 2.0 (breaking) | A `frame` beside `gradients`/`b_matrices`, defined as in §5.3 (gradients F·g, b-matrices F·B·Fᵀ; absent means unknown), replacing `gradient_frame`; the `vector` DWI dimension becomes `list`; phase-encoding directions name a dimension; the §3 interleaving table (which read axis order fastest-first) is withdrawn. Required before any 2.0 DWI file: an unrevised 1.0 block would read a lost measurement frame as identity. |
| `seg` | 0.10, wording only | "a `list` axis" → "a dimension with `components: \"list\"`"; binary labelmaps write `values.transforms: []`; no field changes. |
| `microscopy` | 2.0 (breaking) | `timestamps` and `z_positions` move to core `samples[i].position`; channel `color` becomes a CSS string (as `seg`); §1 against OME 0.6; spectral and FLIM bins (§19). |
| `nifti` | 2.0 | the world from sform/qform (codes 1–5 are RAS), a differing qform as a `world.transforms` entry, `xyzt_units` onto world axes, `toffset` onto the origin, a 4th dimension that is time only when its unit is a time (a `Hz`, `ppm` or `rad/s` unit makes it a spectral world axis), intent codes onto `intent` and `components`: a vector or matrix intent's components are the 5th dimension, as nifti1.h lays them out, with a length-1 4th dimension that states nothing, or the 4th dimension in the four-dimensional layout some tools write; names for codes 3 and 4. |
| `dicom` | 1.1 | Frame of Reference UID → `world.reference` (`dicom:`), Synchronization Frame of Reference UID → an identity transform (`dicom-sync:`), a new section defining those names, per-frame geometry onto `samples`, time only from real times, varying rescale onto `axis_linear`. |
| `fits` | 2.0 (breaking) | the world is FITS's *intermediate* world coordinates (exact, linear); CTYPE/CUNIT/CRVAL/CDELT/PV on world axes, CRPIX on dimensions; the celestial projection as an extension-defined transform type to `fits:icrs`; a unit table to UCUM. |
| `nrrd` | 0.2 | `spacings` of a no-space file become world axes (§19); `space units` map one per world axis (export writes one entry for every world axis, including one no dimension steps along, as NRRD requires); range-axis `units` and a scalar file's measurement frame stay in the extension. |
| units spec | folded into this document (§19) | the UCUM reading on world axes; normalization tables (OME/UDUNITS, FITS, NIfTI) and the UDUNITS names for export. |
| transform spec | folded into §7 (§19) | the derived spaces' formulas move to the implementer's guide. |
| `keyvalues`, `provenance`, `presentation` | wording | `presentation` requires `[]` before its windows apply. |

## 18. Adversarial rounds and studies

**Round 1 (revision 1).** Fourteen scenarios with known answers; two writers, two blind readers.
The writers agreed on 11 of 14 headers; every stated position the readers computed was correct.
Answered by revision 2: `[]` and color, the example `frame` on a list, an invented origin, time
centering, `position` as a multiple of the step, `samples.origin` composition, thickness,
per-kind `frame` rules, resampling, invalid files, `axis_linear`'s `dimension`, handedness and
units.

**Round 2 (revision 2).** Sixteen scenarios, fresh agents. The writers wrote identical headers for
11 of 16, including a single slice, irregular spacing and a skewed covariant frame; both readers
recovered every stated value. Answered by revision 3: the origin as the point at position 0,
`frame` as rows, space-time centering, an unstated dimension claiming nothing, length-1
dimensions, the unit of the quantity, target axes, 1.x extensions.

**Round 3 (revision 3).** Complete scenario lists, fresh agents. The writers wrote identical core
headers for 15 of 16; the sixteenth differed only in writing an identity as `linear` instead of
`[]`. Both readers recovered every stated position, extent, time and value, including irregular
PET frames and time-lapse instants. Answered by revision 4: the two extents (the grid's cells
and each sample's measured interval), the time wording of §4, `thickness` in world units,
`frame`'s size per spatial world axis, `[]` for identity, no `centering` on a length-1
dimension.

**Extension study (revision 3).** Every extension and format document read against the draft, with
the code that implements them. Answered by revision 4: `id`/`name` throughout, and `reference` for frames; one zero rule for
space and time and identity transforms for further frames; `list` claiming no coincidence;
`vector` keeping NRRD's any size; world-axis `extensions`; extension-defined and unknown
transform types; transform `metadata`; a 1.x time axis without times not given a step; UCUM
normalization; the measurement frame of a DWI file; 1.x §4.5 and §4.7 carried over (§8, §9);
groups with 1.x members; `intent` wording; one home per extension fact; OME 0.6's centering and
axis limits.

**duckn 0.6.1 (revision 5).** The extension study found four 1.x converter defects; 0.6.1 fixed
them in 1.x, each toward what this draft already specified (§5.4 positions from the origin, the
`dicom` row's time only from real times, the `nifti` row's time only for a time unit, §15.1's
units per world axis). Answered by revision 5: §14's unit rule (it refused files 0.6.1 writes),
a §14 row for the spectral `domain` axis 0.6.1 writes, the `nifti` row's component layout, the
note on files from older converters, and §19 item 8.

## 19. Open decisions

1. **Transforms from derived spaces.** 2.0 drops `index`, `axis-aligned` and
   `axis-aligned-centered` as transform sources; a 1.x transform from one composes into a
   transform from this world without loss, except where the placement is not invertible (fewer
   steps than world axes), where it is reported.
2. **NRRD `spacings` without `space`.** §15.1 turns them into world axes, reversing the `nrrd`
   extension's 0.1 rule that no embedding is inferred. The added axis needs an origin component,
   taken from `axis mins` or local zero.
3. **Microscopy spectral bins** as a world axis (exact, but then interpolable) or a `list`.
4. **FLIM microtime.** A second time-valued axis beside acquisition time; with axes classified by
   unit alone, a reader cannot tell them apart. Kept as a `list` unless the core adds a
   distinction.
5. **NIfTI slice timing and a 3D file's time unit.** Slice timing can now live in the geometry
   (a space-time step, or per-slice time origins); stating it there and in the `nifti` tags would
   be one fact in two places. A 3D file with a time unit: a time axis no dimension steps along
   (a claim of one time point), or the unit kept in the tags.
6. **Template names.** Whether `nifti:mni152` names any MNI template the header claims, or finer
   names (`mni152nlin2009c`) are defined for the variants that differ by millimeters.
7. **Folding the units and transform specifications into this document.**
8. **The writing software.** Nothing in a 1.x store says which library and version wrote it,
   so a reader cannot tell a file from a converter later found defective (§14's note). A 2.0
   field naming the writer (and its version) beside `version` would let a reader recognize
   such files; the `provenance` extension could hold it instead, but a core field is read by
   every reader.
