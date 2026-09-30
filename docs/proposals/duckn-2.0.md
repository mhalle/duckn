# duckn convention 2.0 — draft specification

**Status:** draft for review, revision 11, 2026-09-30. Not implemented. Supersedes the two
world-frame proposals of 2026-09-26/27, whose decisions it records in §16. Revisions 2–4 answer
three adversarial rounds and a study of every extension; revision 5 follows duckn 0.6.1's
converter fixes; revision 6 settles the open decisions (§19) and carries over what 1.x settled
since; revisions 7 to 11 answer review rounds 4 to 8 (§18), with the choices of their
own listed in §19 for the owner. It folds in the units specification (§3.4) and, for 2.0 files, the transform
specification (§7).

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
different: it names a frame of reference (§3.2), which many files may share, so it is not unique
to this file and is not an `id`. It is either `<extension>:<value>`, the value in the grammar
that extension defines (a DICOM UID, digits and dots), or a bare token, which is local to the
array and says nothing about any other array, even one in the same Zarr group that uses the same
token (a converter writes fixed tokens, such as `qform`, for every file's own frame). A frame
shared by several arrays is named by a prefixed reference; a group-level way to share a bare one
is reserved with the group-level `world` (§12).

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
| `version` | §2.1 | The convention version: `"2.0"`. Required. |
| `world` | §3 | The world's axes, and how this world relates to other frames. |
| `origin` | §4 | The world point at index 0 on every dimension. |
| `dimensions` | §5 | One object per array dimension, in the order of `shape`. |
| `values` | §6 | What the stored numbers mean. |
| `intent` | §2.2 | What the array represents as a whole. |
| `unit_systems` | §3.4 | The registry of unit systems used by structured units. |
| `extensions` | §2.3 | Domain metadata, per extension. |

Every field but `version` is optional. **Absent means unknown**: a field is left out rather than given a
default or a sentinel, and a reader never assumes what a file does not state. The array's
`shape`, `data_type` and `dimension_names` are Zarr's, and a reader uses them with this metadata.

### 2.1 `version`

The version of this convention, `"major.minor"`, required. A file without one is a 1.x file,
read as 1.0 (§14), where an absent `value_transforms` meant identity; one that has any of 2.0's
own fields (`world`, `origin`, `dimensions`, `values`) and no `version` is neither, and is invalid
(§10 rule 1): reading it as 1.0 would present unstated stored values as the quantity. It is the only version number a
reader acts on. A minor version only adds, and a reader of an earlier minor version ignores the
fields it does not know; a major version breaks. Software that implements the convention has
its own release numbers, which say nothing about a file.

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
| `"displacement-field"` | a displacement `d` on a `vector` dimension, defined on this array's grid: the point `p` corresponds to `p + d(p)`, `d` taken into this world's axes through the dimension's `frame` (§5.3), in `values.unit`, a length (converted to the spatial axes' unit; absent or unresolved through UCUM, §3.4, the magnitudes are unknown) |
| `"velocity-field"` | velocities on a `vector` dimension, taken into this world's axes through its `frame` (§5.3), in `values.unit`, a length per time (absent or unresolved through UCUM, the magnitudes are unknown) |

### 2.3 `extensions`

Each extension's top-level block carries its `version`. A dimension, or a world axis, may carry
a block for the same extension; it has no version of its own, is read under the top-level
block's, and requires that block to be present. A reader reads a block under a version it
knows, or under the one the extension's own specification says it may: at major 1 or above, a
later minor of a known major is read under the latest minor it knows (a minor only adds); a 0.x
version is read only as itself, because a 0.x specification may change incompatibly (1.x core
§3.1; `seg` refuses a later 0.x); an older version it no longer knows is read as that
extension's specification says. A block it cannot read under these rules is ignored and
reported.

Extensions keep their own specifications, each revised for 2.0 separately (§17). An
extension §17 marks *compatible* may be carried unrevised in a 2.0 file, read under its own
specification with its 1.x names read as their 2.0 counterparts (`axes` → `dimensions`, `kind`
→ `components` or a step, `space_direction` → `step`, an axis `unit` → the world axis's unit,
`sample_units` → `values.unit`, `value_transforms` → `values.transforms`; §14). An extension §17
marks *revision required* is not written in a 2.0 file until its revision exists: its 1.x
version depends on a field 2.0 does not have (`dwmri` on `measurement_frame`, `microscopy` on
dimension units) or says something 2.0 contradicts (`nifti` 1.1 keeps slice timing in the
extension; `nrrd` 0.1 infers no world).

**One fact, one home.** An extension states each per-position fact in one place: either in its
dimension-level block or in `samples[i].metadata`, as its specification says, and a reader
looks only there. An extension does not restate, *as a statement about this array*, what the
core states (a frame time, a slice position, a voxel size).

**A source record is not a restatement.** A source-format extension (`dicom`, `nifti`, `fits`,
`nrrd`) may keep the source's own attributes as a record of where the array came from. **Each
such extension's specification says what its record leaves out**, and a record, where one is
written, keeps the rest. What is left out is chiefly what the core now states in the array's
own terms — the grid and its placement, the layout, the mapping from stored values to the
quantity (for DICOM: Image Position and Image Orientation, Pixel Spacing, Slice Thickness,
Rescale Slope and Intercept) — plus what the extension excludes for its own reasons (DICOM's
overlays, file meta group and per-frame functional groups, and private elements at the
writer's choice, as dicom 1.x §9 has them). Identifiers and acquisition facts are kept even
where the core states a fact computed from them, unless the extension says otherwise: DICOM
keeps its Frame of Reference UID beside `world.reference`, a slice's Trigger or Acquisition Time
beside its time position, and a Slice Location (whose zero DICOM leaves unstated); NIfTI's slice
timing fields are the exception, left out once the geometry states them (§19 item 5). What a
converter turns into nothing is kept (a NRRD's `old min` and `old max`). An attribute in units the array no longer has is governed
by §9, not by the list: a DICOM Pixel Padding Value stays in the record only while the array
holds the source's own stored values. The core is authoritative for the array; a reader never takes
the array's geometry, layout or values from the record, and may use the rest as what the
source said. A record never contradicts the array (§9).

**References to an extension without its block.** An `intent`, a `world.reference`, a
transform target's `reference` or a transform type that names an extension (`diffusion-weighted`, `dicom:…`,
`fits:…`) does not require that extension's block. Without it the file is incomplete, not
invalid: it says what the array is and leaves the details unstated. An extension block that
breaks its own specification is handled as that specification says (the `seg` extension grades
its errors); where it says nothing, readers of that extension ignore the block and report it.
The core metadata stays valid either way.

---

## 3. `world`

```json
"world": {
  "reference": "dicom:1.2.826.0.1.3680043.8.498.10033451",
  "axes": [
    { "id": "x", "type": "space", "unit": "mm", "positive": "left" },
    { "id": "y", "type": "space", "unit": "mm", "positive": "posterior" },
    { "id": "z", "type": "space", "unit": "mm", "positive": "superior" }
  ],
  "transforms": [ ... ]
}
```

| Field | Meaning |
|---|---|
| `axes` | The world's axes, in the order of the components of `origin`, of every `step`, and of `samples[i].origin` and `samples[i].steps`. Its length is the world's dimension. Required when `world` is present. The order carries no meaning; so that one source gives one header, a converter writes the spatial axes first — in the source's world order where it has one (DICOM's and NIfTI's x, y, z), otherwise in the reverse of the order of the dimensions stepping along them (the axis of the last dimension in `dimensions` first; an oblique dimension counting for the axis of its largest component) — then the time axes, then the rest (axes of no stated type with the spatial ones). It names them `x`, `y`, `z`, then `a3`, `a4`, … (spatial and untyped axes), `t`, then `t1`, … (time), and by its type word for another type (`wavelength`, `chemical-shift`; a second one numbered `wavelength1`). |
| `reference` | The frame of reference this world is measured in, when known (§3.2). |
| `name` | A display name for the frame. |
| `transforms` | How this world relates to other frames (§7). |

### 3.1 A world axis

| Field | Meaning |
|---|---|
| `id` | A token, unique among this world's axes (`"x"`, `"t"`). Transforms address axes by it (§7): an axis a transform acts on must have one. |
| `type` | What the axis measures: `"space"`, `"time"`, or another word for another continuous coordinate (`"wavelength"`, `"chemical-shift"`, `"frequency"`, `"angle"`). Absent: unknown (§3.1). |
| `name` | A display name (`"chemical shift"`). |
| `unit` | The unit of this coordinate: the component along this axis of `origin`, of every `step`, of `samples[i].origin` and `samples[i].steps`, and of `thickness` is in it. (The unit of vector or tensor *values* is `values.unit`, §6.) |
| `positive` | For an axis of `type` `space`: the direction in which coordinates increase. One of `left`, `right`, `anterior`, `posterior`, `superior`, `inferior`. Absent: unknown. `positive` requires `type` `space`. |
| `extensions` | Per-axis extension metadata (a FITS world axis's WCS keywords), §2.3. |

**Units.** A string is a UCUM code (case-sensitive: `mm`, `um`, `s`, `ms`, `Hz`, `[ppm]`). A
structured unit (§3.4) is read through its UCUM code when its `scheme` is UCUM, and is
otherwise unknown unless mapped. A unit that carries a reference time (UDUNITS `hours since
2020-01-01`) is not a world-axis unit: a time axis's zero is its frame's (§3.2).

**What an axis measures is its `type`,** stated, never inferred from the unit: a wavelength in
`nm` is a length but not a position (revision 7; revision 3 let the unit decide, and a spectral
axis in `nm` became a spatial one). A writer states the type whenever it knows it; an axis
without one measures something unknown, and is neither spatial nor temporal to a reader. The
spatial axes are those of `type` `space`; `positive`, handedness, `frame` and the spatial kinds
of §5.2 concern them alone. A sky image's right ascension and declination are angles (`type`
`"angle"`), not spatial axes.

**The unit fits the type**, for the types defined here: `space` and `wavelength` a length,
`time` a time, `frequency` a frequency (`Hz`) or an angular frequency (`rad/s`),
`chemical-shift` a ratio (`[ppm]`) or a frequency, `angle` an angle. For another type, or a
unit a reader cannot resolve to UCUM (§3.4), the fit is not checked. A writer never writes a
unit that does not fit; a reader that finds one takes the axis's unit as unknown and reports
it. It is not a matter of validity, because whether a unit resolves depends on how much of UCUM
a reader parses, and a file must not be valid to one reader and refused by another.

**Which axes compare across files.** Two types are measured from a zero that belongs to the
quantity itself and compare across files directly: `wavelength` (500 nm is 500 nm in any file)
and `chemical-shift` (0 ppm is the reference compound's resonance). **Every other axis has a
local zero** — `space`, `time`, `frequency` (often an offset from a carrier), `angle` (a sky's
angles belong to a celestial frame), any other type, and an axis of no type — and compares
across files only through a frame stated for it: a prefixed `world.reference` for the spatial
axes, or a transform to a reference whose `on` names the axis (§3.2, §7). Nothing about an
extension a reader may not know changes this.

**For the rules below and §10 rule 5, two units are the same** when both are absent, when their
UCUM codes are the same string (a string unit is its code; an object with `scheme` `UCUM`, its
`code`), or when neither has a UCUM code and they are identical JSON values; otherwise they
differ. The comparison is of what is written, never of what a reader resolves (`um` and
`10*-3.mm` differ; a writer normalizes), so that every reader decides validity alike. It is not
a claim that two absent units agree: matching two worlds (§3.2) needs every unit known.

**Spatial axes in different units** are allowed (a grid in `mm` along one axis and `um` along
another, as NRRD's per-axis `space units` can state), but no single quantity may span them: a
step with non-zero components on two spatial axes of different units, and a `frame` anywhere in
such a world, are invalid, because a step's spacing, a thickness and a basis are each one
length, in one unit. (A writer that wants such a step converts to one unit first; §3.2's
matching of two worlds converts too, but a file does not state a conversion.) A `thickness` in
such a world is in the unit of the axes its dimension steps along.

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

Every spatial and temporal axis is measured from a zero that belongs to the **frame**: a
patient-based frame's origin, a scanner's isocenter, a session's time base (other types, §3.1). `origin` (§4) places this array relative
to those zeros. This is the same in space and in time.

A world's **`reference`** names the frame of reference **of its spatial axes**, as DICOM's Frame
of Reference UID does: arrays whose worlds carry the same prefixed `reference` are measured in
the same spatial frame, and their positions compare once their spatial axes are matched —
reordered by `positive`, flipped where one says `left` and the other `right`, converted where
their units differ (an axis whose unit is absent or unresolved cannot be matched). Many arrays share a reference, which is why it is a reference and not an
`id`. A time axis is in a shared frame only when a transform says so (below); otherwise it has
a **local zero**, and so has every spatial axis of a world with no `reference` or a bare one:
its coordinates compare only within the array. A reference is
`<extension>:<value>` (`dicom:1.2.826.0.1.3680043.8.498.10033451`, `nifti:mni152`), the value in
the grammar the extension defines, or a bare token that is local to the array (§1). It stays one
string with a colon (the owner's decision, 2026-09-27): a reference must work as a key — in a
JSON object, a lookup table, a URL — and its one parse, the prefix, happens in one resolver.

**A world whose axes belong to more than one frame** states the others as identity transforms
on the axes they cover (§7):

```json
"world": {
  "reference": "dicom:1.2.826.0.1.3680043.8.498.10033451",
  "axes": [ { "id": "x", ... }, { "id": "y", ... }, { "id": "z", ... }, { "id": "t", "type": "time", "unit": "s" } ],
  "transforms": [
    { "to": { "reference": "dicom:1.2.840.113619.2.55.3.1", "axes": [ { "id": "t", "type": "time", "unit": "s" } ] },
      "on": ["t"], "forward": { "identity": true } }
  ]
}
```

Times shared this way are comparable across arrays without any date in the file: a time base is
an identity, not a moment. (A DICOM UID names one frame whichever kind it is; `on` says which
axes the transform covers, so the DICOM Synchronization Frame of Reference takes the same
`dicom:` prefix.) **A writer states a shared frame for an axis only when that axis's
coordinates are measured from the shared frame's zero.** A local zero may be any instant the
writer measures from, and the axis's `name` may say which: duckn's DICOM converter keeps Trigger
Time as the source states it (after the ECG R wave: `"name": "time after R wave"`, so a first
phase at 20 ms has the time component 20 in `origin`, the axis's unit being the source's `ms`) and measures Acquisition Time from the
first frame. Neither is the Synchronization Frame of Reference's zero,
so such a writer does not state that frame, which stays in the `dicom` extension's record
(§2.3). A shared time base needs an agreed zero, and a date as that zero was not adopted (§16);
stating one is left until an application needs it.

### 3.3 Examples of worlds

| World | `axes` |
|---|---|
| DICOM patient coordinates (LPS) | `left`, `posterior`, `superior`, each `type` `space` in `mm` |
| NIfTI (RAS) | `right`, `anterior`, `superior`, each `type` `space` in `mm` |
| A time series in LPS | the three above, plus `{ "id": "t", "type": "time", "unit": "s" }` |
| A microscope stage, orientation unknown | three `space` axes in `um`, no `positive` |
| An MR spectroscopic image | three spatial axes plus `{ "id": "chemical-shift", "type": "chemical-shift", "unit": "[ppm]", "name": "chemical shift" }` |
| A 2D slide | two `space` axes in `um` |
| A spectral image | two `space` axes plus `{ "id": "wavelength", "type": "wavelength", "unit": "nm" }` |

### 3.4 Units

This section is the units specification, folded in (1.x kept it separately). A unit, wherever
2.0 takes one (`world.axes[].unit`, `values.unit`, a transform target's axes), is a string or an
object:

| Form | Meaning |
|---|---|
| a string | a UCUM code on a world axis (§3.1); on `values.unit`, shown as written and read as a UCUM code where one is needed (a displacement's length, §2.2), UCUM recommended |
| `{ "symbol", "scheme", "code", "url"? }` | `symbol` is what a person sees; `code` is the unit in the system `scheme` names (`"UCUM"`, `"UDUNITS"`, `"QUDT"`, or another); `url` points to the unit's definition. The first three are required together. |

A world axis's unit is read through UCUM: a string is its code, an object with `scheme: "UCUM"`
its `code`; any other scheme is unresolved for a reader that cannot map it, and so is the unit
(the axis's `type` still says what it measures; §3.1 does not check the fit). A file may register the schemes it uses in the top-level
`unit_systems` (`{ "<scheme>": { "name", "version", "url" } }`); UCUM needs no registration.
Absent means unknown: a unit is left out, never written as `null`, `""` or `{}`.

**Normalization.** A reader of a 1.x file or a converter maps common spellings to UCUM codes:
`µm` (U+00B5), `μm` (U+03BC), `micron`, `micrometer` → `um`; `millimeter` → `mm`; `nanometer`
→ `nm`; `sec`, `second` → `s`; `msec`, `millisecond` → `ms`; `ppm` → `[ppm]`; `degree`, `degrees`
→ `deg`; `radian` → `rad`; `hertz` → `Hz`; `HU` (a values unit) → `[hnsf'U]`, kept as the
object's `symbol`. A writer exporting to a format
that uses UDUNITS spellings (OME-Zarr) writes UDUNITS names (`millimeter`, `micrometer`,
`second`); FITS's own unit strings are read through the `fits` extension's table (§17). A
spelling with no mapping is kept as written, and the unit is unresolved.

---

## 4. `origin`

The world point at index 0 on every dimension (position 0, where `samples` places samples by
position, §5.4), one component per world axis, each measured from its axis's zero (§3.1, §3.2). On a
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
(fMRI slice timing: each slice is 3 mm higher and 0.055 s later); such a dimension is spatial,
and its time part places each sample in time. The **spacing** of a dimension is the length of
its step's spatial part when it has one, and otherwise the step's component along its one axis;
on a sheared grid the spacing is along the step, and the separation perpendicular to the other
steps is derived. A dimension that steps along two non-spatial axes at once states no spacing.

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
  For a spatial dimension (including one whose step also moves through time) it is spatial, in
  the spatial axes' unit, measured perpendicular to the steps of the array's other spatial
  dimensions (a slice's thickness normal to the slice plane, as DICOM states it, even on a
  sheared grid); for any other dimension, along its step (a time frame's duration).

The two differ whenever the thickness is not the spacing: overlapping CT slices, gaps between
irregular slices, PET frames of unequal length. A frame's time interval is its thickness
interval; its cell is the grid's.

On a step that moves through space and time at once, `centering` describes the step's spatial
part; its time part places each sample at an instant (each fMRI slice is a spatial cell acquired
at one moment). The same holds where `samples` vary a spatial dimension's samples in time only
(§5.4): cells come from the step, and the times are instants.

**When a sample's time is geometry.** A time is stated in the geometry when the array has a time
dimension (a series), including the times at which its slices were acquired within each
volume (fMRI slice timing, §13.2). A single volume whose slices were acquired at different
moments (a CT) has no time dimension and states no time axis: its slices' acquisition times are
a fact about the source, kept in the source record where one is written (§2.3).

**A time position is where the sample sits, like any position:** an instant for a `node`
sample, the middle of its interval for a sample with a `thickness` or a `cell` (§5.1). A source
that states onsets converts them: a PET frame starting at 0 and lasting 10 s is at 5 with
`thickness` 10; a slice acquired at an instant (BIDS `SliceTiming`, NIfTI `slice_code`) is at
that instant. A time position on a dimension that states neither `centering` nor `thickness`
is where the source placed the sample, and which moment of its acquisition that is is not
stated (a DICOM Acquisition Time with no known duration).

**A dimension of length 1** may have a step. With no second sample its length is not a spacing,
and it has no cell: the step states a direction (either sense), a writer does not write
`centering` on it, and `thickness` alone gives the extent along it. A writer gives the step the
length of the thickness when one is stated, and a length of 1 in the axes' unit otherwise, so
that a voxel volume or an exported spacing computed from the steps is the measured one where it
is known. A
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
| `"3-gradient"` | 3 | in a world with 3 spatial axes | a covariant 3-vector |
| `"3-normal"` | 3 | in a world with 3 spatial axes | a unit-length covariant 3-vector |
| `"4-vector"` | 4 | | any 4-vector, not a spatial one |
| `"quaternion"` | 4 | | w, x, y, z; w real, no normalization assumed |
| `"2D-symmetric-matrix"` | 3 | in a world with 2 spatial axes | Mxx Mxy Myy |
| `"2D-masked-symmetric-matrix"` | 4 | in a world with 2 spatial axes | mask Mxx Mxy Myy |
| `"2D-matrix"` | 4 | in a world with 2 spatial axes | Mxx Mxy Myx Myy |
| `"2D-masked-matrix"` | 5 | in a world with 2 spatial axes | mask Mxx Mxy Myx Myy |
| `"3D-symmetric-matrix"` | 6 | in a world with 3 spatial axes | Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-masked-symmetric-matrix"` | 7 | in a world with 3 spatial axes | mask Mxx Mxy Mxz Myy Myz Mzz |
| `"3D-matrix"` | 9 | in a world with 3 spatial axes | Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |
| `"3D-masked-matrix"` | 10 | in a world with 3 spatial axes | mask Mxx Mxy Mxz Myx Myy Myz Mzx Mzy Mzz |

A spatial vector (a displacement, a velocity) is a `vector` of the world's spatial size; where
both `vector`/`covariant-vector` and a sized kind (`3-gradient`) fit, a writer uses the first.
A `vector` of another size (an ITK diffusion series stored as a vector of volumes) is not
spatial and takes no `frame`. NRRD's three *domain* kinds (`domain`, `space`, `time`) are not
components: a dimension that samples a continuous coordinate has a `step` (§5.1).

### 5.3 `color_space` and `frame`

**`color_space`** is one of CSS Color 4's predefined spaces: `srgb`, `srgb-linear`,
`display-p3`, `display-p3-linear`, `a98-rgb`, `prophoto-rgb` and `rec2020` for `RGB-color` and
`RGBA-color`; `xyz-d50` and `xyz-d65` for `XYZ-color`. RGB and alpha components run from 0 to
1; XYZ components are CIE XYZ, with Y = 1 at the white point (so X and Z may exceed 1). **The
quantity (§6) of a color dimension is its components,** held one of three ways: in a
floating-point type under `transforms: []`, the stored values themselves; in an unsigned integer
type under `[]`, for `RGB-color` and `RGBA-color` only, the stored value as a fraction of the
type's maximum (in a `uint8` array a stored 255 is the component 1.0: the one place `[]` reads
the storage type, as image formats do); anything else — a signed type, 12 bits stored in 16, XYZ
in an integer type — through a `linear` transform whose result is the components. Every
encoding of §6 keeps the components: a `uint8` `[]` array re-encoded as `float32` `[]` stores
1.0 where it stored 255. Without a stated mapping the components, and so the colors, are unknown. A color space is never stated by an ICC profile. Absent: unknown. A reader may
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
| `origin` | This sample's full world point (one component per world axis) at index 0 of every other dimension: it replaces `origin + index · step`, and the other dimensions' offsets still add, each from that dimension's step at this sample (`samples[i].steps` where given) times its index or its `position`. For per-slice positions no uniform step fits (a gantry tilt), and for per-slice times on a spatial dimension (slice timing), where it restates the slice's position and adds its time. A writer keeps `samples[0].origin` equal to `origin`; where they differ, a reader places samples by `samples[i].origin` and reports the file as not conformant. At most one dimension carries `origin` or `steps` in its samples. |
| `steps` | At this sample, the step of every dimension, in dimension order (`null` for one without a step): non-parallel slices. Requires `origin`: the sample's own dimension adds nothing further. |
| `thickness` | This sample's thickness (§5.1). |
| `metadata` | Open per-sample metadata, keyed by application or standard (a DICOM slice's tags), §2.3. |

`position`, `origin`, `steps` and `thickness` belong to a dimension with a step; `id`, `name`
and `metadata` to any. A sample has at most one of `position` and `origin`.

---

## 6. `values`

```json
"values": {
  "unit": { "symbol": "HU", "scheme": "UCUM", "code": "[hnsf'U]" },
  "transforms": [ { "name": "linear", "parameters": { "slope": 1.0, "intercept": -1024.0 } } ],
  "missing": [-3024]
}
```

| Field | Meaning |
|---|---|
| `unit` | The unit of the *quantity* (the values after `transforms`), never of stored values the transforms have not been applied to. A UCUM code is recommended, in the object form where a display symbol differs (`{ "symbol": "HU", "scheme": "UCUM", "code": "[hnsf'U]" }`, §3.4); a string is shown as written. `[arb'U]` states arbitrary units: a writer states it only when the source does, and otherwise leaves the unit out. Was `sample_units`. |
| `transforms` | An ordered list mapping stored values to the quantity, applied first to last. Was `value_transforms`. |
| `missing` | Values of the quantity that mark no measurement (a scanner's padding outside the field of view, a masked region), as a list: a DICOM Pixel Padding Value of −2000 stored under an intercept of −1024 is `[-3024]`. Being values of the quantity, they survive the encodings of §6 (a writer that re-encodes checks each is still exactly representable). Stated only where `transforms` is `[]` or one `linear` (one mapping, one-to-one, the same at every index); under `axis_linear` or a `lut` it has no form yet. A reader compares a sample's quantity with each listed value exactly, computing it in IEEE double precision as `stored · slope` rounded, then `+ intercept` rounded (never a fused multiply-add), or under the color exception as `stored / maximum` rounded, and a writer lists each value as that computation gives it for the encoding it writes (a materialized `float32` array lists the `float32` value). `[]` states that nothing is missing; absent states nothing, and is what a writer writes when it knows of missing values it cannot list. A range (DICOM's Pixel Padding Range Limit) has no form yet. A `missing` stated where this forbids it is ignored and reported. |

**`transforms: []` states that the stored values are the quantity** (on an `RGB-color` or
`RGBA-color` dimension in an unsigned integer type, the quantity is the stored value's fraction
of the type's maximum, §5.3: the one exception, and every rule of this section applies to that
quantity, `missing` and the encodings included). **A writer states an identity this way, never
as a `linear` of slope 1 and intercept 0. An absent `transforms` states
nothing**, so with a `unit` and no `transforms` a reader knows what the quantity is but not how
the stored values give it, and must not show stored values in that unit. In 2.0 this is the only
rule; 1.x files keep their own (§14). A source format whose own definition makes the stored
numbers the data — a NRRD without `old min`/`old max`, a NIfTI whose `scl_slope` is 0 or not finite
(unscaled, whatever `scl_inter` says, as nifti1_io and nibabel read it) or is 1 with `scl_inter`
0, a DICOM image with no rescale
and no Modality LUT — is converted with `[]`; a
writer leaves `transforms` out only when it cannot say how the stored values relate to the
quantity (a NIfTI with a usable slope and an intercept that is not finite, which nifti1_io
reads as 0 and nibabel refuses: left out and reported).

The transform types are 1.2's, with one parameter renamed:

| Type | Parameters | Quantity |
|---|---|---|
| `linear` | `slope`, `intercept` | stored · slope + intercept |
| `lut` | `values`, `first_value` (default 0) | `values[stored − first_value]`, clamped to the table |
| `axis_linear` | `dimension` (an index into `dimensions`), `slope`, `intercept` | stored · slope[i] + intercept[i] at index i along that dimension |

In `axis_linear`, `slope` and `intercept` are each a number (the same at every index) or a list
with one entry per index along the dimension (a per-frame decay correction); every value is
finite. A reader of part of the array takes the entries for the indices it read. A mapping
that varies along two dimensions at once (per-instance rescale over both time and slice) has no
form yet, and is left unstated.

**The invariant** (1.2 §4, carried over): the stored values, read through `transforms`, equal the
quantity `values.unit` names. Metadata that disagrees with the bytes it describes is worse than
missing metadata, because it is confidently wrong.

**Reading.** A reader offers either the quantity (the transforms applied) or the stored values,
and says which. It never presents partly transformed values as the quantity. A transform type it
does not know, or one whose parameters are malformed (a list of the wrong length, a value that
is not finite), makes the quantity unknown; the stored values stay available, and the rest of
the metadata stands (§10). A reader lets a caller tell a stated identity (`[]`) from an
unstated mapping (absent).

**Writing.** A writer of a known quantity chooses one of three encodings:

| Policy | Stored values | `transforms` | When |
|---|---|---|---|
| preserve | the source's stored values | carried unchanged | the quantity is unmodified; the default, and the only reversible one |
| materialize | the quantity itself (for colors in an unsigned integer type, as §5.3 encodes it) | `[]` | the quantity was modified, or a self-describing array is wanted |
| re-encode | newly quantized values | derived for the new type | the quantity is unmodified and another storage type is wanted; not for `lut`, which cannot be inverted |

Materializing an unmodified quantity (applying a rescale to write Hounsfield units) is still a
faithful re-encoding of the source: its source records stay (§9). Materializing while keeping
the transforms that produced the values applies them twice on the next read; a materializing
writer replaces them with `[]`, never leaves them out (absence would discard the one thing it
knows: that its values are the quantity). `unit` is kept in every case, and so is `missing`,
each value as the new encoding represents it; one the new encoding cannot represent exactly
empties the list to absent (not `[]`: missing values are known to exist).

**Display is not a value mapping.** A window, a level or a VOI lookup maps the quantity to
display intensities: it is not a transform here, and a core field never carries it (the
`presentation` extension does).

---

## 7. `world.transforms`

How this world relates to other frames: registrations, template spaces, other time bases, and
the further frames its own axes belong to (§3.2). Every such relation lives here, including
those an extension defines, so one fact has one place.

```json
"transforms": [
  { "to": { "reference": "nifti:mni152", "axes": [ { "id": "x", "type": "space", "unit": "mm", "positive": "right" }, ... ] },
    "on": ["x", "y", "z"],
    "forward": { "affine": [[1.02, 0, 0, -2.5], [0, 0.98, 0, 4.0], [0, 0, 1.01, 1.2]] },
    "metadata": { "software": "ANTs 2.5", "method": "affine" } },
  { "to": { "reference": "stimulus", "axes": [ { "id": "t", "type": "time", "unit": "s" } ] }, "on": ["t"],
    "forward": { "affine": [[1.0, -12.5]] } }
]
```

| Field | Meaning |
|---|---|
| `to` | The target frame: its `reference` (required; a bare token, like `stimulus` above, names a frame local to the array, §1), an optional `name`, and `axes` describing its axes as world-axis objects (§3.1). Without `axes` the target's coordinates are numbers whose units and directions are not stated; a writer states them. |
| `on` | The ids of this world's axes the transform acts on, in the order the transform takes them: an affine's columns follow `on`, and its rows follow `to.axes`. Absent: all of them, in the world's order. |
| `forward` | A transform object mapping this world's coordinates (on `on`) to the target's. |
| `inverse` | A transform object mapping the target's coordinates back. |
| `metadata` | Open provenance of the transform (the registration's software, method, error), keyed by application or standard. |

At least one of `forward` and `inverse` is present. The source is always this world. A
`to.reference` appears at most once with the same set of axes (an absent `on` being all of
them). A transform object has exactly one key naming its type:

| Type | Form |
|---|---|
| `identity` | `true`: the axes on `on` are the target frame's (§3.2) |
| `affine` | rows of an m × (n+1) matrix: n = the axes it acts on, m = the target's axes. Its `inverse` is implied only when m = n and the linear part is invertible; otherwise a writer that wants one states it. |
| `sequence`, `displacements` | reserved, with OME-Zarr 0.6's meaning; not defined in 2.0 |
| `<extension>:<type>` | defined by an extension (a FITS celestial projection) |

A reader that does not know a type keeps it unchanged and does not apply it; the file stays
valid. The type names and the row-form affine follow OME-Zarr's coordinate transformations; an
export writes each as OME's object, adding its `type` key and its input and output coordinate
systems. **One fact, one form:** a transform to a frame with the identity on every axis of this
world says that this world *is* that frame, which `reference` says; a writer states it as the
`reference` (spatial axes) and keeps identity transforms for the axes `reference` does not
cover (§3.2).
Group-level transforms relating one member array's world to another's are reserved, with a
`{ "path": … }` reference as 1.1's transform specification reserved it.

A transform only relates coordinates. `origin` and `step` place the array; `frame` gives the
basis of vector components; neither is a transform here.

**Sources are always this world.** 1.x's transform specification also allowed a transform from
the index grid or from its derived `axis-aligned` and `axis-aligned-centered` spaces; 2.0 does
not. A reader of such a 1.x transform composes it with the array's placement into a transform
from this world, which is exact whenever the placement is invertible, and reports the one it
cannot: fewer steps than world axes, or no `space_origin`. When a 1.x file states transforms to
one target from both `world` and a derived space, the one from `world` is kept and the other
reported. A 1.x transform acts on the spatial axes only (transform specification §8), so it
maps with `on` naming them. For 2.0 files this section replaces the transform specification;
its formulas for the derived spaces move to the implementer's guide, for reading 1.x (which
must settle the specification's two index formulas, `D·index + o` in its §2.1 and
`D·(index + c) + o` in its §2.2 table).

---

## 8. Resampling and derived arrays

- Only a dimension with a `step` may be interpolated. A `components` dimension never is:
  interpolating across it mixes different quantities. A dimension that states neither is not.
- Values are interpolated as quantities. An affine transform commutes with interpolation whose
  weights sum to one, so a reader may interpolate stored values under `linear` or `[]`; under
  `lut`, or `axis_linear` along the dimension being interpolated, it applies the transform
  first — except nearest-neighbor resampling, which moves stored values without mixing them and
  may keep them under any transform. A filter whose weights do not sum to one (a derivative, a
  sharpening kernel) does not commute with an intercept. With `transforms` absent, or of a type
  the reader does not know, the mapping is not known, and interpolating stored values is the
  reader's own assumption.
- A `label-map` (§2.2), and the binary labelmaps of the `seg` extension, are resampled
  nearest-neighbor only.
- Vectors and tensors are interpolated component by component in one basis; a reader brings them
  into the world's axes (§5.3) before resampling onto a grid with other directions. Without a
  `frame` the basis is unknown, and they may be resampled only onto a grid with the same
  directions.

**Derived arrays** (1.x §4.5, carried over): a tool that derives an array — resampling,
filtering, registration — writes what is true of the new array. It keeps the world (its axes,
`reference` and transforms) when the coordinates are unchanged, writes the new placement,
keeps a `thickness` only while it is still true, computes `values` and `intent` for the new
array (a smoothing whose weights sum to one keeps the unit; a derivative does not; `missing` stays only where no output sample mixes a missing value
with a measured one, as under nearest-neighbor resampling or a mask-aware filter, and is
otherwise dropped), and drops every source-record extension (`dicom`, `nifti`, `fits`,
`nrrd`), with its entries in `samples[i].metadata`: the array is no longer a re-encoding of that
source (§9). It drops, too, any extension
it does not know, since it cannot tell which of its fields remain true, and it adds its own
`provenance` step (§9). Registration changes the frame, and with it the `reference`.

Two cases are not derivations: **rewriting** the same
values on the same grid in the same convention version (rechunking, recompressing, changing the
store), which keeps every extension, known or not, and the **levels of a multiscale pyramid**,
which are one image at several resolutions
(each level states its own placement and its own `values` by the rules above — `missing`
only where no sample of the level mixes one — and keeps the image's known extensions, except
that source records, with their per-position entries in `samples`, stay only on a level whose
values and positions are the source's; an extension it does not know stays only on such a level,
since it cannot tell which of its fields remain true).

**Converting a 1.x file to 2.0 is not a rewrite.** Its extensions follow §2.3: a compatible one
is carried; one marked revision required is written in its 2.0 revision or, until that exists,
the file stays 1.x; one §17 does not list (an application's own, an unknown one) is carried
only if its own specification says it depends on no 1.x core field, and otherwise dropped and
reported.

## 9. Writers and converters

1.x §4.7, carried over: **every writer states only what is true of the array it writes, and
states it so it cannot be misread.** A writer that cannot vouch for a fact leaves it out. A
converter keeps a source extension only while the array faithfully re-encodes that source, and
makes that extension true of this array. What a writer optimizes for (compact storage, fidelity
to a source, compliance with a standard) is its own choice, and a tool documents the choice it
makes.

**No field contradicts what a reader gets from the file alone** — core or extension, a
statement or a source record. A source attribute stated in units the array no longer has (a
DICOM Pixel Padding Value in stored values beside an array of Hounsfield units) is left out;
what it meant is restated in the array's own terms where the core has a field for it
(`values.missing`, §6): a converter that knows the attribute restates it, where §6 allows. Where a writer cannot judge an attribute (a vendor's private element),
the extension states what the array holds, as the `dicom` extension's `stored_values` does, so
that a reader can.

**One source, one core header.** A converter writes each coordinate in the unit its source
states it in (DICOM's Trigger Time in `ms`, NIfTI's in its `xyzt_units`), and the world's axes in
the order and with the ids §3 gives, so that two converters of one source write the same core
metadata, up to the rounding of coordinates they compute (a coordinate copied from the source
is written as the source states it). Where a source allows two encodings of one geometry (a
regular series as a step or as positions), the extension's specification picks one. Extension
metadata and provenance are not held to this: a record is optional, and a step's `name` is the
writer's own words.

**A writer records itself.** Every 2.0 writer adds one step to the `provenance` extension's
`processing` list naming it: `{ "name": "<what it did>", "software": { "name", "version" } }`
(provenance §5.1); a converter also records what it converted as a `sources` entry, with at
least its `format` and, where known, an identifying field (provenance §7). A tool that writes an array from one that already has a `provenance` block
keeps its `sources` and `processing` steps — the input's lineage is the new array's lineage —
and adds its own step with no `inputs`, which provenance §5.1 reads as operating on the previous
step's output. A new input it also consumes (a second image, an atlas) is appended to `sources`,
so that earlier indices stay valid, and named in the step's `description`: provenance 1.0 has no
way for one step's `inputs` to name both the previous output and a source (§19). It is how
a reader recognizes a file from a writer later found defective (§14). The rest of the extension
stays optional. The rules of this section bind writers (conformance); a file that misses one is
still read, and a reader may report it.

## 10. Consistency rules

1. `origin`, every `step`, every `samples[i].origin` and every vector in `samples[i].steps` have
   one component per world axis; a `step` or an `origin` requires a `world`, and a `world` has
   `axes`. `dimensions` has one entry per entry of Zarr's `shape`. `version` is present (a file
   with `world`, `origin`, `dimensions` or `values` and no `version` is neither 1.x nor 2.0).
2. A dimension has at most one of `step` and `components`; `centering`, `thickness`,
   `samples[i].position`, `.origin`, `.steps` and `.thickness` belong to a dimension with a
   `step`; `color_space` and `frame` to one with `components`.
3. The steps are linearly independent.
4. A `components` kind's fixed size matches the dimension's length in `shape`.
5. World axis ids are unique, and so are sample ids along a dimension. `positive` appears only
   on `space` axes, at most one term of each pair. No step spans spatial axes of different
   units, and a world whose spatial axes differ in unit has no `frame` (units compared as
   written, §3.1).
6. `frame` is square and invertible, one row and column per spatial world axis, on a kind that
   is spatial in this world (§5.2, §5.3).
7. A `color_space` fits its kind (§5.3).
8. A transform's `on` names axes of this world, each once; its affine has one column per such
   axis plus one, and one row per target axis; a `to.reference` appears at most once with the
   same set of axes (the order of `on` does not matter, and an absent `on` is every axis).
9. A world axis's `type`, where given, is a token (§1).
10. `samples`, where present, has one entry per position along its dimension; no entry has both
    `position` and `origin`; a sample with `steps` has `origin` (so that its dimension's own
    offset is never read two ways); at most one dimension's samples carry `origin` or `steps`;
    each `samples[i].steps` has one entry per dimension, `null` exactly where a dimension has no
    `step`.
11. An `id` is a token; a `reference` is a bare token or `<prefix>:<value>` with a token prefix
    and a non-empty value (the value's own grammar, such as a DICOM UID's, is checked by readers
    of that extension, which report a value that breaks it; it is not validity). A transform's
    `to` has a `reference`.

**A file that breaks a rule is invalid.** A reader refuses its duckn metadata rather than guess
which part to trust, and may still give access to the Zarr array's raw values. These are not
validity: a value transform the reader cannot apply (an unknown type, malformed parameters,
a `lut` that is empty or not first) makes only the quantity unknown (§6), and a `missing`
stated where §6 forbids it is ignored; an extension block that breaks its own specification is
handled as §2.3 says; and a writer's obligations (§6's encodings, §8's derivation rules, §9)
are conformance. A unit that does not fit its axis's type is reported, not invalid (§3.1).

## 11. What a reader derives

- **The index-to-world affine**: its columns are the steps, its last column the origin (for a
  regular grid; `samples` override it per position).
- **The grid's cells and each sample's measured interval** (§5.1).
- **LPS or RAS, and handedness**, from `positive` (§3.1), through one function per language
  (Python in duckn, JS in `duckn-spatial`): the permutation and signs taking this world's
  spatial axes to LPS. A world whose spatial axes lack `positive` has no orientation, and the
  function says so.
- **What each axis and dimension is**: an axis from its `type` (§3.1), a dimension from its step.

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
      { "id": "x", "type": "space", "unit": "mm", "positive": "left" },
      { "id": "y", "type": "space", "unit": "mm", "positive": "posterior" },
      { "id": "z", "type": "space", "unit": "mm", "positive": "superior" }
    ]
  },
  "origin": [-250.0, -250.0, -400.0],
  "dimensions": [
    { "step": [0, 0.26, 2.49], "centering": "cell", "thickness": 3.0 },
    { "step": [0, 0.98, 0], "centering": "cell" },
    { "step": [0.98, 0, 0], "centering": "cell" }
  ],
  "values": { "unit": { "symbol": "HU", "scheme": "UCUM", "code": "[hnsf'U]" },
              "transforms": [ { "name": "linear", "parameters": { "slope": 1, "intercept": -1024 } } ] }
}
```

### 13.2 fMRI with slice timing

Volumes are instants (node); each slice is 3 mm higher and 0.055 s later than the one below. The
source states the signal in arbitrary units, so `[arb'U]` is written (§6). This and the
examples after it are fragments: `version` is as in §13.1, and so is `world` where a fragment
shows none.

```json
"world": {
  "axes": [
    { "id": "x", "type": "space", "unit": "mm", "positive": "right" },
    { "id": "y", "type": "space", "unit": "mm", "positive": "anterior" },
    { "id": "z", "type": "space", "unit": "mm", "positive": "superior" },
    { "id": "t", "type": "time", "unit": "s" }
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
"world": { "axes": [ { "id": "x", "type": "space", "unit": "um" }, { "id": "y", "type": "space", "unit": "um" } ] },
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
  { "step": [5.0, 0, 0], "thickness": 5.0 },
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

Frames of unequal length are cells: each position is its frame's middle, as a multiple of a
one-second step from the injection (`origin`'s time 0), and `thickness` is its duration. The
activity is stored with one slope per frame. (Frames whose middles are evenly spaced take a step
of that spacing instead, from the first frame's middle, with `thickness` their length.)

```json
"world": { "axes": [ { "id": "x", "type": "space", "unit": "mm", "positive": "left" },
                    { "id": "y", "type": "space", "unit": "mm", "positive": "posterior" },
                    { "id": "z", "type": "space", "unit": "mm", "positive": "superior" },
                    { "id": "t", "type": "time", "unit": "s" } ] },
"origin": [-150.0, -150.0, -100.0, 0.0],
"dimensions": [
  { "step": [0, 0, 0, 1.0], "centering": "cell",
    "samples": [ { "position": 5, "thickness": 10 }, { "position": 15, "thickness": 10 }, { "position": 40, "thickness": 40 } ] },
  { "step": [0, 0, 2.0, 0], "centering": "cell" },
  { "step": [0, 2.0, 0, 0], "centering": "cell" },
  { "step": [2.0, 0, 0, 0], "centering": "cell" }
],
"values": { "unit": "Bq/mL", "transforms": [
  { "name": "axis_linear", "parameters": { "dimension": 0, "slope": [1.10, 1.12, 1.15], "intercept": 0 } } ] }
```

### 13.7 Fluorescence channels

```json
"world": { "axes": [ { "id": "x", "type": "space", "unit": "um" }, { "id": "y", "type": "space", "unit": "um" },
                    { "id": "z", "type": "space", "unit": "um" } ] },
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

A 2.0 reader maps every 1.x file in one function; writers write 2.0. A file with no `version`
is a 1.0 file (1.2 §3.1), unless it has one of 2.0's own fields (§2.1).

| 1.x | 2.0 |
|---|---|
| `space: "left-posterior-superior"` (or `LPS`) | `world.axes` `x`, `y`, `z`, each `type` `space`, `positive` `left`, `posterior`, `superior` |
| `right-anterior-superior`, `left-anterior-superior` (RAS, LAS) | the matching terms |
| a `-time` space | the three, plus `{ "id": "t", "type": "time" }` with the 1.x time axis's `unit` when a dimension steps along it or `space_origin` states a time (its fourth component, a local zero); with neither, no time axis is added (it would claim one time point, §1) |
| `scanner-xyz`, `3D-right-handed`, `3D-left-handed`, the general 3D names | `x`, `y`, `z` of `type` `space` without `positive`; a stated handedness is reported as not carried over |
| `space_dimension: n` | n axes without `positive`, ids `x`, `y`, `z`, then `a3`, `a4`, …, of `type` `space`, except in a file with a `fits` extension block, whose axes take no type (its sky axes are angles in a linearized projection, not spatial; reported) |
| a spatial axis's `unit` | each spatial world axis takes the unit of the spatial dimensions whose `space_direction` has a non-zero component along it, normalized as §3.4 lists; one no spatial dimension steps along takes the unit all spatial dimensions share, if they share one. Different units on different world axes are legitimate (§3.1); a file is refused only when one world axis would take two units (a step across axes in different units) |
| `space_origin` | `origin` |
| `axes` | `dimensions` |
| `space_direction` | `step` (extended with 0 on any world axis the 1.x space did not have) |
| `kind: "domain" / "space" / "time"` with a direction | implied by the step |
| `kind: "time"` with per-sample times and no direction | the dimension steps by one unit along the time axis the `-time` row added, when there is one, each sample's position its time less `origin`'s time component; otherwise along a world axis `{ "type": "time" }` added for it (id `t`, or `t1`, … if taken), in that axis's `unit` (none if it states none), `origin`'s component 0 (a local zero), each sample's position its time |
| `kind: "time"` with no times | a dimension that states nothing; no time axis is added |
| `kind: "domain"` with per-sample positions, a unit and no direction (a NIfTI spectrum in `Hz`, `ppm` or `rad/s`, as duckn 0.6.1 writes it) | a world axis added the same way: `[ppm]` of `type` `chemical-shift`; `Hz` or `rad/s` of `type` `frequency`, which has a local zero (§3.1); added after the world's other axes (§3's order is for converters; a mapped file keeps its own) |
| `kind: "domain"` with neither positions nor direction | a dimension that states nothing |
| `kind` of a range kind | `components`, the same word; a 1.x `3-vector` that a `measurement_frame` governs becomes `vector` (spatial in a 3-axis world), so the frame is carried; the DWI dimension of a `dwmri` file (`vector` or `list` in dwi 1.0) becomes `list` |
| `measurement_frame` | `frame` on each spatial components dimension, its spatial block when the 1.x space has time (a 4 × 4 frame's first 3 × 3). In a `dwmri` 1.0 file it governs the gradients, which the 1.x block keeps (read under 1.0, §2.3) until `dwmri` 2.0 gives them a `frame` of their own: by `gradient_frame` (dwi §4.1), `measurement` → the frame, and an absent frame is the identity there (dwi §6); `world` → the identity; `image` → not carried, and reported: dwi 1.0 defines it as FSL's `bvec` convention, whose first component FSL flips when the image's determinant is positive, and a 1.x file does not record whether its converter applied the flip, so the gradients' frame is unknown. A frame the file does not use for either is reported as not carried over; a 4 × 4 frame that couples time and space is reported (the coupling is not carried). **A frame in a file that declares 1.0 is ambiguous:** the transform specification says 1.0 wrote columns, while duckn's NRRD converter from 0.5.5 wrote rows labeled 1.0 (and through 0.5.4 wrote the transpose). A symmetric one reads either way; a non-symmetric one is not carried over but reported, for the file to be converted again from its source, and where it governed `dwmri` gradients (`gradient_frame` `measurement`) the gradients' frame is then unknown: a reader does not fall back on dwi 1.0's identity default. |
| `samples[i].position` (a distance) | `samples[i].position` divided by the spacing |
| `samples[i].position` on a 1.x time axis that has a direction (a time in the axis's unit, 1.x §3.2) | its time less `origin`'s time component, divided by the step's time component |
| `samples[i].position` and `.origin` together | `samples[i].origin` |
| `samples[i].directions` (spatial directions only) | `samples[i].steps`: each dimension's direction extended with 0 on non-spatial world axes, `null` for a dimension without a step; with `samples[i].origin` from the 1.x file, or, where it gives none, computed from the nominal placement (`origin` plus the sample's `position` or index times the step) |
| `centering` on an axis of length 1 | dropped (§5.1) |
| `sample_units` | `values.unit`, normalized as §3.4 lists (`HU` → `{ "symbol": "HU", "scheme": "UCUM", "code": "[hnsf'U]" }`) |
| `value_transforms` (absent in a 1.0/1.1 file, or one with no `version`) | `values.transforms: []` (they meant identity) |
| `value_transforms` (absent in a 1.2 file) | absent (1.2 meant "not stated") |
| `axis_linear`'s `axis` | `dimension` |
| a UDUNITS unit with a reference date (`hours since 2020-01-01`) | the unit alone (`h`); the reference date is reported as not carried over (§3.1) |
| `space_transforms` from `world` | `world.transforms`, with `on` naming the spatial axes and with its `metadata` (1.x applied it to the nominal positions, not per-sample ones: a file whose samples override positions is reported); an identity to one reference on every spatial axis becomes `world.reference` (§7) when it is the only such identity; otherwise each stays a transform |
| `space_transforms` from `index`, `axis-aligned`, `axis-aligned-centered` | composed with the placement into a transform from this world (§7), with the nominal placement 1.x defined them by (its steps and `space_origin`, ignoring per-sample positions; a file whose samples override positions is reported) |
| an extension's own `space_transforms` | `world.transforms`, the target qualified by the extension's name |
| a 1.x target `to.name` | `to.reference`: a prefixed name as it is, a bare name as a reference local to the array (§1; 1.x also let a container's arrays share it, which is reported as not carried), its characters outside the token set replaced by `_` (reported) |
| a 1.x target's `axes` (`kind`, `unit`) | world-axis objects: `kind` `space` → `type` `space`, `time` → `time`, the unit normalized |
| `thickness`, `color_space`, `intent`, `unit_systems`, `centering` | unchanged, except that an `XYZ-color` axis in an unsigned integer type under identity (1.2 read its components as fractions of the type's maximum) gains a `linear` of slope 1 / maximum, reported |
| `extensions` | read under their own versions (§2.3) |
| anything that breaks the rules of the 1.x version the file declares | read as that version's rules say (a part it refuses is refused), and reported; not repaired |

**Files from duckn 0.6.0 and earlier converters.** The mapping carries what a 1.x file says, and
four converters said wrong things until duckn 0.6.1: an irregularly spaced DICOM series' sample
positions were absolute coordinates along the slice normal, not distances from the origin; a 4D
DICOM series with only Temporal Position Identifier had those indices labeled milliseconds; a
NIfTI vector or tensor file (components in the 5th dimension) gained a one-sample time axis and
lost its components' kind, and a spectrum in `Hz` or `ppm` became time; an NRRD's `space units`
were given to spatial axes by position, not by the world axis each steps along. A fifth is older:
through 0.5.4 duckn's NRRD import stored the measurement frame transposed. A reader cannot
detect these: duckn's 1.x converters record nothing about themselves (haversack's input
copies do, in their own `extensions.haversack`; nothing general reads it). They are
re-converted from their sources, not repaired in the mapping. 2.0 writers record themselves
(§9), so the next such defect can be recognized.

## 15. Correspondence

### 15.1 NRRD

| NRRD | 2.0 |
|---|---|
| `dimension`, `sizes`, `type`, `encoding`, `endian` | Zarr's `shape`, `data_type`, `codecs` |
| `space` | `world.axes[].positive`; export permutes and flips to one of NRRD's three orientation names |
| `space dimension` | the number of `world.axes`, each of `type` `space` (without a `space` name, ids `x`, `y`, `z`, … and no `positive`) |
| `space units` | `world.axes[].unit`, one per world axis |
| `space origin` | `origin` |
| `space directions` | `dimensions[].step` |
| `measurement frame` | `dimensions[].frame` (transposed: NRRD writes columns) |
| `kinds`: domain kinds | a `step`, where `space directions` or `spacings` give one; otherwise a dimension that states nothing |
| `kinds`: range kinds | `components` |
| `centers` | `centering`; an axis with none is `cell`, NRRD's default, where the origin is computed from `axis mins` (the assumption is written, not left implicit), except on a length-1 axis, which takes no `centering` (§5.1) |
| `thicknesses` | `thickness` |
| `spacings`, `units`, `axis mins` of a domain axis with no `space` (and of one outside `space directions` in a file with `space`, whose world axis follows the spatial ones) | a world axis per such dimension, its `type` from the axis's `kinds` entry (`space` → `space`, `time` → `time`; `domain` states no type, so none is written, whatever the unit: §3.1 never infers one), in that unit (none if unstated), stepped along by the spacing; the world's axes as §3 orders them (kinds `space` and `domain` first, then `time`, each in NRRD's axis order, fastest first), ids `x`, `y`, `z`, then `a3`, `a4`, … for the first group and `t`, `t1`, … for time; with `axis mins` and `axis maxs` and no `spacings`, the spacing is (max − min) / size for a cell-centered axis and (max − min) / (size − 1) for a node-centered one; `origin` from the axis mins when every such axis states one, otherwise no `origin` (the steps still give the geometry, §4). An axis min is the first sample's position on a node-centered axis and the lower edge of the first cell on a cell-centered one (NRRD's own rule; an unstated centering is NRRD's default, cell), so a cell-centered axis's origin component is its min plus half the spacing |
| `labels` | Zarr's `dimension_names` |
| `old min`, `old max`, `min`, `max`, `content`, `units` of a range axis | the `nrrd` extension |
| key/value pairs | the `keyvalues` extension |

### 15.2 OME-Zarr 0.6

| duckn 2.0 | OME-Zarr 0.6 |
|---|---|
| `world` | a coordinate system `{name, axes}`; `world.reference`, or `"world"`, becomes its name |
| a world axis's `id`, `unit` | an axis's `name`, `unit` (UDUNITS-2 spelling on export: `mm` is `millimeter`) |
| a world axis's `type` | the axis `type`: `space`, `time`, or the custom type OME allows |
| `positive: "left"` | nothing in 0.6; the anatomical orientation proposed in OME's RFC-4 (`{type: "anatomical", value: "right-to-left"}`), where it is adopted |
| `origin` and axis-aligned steps | a level's `scale` and `translation` |
| oblique steps | an array-aligned coordinate system (scale, translation), then an `affine` to the world's |
| `world.transforms` | `coordinateTransformations` from this system to the target; an `on` subset is a `byDimension` |
| a `components` dimension | an axis of `type: "channel"`, discrete, in the same coordinate system |
| `centering` | OME 0.6 fixes the pixel center at the coordinate: `cell`. A `node` grid has no OME form. |
| `values`, `components` meaning, `frame` | nothing: these stay duckn's |

OME-Zarr has limited an image's number, kind and order of axes (0.4: 2–5 axes, at most one
channel, time/channel/space order); whatever limit the version written states, an array outside
it has no OME view. OME metadata sits on an image
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
- **An axis `type`, stated (revision 7).** Revisions 3–6 let the unit say what an axis measures;
  review round 4 found a wavelength in `nm` read as a spatial axis, and a unit in a scheme one
  reader knows and another does not making the same axis spatial for one and unknown for the
  other. OME-Zarr states an axis `type` for the same reason. A unit that does not fit its type is
  reported, not invalid (revision 9), for the same reason: validity may not depend on how much
  UCUM a reader parses.
- **Value transforms and world transforms keep different shapes.** A value transform is
  `{ "name", "parameters" }`, as in 1.x, so 1.x value transforms carry over unchanged; a world
  transform follows OME-Zarr's shape (§7). They are different things, and the names do not
  collide.

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

Each row says whether the 1.x version may be carried unrevised in a 2.0 file (*compatible*) or
must be revised first (*revision required*, §2.3). No 2.0 file is written until the required
revisions exist as documents; this table is what each must say.

| Extension | Revision | What changes |
|---|---|---|
| `dwmri` | 2.0, revision required | A `frame` beside `gradients`/`b_matrices`, defined as in §5.3 (gradients F·g, b-matrices F·B·Fᵀ; absent means unknown), replacing `gradient_frame`; the `vector` DWI dimension becomes `list`; phase-encoding directions name a dimension; the §3 interleaving table (which read axis order fastest-first) is withdrawn. Required before any 2.0 DWI file: an unrevised 1.0 block would read a lost measurement frame as identity. |
| `seg` | 0.9 for an array's block and 0.10 for a group's, unchanged in version (a 0.x version is read only as itself, §2.3, so a wording revision takes no new number); compatible | "a `list` axis" → "a dimension with `components: \"list\"`"; binary labelmaps write `values.transforms: []`; no field changes. |
| `microscopy` | 2.0, revision required | `timestamps` and `z_positions` move to core `samples[i].position` (they are absolute stage positions and acquisition times: the origin's component is subtracted and the step divided out, and times take a local zero); channel `color` becomes a CSS string (as `seg`); §1 against OME 0.6; spectral bins are a world axis (a wavelength in `nm`, each bin at its stated center) when every bin states its wavelength, and a `list` otherwise; FLIM microtime bins are a `list` (decision 4: `type` could now tell a microtime axis from acquisition time, but microscopy 2.0 keeps them a list until an application needs them as an axis). |
| `nifti` | 2.0, revision required | the world from sform/qform (codes 1–5 are RAS), a differing qform as a `world.transforms` entry to a local reference (`qform`), `xyzt_units` onto world axes, `toffset` onto the origin, a 4th dimension that is time only when its unit is a time (a `ppm` unit makes it a `chemical-shift` axis, a `Hz` or `rad/s` unit a `frequency` axis, which has a local zero, §3.1), `dim_info` kept in the record, spatial dimensions `cell` and volumes `node` (NIfTI's voxels and time points; slice times are measured from the volume's time), intent codes onto `intent` and `components`: a vector or matrix intent's components are the 5th dimension, as nifti1.h lays them out, with a length-1 4th dimension that states nothing, or the 4th dimension in the four-dimensional layout some tools write. **Slice timing lives in the geometry, not also in the extension:** a regular order (`slice_code` sequential or interleaved, with `slice_duration`) becomes per-slice time origins on the slice dimension (or, when sequential, a step through space and time, §5.1), and irregular times (a BIDS `SliceTiming` list) become per-slice time origins, each slice at its acquisition instant (§5.1); the header's `slice_code`, `slice_start`, `slice_end` and `slice_duration` are then not repeated. A 3D file with a time unit keeps the unit in the extension and adds no time axis (an axis would claim one time point, §1). References: xform codes 3 and 4 name `nifti:talairach` and `nifti:mni152` (codes 1, 2 and 5 name no shared frame - a scanner, another file, some template - and write no `reference`); `nifti:mni152` means only "the header says an MNI 152 template", and finer names (`nifti:mni152nlin2009casym`) are optional, for a writer that knows the variant. |
| `dicom` | 1.1, revision required (it defines the `dicom:` reference; a 1.0 reader of a 1.1 block misses only additions, since the changes below either add or restate 1.0 in 2.0's names) | Frame of Reference UID → `world.reference` (`dicom:<UID>`), and it stays in `tags` as the source record (§2.3; the `frame-of-reference` group keeps both its keywords); the Synchronization Frame of Reference UID stays in `tags` only, and becomes an identity transform on the time axis to `dicom:<UID>` only when that axis is measured from that frame's zero (§3.2); a new section defining the `dicom:` reference (a UID, digits and dots, at most 64 characters; it covers the spatial axes as `world.reference` and the axes `on` names in a transform); a single Pixel Padding Value restated as `values.missing` (§6) under either policy where `transforms` is `[]` or `linear` (with a Pixel Padding Range Limit, or under `axis_linear`, `missing` is left out, not written `[]`), and kept in `tags` only while `stored_values` is true; Slice Location kept per slice as 1.0 §6.1 keeps it (its zero is unstated, so the core does not state it); Acquisition Time is when an acquisition started: a converter that knows the duration (PET's Actual Frame Duration) places the frame at its middle with that `thickness` — frames whose middles are evenly spaced as a step of that spacing from the first frame's middle, others as positions on a step of one time unit (§13.6) — and otherwise writes the time with no `centering` (§5.1); times in the source's unit (`ms` for Trigger Time, an instant, so a dimension of trigger times is `node`; `s` for times of day converted from their first frame); per-slice tags may sit in `samples` on a regular dimension (1.0 §6.2 said `samples` are omitted there); per-frame geometry onto `samples`; time only from real times; varying rescale onto `axis_linear`. **Unchanged from 1.x:** tags describe the source, not the array (§10.1); the §5 groups by name (0.6.2) and redaction as `null` with `anonymized: true` (§4.3); `stored_values` (true: the array holds the source's stored values, the mapping in `values.transforms`; false: the array holds the Modality stage's output, the quantity, encoded as `values.transforms` says) and the rule that nothing in stored-value units is written of values that are not stored values (§5.10); per-slice tags in `dimensions[i].samples[j].metadata.dicom`. The §2 table of excluded attributes names 2.0's fields: `origin`, `step`, `thickness`, `values.transforms`, `values.unit`, a dimension's `color_space`. |
| `fits` | 2.0, revision required | the world is FITS's *intermediate* world coordinates (exact, linear; zero at the reference point, and for celestial axes coordinates in the projection plane, not offsets in right ascension), its axes of no type or of the type the extension states, all with local zeros (§3.1); CTYPE/CUNIT/CRVAL/PV on world axes, and the CDELT/PC split and CRPIX kept for export only (the core states their product and the origin, and a reader never takes geometry from them); the celestial projection as an extension-defined transform type to `fits:icrs`; a unit table to UCUM. |
| `nrrd` | 0.2, revision required | `spacings` of a no-space file become world axes (§15.1), reversing 0.1's rule that no world is inferred, and the extension records that the source had no `space` (`"no_space": true` in its top-level block, and no per-axis spacing or minimum, which the core states), so an export writes `spacings` and `axis mins` again; `space units` map one per world axis (export writes one entry for every world axis, including one no dimension steps along, as NRRD requires); range-axis `units` and a scalar file's measurement frame stay in the extension. |
| units spec | folded into §3.4 | the UCUM reading on world axes, the string-or-object form, `unit_systems`, the normalization table; the spec stays for 1.x files. |
| transform spec | folded into §7 | 2.0 transforms start from this world; the derived spaces' formulas move to the implementer's guide, for reading 1.x. |
| `provenance` | 1.1, compatible | the writer's own `processing` step becomes required of 2.0 writers (§9); nothing else changes. A way for a step to take the previous step's output and a new source together is open (§19). |
| `keyvalues` | wording, compatible | none. |
| `presentation` | wording, compatible | windows are in the quantity, after `values.transforms`, under any stated mapping (presentation §2.2); with `transforms` absent and no `unit` they are in the stored values; with a `unit` but no `transforms` they cannot be applied. |

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

**Revision 6 (2026-09-29).** A review of the draft against what 1.x settled after it branched
(duckn 0.6.1 and 0.6.2, and the DICOM tag rules of 0.5.3–0.5.4), and the owner's answers to
§19. Answered: a source record beside a core fact (§2.3); shared time bases only from a shared
zero (§3.2); the units specification folded in (§3.4); 1.2's value rules carried over (§6); the
transform sources and the transform specification (§7); "no field contradicts the file" and the
writer's record of itself (§9); the `seg` revision number, the `dicom` carry-overs, and the
decided `nifti`, `nrrd` and `microscopy` rows (§17); the older-converters note (§14).

**Round 4 (revision 6).** Six new scenarios (a CT preserved and materialized, a cardiac cine,
a NRRD with no space, interleaved fMRI slice timing, a resampled derivative), two writers and two
blind readers, plus a review of the whole draft against the released 1.x documents and code.
Both writers wrote the same core headers for all six, equal to the reference headers but for
choices the draft left open; both readers recovered every stated position, extent, time and
value, read the two 1.x files through §14 correctly, and found the planted contradiction (a
stored-unit padding value beside Hounsfield units). Answered by revision 7: references in an
extension's own grammar (DICOM UIDs have dots) and covering the spatial axes; bare references
local; a stated axis `type`; mixed spatial units allowed but not mixed; `version` required;
`values.missing`; when a time is geometry, onsets, `samples[i].origin` as a full point, spacing
and thickness of a space-time step; matrix kinds spatial by dimension; color encoding as 1.2
had it; malformed value transforms not invalidating the file; 1.2's interpolation, derivation,
rewrite and pyramid rules; the stored-unit rule as dicom-spec §5.10 has it; provenance steps
kept; conformance separate from validity; extensions compatible or requiring revision; the
`presentation` row corrected; the §14 table completed (ids, types, time units, `gradient_frame`,
1.0 frames, directions, reference dates, target axes, identity promotion); §15 and OME wording.

**Round 5 (revision 7).** Four more scenarios (a spectral image, a NRRD in two spatial units, a
materialized CT's padding, a cardiac cine whose trigger times start at 20 ms), the same two
writers and two readers, and a review of revision 7. The writers again wrote the same core
headers, equal to the reference headers; the readers again recovered every number and read the
1.x files correctly. Answered by revision 8: time positions are where samples sit (revision 7's
"onsets" contradicted the centered thickness and the PET example); only space and time have
frames, and 500 nm is 500 nm everywhere; `type` optional with absent unknown, and when a unit
fits it; an affine's columns follow `on`; reference values checked by their extension, not
validity; conversion from 1.x is not a rewrite; `values.missing` bounded (not under
per-index or many-to-one mappings, not through mixing derivations); color and XYZ components;
length-1 steps take the thickness; the source-record test by what the converter mapped; the
§14 and §15.1 gaps (1.0 frames not carried, dwmri's DWI dimension, time positions, ambiguous
names, 1.x files that break 1.x rules).

**Round 6 (revision 8).** The ten scenarios again, one writer, one blind reader, and a review of
revision 8 against the 1.x documents and duckn's converter. The writer's core headers equaled the
reference headers; the reader recovered every number and found both planted faults (a `missing`
kept through a linear resample; an `axis_linear` list one short). The review found what the
scenarios did not reach. Answered by revision 9: extension versions (a 0.x version is read only as
itself; revision 8 read a later 0.x under an earlier one, nrrd 0.2 as 0.1, and an older breaking one under a later); a unit that does not fit its type reported, not
invalid (revision 8 made NIfTI's own `rad/s` conversion invalid, and validity depend on a
reader's UCUM parser); a dropped 1.0 measurement frame leaves `dwmri` gradients in an unknown
frame, not the identity; color components kept by every encoding (a `uint8` `[]` re-encoded as
`float32` had read 255 as the component); bare references local to the array (a group-shared
bare token made every NIfTI's `qform` one frame); other types may have frames an extension
defines; Slice Location kept, as 1.0 kept it; the source-record test restated (identifiers and
acquisition facts kept); provenance steps without a mechanism provenance lacks; NRRD types from
`kinds`, not units; `missing` limited to `[]` and `linear`, ignored where misplaced; pyramid
levels state their own `values`; examples §13.6 and §13.7 with their own worlds; §10 rules 5
and 8, the 1.x `-time` axis reused, `gradient_frame` `image` in dwi's order, identity promotion,
NIfTI's non-finite slope and intercept, displacement and velocity units, time positions with no
centering.

**Round 7 (revision 9).** The ten scenarios with one writer, and a review of revision 9's
changes. The writer's core headers equaled the reference headers again, with no reading of a
position, time or value in doubt; what it found were places where two writers could still differ.
Answered by revision 10: units compared as written (revision 9 moved the fit out of validity but
left rule 5's "different units" to each reader's parser); the color exception stated in §6
itself, and 1.2's integer XYZ carried by a `linear`; `gradient_frame` `image` not carried (FSL
flips the first component when the determinant is positive, and a 1.x file does not say
whether its converter did); an absent displacement unit unknown, not a default; the exact
`missing` computation (a fused multiply-add changes it); a file with `world` and no `version`
invalid; the source-record list closed per extension; FITS axes as offsets from CRVAL; NIfTI and
1.x spectra in `Hz` untyped; 1.x FITS angles typed `angle`; non-parallel slices' steps in
`samples[i].origin`; one source, one header (units as the source states them, axis order,
`sources`, NIfTI centering, PET frames); `seg` keeps 0.10; pyramid levels and unknown extensions;
§13.6's origin and unequal frames.

**Round 8 (revision 10).** One writer and a review of revision 10. The writer's core headers
equaled the references again. The review found four rules that gave wrong positions or times,
two of them revision 10's own. Answered by revision 11, which simplifies rather than adds: only
`wavelength` and `chemical-shift` compare across files, every other axis — angles and
frequencies included — having a local zero unless a frame is stated (revision 10 had made 1.x
FITS sky axes comparable with no frame, and exempted FITS axes through an extension a reader
may not know); PET frames placed by the spacing of their middles, not their length; a sample
with `steps` has `origin`; 1.x registrations on per-sample positions reported; a file with any
2.0 field and no `version` invalid; unit sameness scoped to validity; the source record's
contents left to each extension, with DICOM's own exclusions and NIfTI's slice timing named;
one source, one *core* header, with axis ids; `missing` through re-encodings and the color
exception; FITS intermediate coordinates described correctly; `seg`'s versions; §19 lists
revision 10's choices.

## 19. Decisions

Settled 2026-09-29, the owner agreeing to each recommendation:

1. **Transforms from derived spaces** are dropped; a 1.x one is composed into a transform from
   this world, and one that cannot be is reported (§7).
2. **NRRD `spacings` without `space`** become world axes (§15.1).
3. **Microscopy spectral bins** are a world axis when each bin states its wavelength, a `list`
   otherwise (§17).
4. **FLIM microtime** is a `list` (§17).
5. **NIfTI slice timing** lives in the geometry only; a 3D file's time unit stays in the
   extension (§17).
6. **Template names**: `nifti:mni152` is generic; finer names are optional (§17).
7. **The units and transform specifications** are folded in (§3.4, §7).
8. **The writing software** is recorded in the `provenance` extension, as 1.2 decided, by every
   2.0 writer (§9).
9. **A DICOM identifier the core states** stays in the extension's record (§2.3).
10. **A shared time base** is stated only when the times are measured from its zero (§3.2).

**Choices revision 7 made, for the owner's review** (each answers a round 4 finding, and each
could be reversed):

11. **A stated axis `type`** (`space`, `time`, or another word), replacing "the unit decides"
    (§3.1); optional, absent meaning unknown (revision 7 made it required; round 5 found every
    unitless NRRD axis then invalid). Alternative: keep the unit rule and add only a way to say
    "not spatial".
12. **`values.missing`**, the quantity's no-measurement values (§6), so that materializing a CT
    keeps what its Pixel Padding Value meant. Alternative: no core field; padding lost on
    materializing.
13. **One `dicom:` prefix for both DICOM frames**, with `world.reference` covering the spatial
    axes and a transform's `on` covering the rest (§3.2), replacing the `dicom-sync:` prefix.

**Choices revision 9 made, for the owner's review:**

14. **A bare reference is local to the array** (§1), not shared by a Zarr group's members that
    use the same token; sharing a bare frame is reserved with the group-level `world`.
    Alternative: group-shared, with converters forbidden fixed tokens such as `qform`.
15. **A NRRD `domain` axis gets no `type`** (§15.1): only `kinds` `space` and `time` state one,
    so a no-space NRRD in `mm` reads as axes of unknown type, still placed and resampled, but
    with no spatial vectors, no `frame`, and no OME-Zarr view (OME requires two or three space
    axes).
    Alternative: a length unit on a `domain` axis implies `space`, the inference revision 7
    removed.
16. **A unit that does not fit its type is reported, not invalid** (§3.1), and `frequency` takes
    `rad/s`. Alternative: a fixed list of units per type, checked as validity.

**Choices revisions 10 and 11 made, for the owner's review:**

17. **Only `wavelength` and `chemical-shift` compare across files**; every other axis has a local
    zero unless a frame is stated (§3.1). Alternative: every type but space and time compares
    directly (revision 8), which needs extensions to exempt their axes.
18. **An absent displacement or velocity unit is unknown** (§2.2), so a unitless ITK or NRRD
    displacement field has no magnitudes until one is stated. Alternative: the spatial axes'
    unit by default.
19. **`gradient_frame: image` is not carried from 1.x** (§14): FSL's first-component flip is
    unrecorded. Alternative: carry it, assuming the flip was not applied.
20. **Units compared as written for validity** (§3.1): `um` against `10*-3.mm` across one step
    makes a file invalid. Alternative: compare resolved units, validity then depending on the
    reader.
21. **One source, one core header** (§9): source units, axis order and ids fixed for
    converters. Alternative: leave them to the writer, headers then differing in form.
22. **`missing` compared exactly by one stated computation** (§6), fused multiply-add excluded.
    Alternative: compare in the stored domain.

**Open:** a provenance step that takes the previous step's output and a new source together (§9)
needs a provenance revision; until then the new source is named in the step's `description`.

Implementation order, agreed with the answers: duckn reads 1.x and 2.0, and keeps writing 1.2
until the extension revisions §17 marks required exist; then it writes 2.0.
