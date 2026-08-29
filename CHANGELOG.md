# Changelog

## Unreleased

### Fixed — calibration integrity across readers, writers, and adapters
- `to_sitk`/`to_vtk`/`to_nifti` emit calibrated values (since 0.1.7), but
  the matching `from_*` wrapped those values as `Volume.raw` while keeping
  the caller's `value_transforms` — so a round trip applied the chain
  twice and shifted a CT volume by exactly its intercept, 1024 HU,
  silently. The `from_*` side now drops the transforms per §4.3.
- `RescaleSlope`/`RescaleIntercept`/`RescaleType` were read from the first
  instance and asserted over the whole stacked array. Per-instance
  variation is real (PET, NM), and every other slice was silently wrong by
  the difference. All three extraction sites now require agreement and
  otherwise warn and record no calibration — uncalibrated values are
  wrong-looking, where a misapplied intercept is plausibly wrong.
- `zarr_to_nrrd` wrote stored values under a `sample units` header with
  nothing recording the rescale. NRRD's `old min`/`old max` is not a
  substitute: it records the range the data spanned *before* a
  quantization step rather than stating a mapping, so the forward
  direction is implicit in the storage type rather than in the metadata,
  encoding a transform into it would mean synthesizing an original range
  the data never had, it cannot express a `lut`, and most readers treat it
  as informational. The writer materializes instead. This also settles a
  disagreement where `io._write_nrrd` already wrote calibrated values, so
  one volume produced two different files depending on the entry point.
- `zarr_to_nifti` took the first `linear` transform and stopped, silently
  discarding a stacked transform or a `lut`. A single linear chain still
  round-trips exactly through `scl_slope`/`scl_inter`; anything else is
  materialized.
- `zarr_to_dicom` never derived the value mapping from `value_transforms`;
  rescale reached the output only when a dicom extension happened to carry
  the source's tags. It now writes the mapping from the transforms, which
  are authoritative, using DICOM's own forms — `RescaleSlope`/`Intercept`
  for an affine chain, a Modality LUT Sequence for a table (mutually
  exclusive, PS3.3 C.11.1.1.2) — and refuses where DICOM cannot represent
  one, since LUT Data is unsigned 16-bit and a table of negative
  Hounsfield units has no Modality LUT form.
- Unknown transform names now fail the calibrated read (§4.2) rather than
  returning a partially applied chain that looks plausible and is in no
  defined units. Stored values remain reachable.
- `resample()` drops the `dicom`/`nifti`/`fits` extensions, which describe
  a source the resampled array no longer re-encodes (§4.5).
- `cast(normalize=True)` cleared `value_transforms` but kept
  `sample_units`, leaving values spanning 0–255 still claiming HU.
- Metadata is re-validated on write (`models.duckn_attrs`), so a model
  mutated after construction fails the write instead of producing a store
  no reader will open.

### Changed
- Only the value-mapping attributes are excluded from the dicom
  extension's `tags`. `BitsStored`, `HighBit`, `PhotometricInterpretation`,
  `PlanarConfiguration` and `SamplesPerPixel` stay: they record what the
  Zarr dtype does not, a DICOM writer needs them to reconstruct, and
  unlike the rescale attributes they have no authoritative duckn
  counterpart.

### Added — value interpretation and derived-data rules (spec)
- duckn convention §4 "Value Interpretation" defines the model the code has
  been converging on: `sample_units` names the *quantity*, `value_transforms`
  describes only its *encoding*, and the writer invariant is that stored
  values interpreted through the transforms must equal the intended
  quantity. Readers must not present a partially applied chain as
  calibrated. Writers get three defined policies — preserve (default,
  the only reversible one), materialize (drops the transforms), and
  re-encode (explicit, invertible transforms only).
- §4.4 records that non-affine transforms do not commute with
  interpolation, so a `lut` must be applied before resampling, while
  nearest-neighbour is exempt because it selects rather than averages.
- §4.5/§4.6 define derived arrays and place provenance out of scope:
  inherited metadata is dropped on derivation because inheritance has no
  defined semantics, and dropping asserts nothing that a future provenance
  model could contradict.
- dicom-spec §10 applies this: the extension describes a *source DICOM
  object*, never the array it is attached to, and a derived array drops it
  entirely rather than editing or filtering it. Producing DICOM from a
  derived array is a job for a dedicated writer, not metadata inheritance.
- Pixel-encoding attributes (`RescaleSlope`/`RescaleIntercept`/`RescaleType`,
  `ModalityLUTSequence`, `PhotometricInterpretation`, `PlanarConfiguration`,
  `BitsStored`, `HighBit`, `SamplesPerPixel`) are excluded from `tags`:
  a duckn writer may change the array's encoding, at which point the DICOM
  copies describe an encoding the array no longer uses.

### Fixed
- `resample()` produced incorrect values for arrays carrying a `lut`. It
  resampled raw stored values and carried the transforms forward, which is
  valid only for affine transforms; interpolating table *indices* and then
  looking them up is unrelated to interpolating the looked-up values. Such
  arrays are now materialized before resampling and the spent transforms
  dropped. Nearest-neighbour keeps the previous behavior, since it
  commutes with any transform.

### Added — duckn convention 1.1: `lut` value transform
- `value_transforms` gains a `lut` type: an explicit stored → real lookup
  table, for value mappings that are not affine. `values[i]` is the real
  value for stored value `first_value + i`, and values outside the table
  clamp to its ends (the DICOM Modality LUT rule). A `lut` must be the
  first transform in a chain, since it indexes stored values; the metadata
  validator enforces it.
- DICOM import maps an explicit `ModalityLUTSequence` to a `lut` transform,
  and `ModalityLUTType` to `sample_units`. The explicit table and
  `RescaleSlope`/`RescaleIntercept` are mutually exclusive in DICOM
  (PS3.3 C.11.1.1.2); the table wins where both appear. `LUTData` is
  unpacked at the width its descriptor declares, since for VR `OW` it
  arrives as raw bytes that would otherwise be read as 8-bit values.
- Metadata written by the DICOM importer declares convention version
  `"1.1"` when it uses a `lut`, and `"1.0"` otherwise — the lowest version
  that covers what was written.
- The all-linear transform chain keeps its existing fast path (composed to
  a single slope/intercept); only chains containing a non-affine step take
  the new sequential path.

### Added — DICOM lossy-compression provenance
- `lossy_compressed` is a first-class field of the dicom extension, set
  from `LossyImageCompression` and falling back to the transfer syntax UID.
  Following DICOM's own rule (PS3.3 C.7.6.1.1.5) it is sticky: it records
  that the values are no longer the acquired ones, which survives
  decompression and conversion. Absent means unknown, not lossless.
- It is recorded even when tags are excluded. The transfer syntax describes
  how the source encoded its bytes — provenance, fairly dropped with the
  tags — but that the pixel values are degraded is a fact about the data,
  and losing it because someone asked for a smaller store would be a
  hazard rather than a saving.
- dicom-spec documents the lossy attributes, and warns that tags describing
  the *encoded* form (`PhotometricInterpretation`, `PlanarConfiguration`)
  stop being true once pixels are decoded into a Zarr array.

### Added
- `validate_seg_extension(ext, *, shape=None, axes=None)` checks the
  consistency rules of seg spec §5 — unique ids, background label 0,
  dangling and circular references, duplicate effective label sets, and
  (with `axes`) layer/list-axis agreement and the fractional-labelmap
  layer rule. It reports every violation at once. The spec said writers
  "should validate acyclicity"; nothing did.
- `effective_label_values(ext, segment_id)` resolves a segment's effective
  voxel set as `(layer, label_value)` pairs, following string references
  recursively. The format could express the hierarchy in spec §7.5, but
  no code could traverse it.

### Fixed
- NIfTI export dropped a qform that differed from the sform. `ed3c27c`
  moved the legacy qform restore behind `restore_transforms` (default
  off), so the default path overwrote the qform with a copy of the sform.
  It is restored again — but only when the reconstructed geometry still
  matches what was imported, so an edited geometry cannot resurrect a
  stale qform that contradicts its own sform.
- `.seg.nrrd` tag keys without the `Segmentation.` prefix gained one on
  write (`Vendor.Reviewer` → `Segmentation.Vendor.Reviewer`).
- The DICOM SEG import path never populated `terminologies`, unlike the
  `.seg.nrrd` path; both now register the schemes they reference.
- `DicomClassification` accepted a `Designation` in a coded-entry slot and
  silently dropped its `modifier` on serialization; nested modifiers are
  now rejected, pointing at the separate modifier field.

### Changed (breaking) — seg extension 0.5 → 0.6
- Reconciled the segmentation spec and implementation around a single
  identity model: `designations` (array of `{scheme, code, meaning?,
  modifier?}`) is canonical. The `identifiers` dict and the `Identifier`
  model are removed. `Designation.meaning` is now optional (the code is
  authoritative; the meaning is a rendering — on conflict the code wins);
  empty meanings normalize to `None`.
- Per-designation `url` and `display` are removed. Concept URLs are now
  derived from the new `url_template` field on `terminologies` registry
  entries (`{code}` is substituted).
- Slicer application state moved under `metadata.slicer`: extension-level
  `contained_representations`, `conversion_parameters`,
  `reference_extent_offset` and segment-level `name_auto_generated`,
  `color_auto_generated`, `tags` are no longer first-class fields.
- DICOM classification is a first-class `dicom` field on segments
  (was written to `metadata.dicom` by the DICOM SEG importer and read
  from there by the exporter, while the .seg.nrrd path used the
  top-level field — classification now survives cross-format round trips).
- `DicomClassification` entries are uniformly `CodedEntry`
  (`scheme`/`code`/`meaning?`); the id/name variant is gone.
- Spec: the version rule is now explicit — while the major version is 0, a
  minor bump may break (0.6 does). Fractional labelmaps are given semantics
  (one layer per segment; label unions and references are binary-only),
  label value 0 is reserved for background, `extent` bounds are inclusive,
  and §5's uniqueness rules are restated over (layer, label) pairs so they
  hold for layered and reference segments. Terminology registration is a
  recommendation in all three places it is mentioned (it was a requirement
  in two). The `legacy` field is documented.
- The `seg` extension is no longer called "slicerseg" in README.md,
  dicom-spec.md, units-spec.md, or zero-copy-axis-order.md.

### Added
- `Segment.label_value` accepts string entries referencing other segments
  by id (hierarchical/aggregate segments per spec §2). The .seg.nrrd
  writer raises on them (not representable); the DICOM SEG writer skips
  reference-only segments.
- DICOM SEG import now surfaces the property type code as the segment's
  primary designation, and export writes type/anatomic-region modifier
  code sequences (laterality survives export).
- `SEG_EXTENSION_VERSION` constant ("0.6"); both converters stamp it
  (previously one wrote "1.0" and the other "0.5").
- `Designation` and `SEG_EXTENSION_VERSION` are exported from `duckn`.
- Pre-0.6 seg extensions are migrated on read: `identifiers` →
  `designations`, `metadata.dicom` → `dicom`, and the moved Slicer fields →
  `metadata.slicer`. Without this, `extra="forbid"` made every store
  written by 0.1.8 unreadable, and `metadata.dicom` stores loaded fine but
  exported no classification at all.
- `SegAccessor.terminologies` / `.concept_url()` resolve a concept URL from
  the registry's `url_template`; `SegmentView.label_values` returns the
  integer labels a segment owns.
- `tests/test_dicom_seg_export.py` covers `zarr_to_dicom_seg`, which had
  none — the gap behind most of the export bugs fixed below.

### Fixed
- DICOM SEG export wrote `SegmentNumber` straight from `label_value`,
  producing files that violate DICOM's "start at 1, increase monotonically"
  rule (PS3.3 C.8.20.2.1) — a layered segmentation exported every segment
  as number 1, and sparse atlas ids exported as-is. Segment numbers are now
  assigned sequentially and the LABELMAP voxel data is remapped to match.
- DICOM SEG export fell back to the segment's `id` for a missing
  `CodeMeaning`, publishing an identifier as a SNOMED concept's meaning
  (and re-importing it as a `designation.meaning`). It now falls back only
  to the segment `name` and raises otherwise, per spec §4.2.
- DICOM SEG export could emit an empty `SegmentSequence` (Type 1, requires
  ≥1 item) for an all-reference or metadata-less segmentation. It now
  synthesizes segments from the label values present, and raises where it
  cannot. Label-union and layered segments, which have no LABELMAP
  representation, now raise instead of silently exporting partial data.
- `.seg.nrrd` serialization dropped a designation's `modifier` whenever
  `dicom.type` was present without a `type_modifier` — losing laterality on
  exactly the shape spec §7.1 shows.
- `.seg.nrrd` serialization renamed `Segmentation_SourceRepresentation` to
  the older `MasterRepresentation` spelling, and dropped `Segment*` keys the
  model does not represent. Legacy replay now starts from the original
  key/values, so both survive.
- `.seg.nrrd` parsing rejected two-part coded entries (`SCT^64033007`),
  discarding the code along with the absent meaning; assigned label value 0
  (background) to segments with no `SegmentN_LabelValue`; carried
  `SegmentN_Layer: 0` onto 3D arrays with no `list` axis, violating spec §5;
  and raised on a legal `"Closed surface"` master representation.
- `SegAccessor.segment(label_value=...)` never matched a segment whose
  `label_value` is a multi-element list, and `segment(snomed=...)` raised
  `TypeError` when `designations` was present but null.

## 0.1.8 — 2026-05-07

### Changed
- scipy is no longer an undeclared core dependency. Core paths
  (read/write/geometry/adapters) now run scipy-free. `resample()`
  still requires `scipy.ndimage` and lazy-imports it with a helpful
  error if missing: install via `pip install duckn[resample]`.
- `VolumeGeometry.from_metadata` polar decomposition switched from
  `scipy.linalg.polar` to numpy SVD (mathematically equivalent).

### Added
- `[resample]` optional extra in `pyproject.toml` (`scipy>=1.10`).

## 0.1.7 — 2026-05-07

### Changed (breaking)
- `Volume.data` is now the calibrated view: linear `value_transforms`
  from the metadata are applied lazily on first access and cached.
  `Volume.raw` is the new field holding raw stored values. Constructor
  takes `Volume(raw=..., metadata=...)` (was `data=`). `vol.dtype`
  reflects the effective output dtype (float32 when a non-identity
  transform applies, else `vol.raw.dtype`).
- This closes the calibration gap in adapter exports: `to_sitk`,
  `to_nifti`, `to_vtk` use `vol.data` and now automatically receive
  calibrated values (e.g., HU for CT) regardless of how the volume
  was loaded. `io.read("ct.zarr")` followed by `to_sitk(vol)` produces
  an SITK image containing HU values, not raw uint16.

### Migration
- `Volume(data=arr, metadata=meta)` → `Volume(raw=arr, metadata=meta)`.
- Code reading raw stored values from a Volume should switch to
  `vol.raw` (was `vol.data`). Code that wants calibrated values keeps
  using `vol.data`.

### Internal
- New shared `_rescale` helper in `zarr_io.py`. Used by both
  `DucknArray.__getitem__` and `Volume.data` so the rescale +
  output-dtype rules are defined in one place.
- Writers in `io.py` (`_write_zarr`, `_write_zmp`, `_write_zarr_zip`)
  now use `vol.raw` to preserve source representation through the
  metadata's `value_transforms`. NRRD writer keeps `vol.data` (NRRD
  has no standard slope/intercept field).
- `cast.py` operates on `vol.data` and strips `value_transforms` from
  the result's metadata (calibrated values baked into the cast output).
- `resample.py` operates on `vol.raw`, preserving metadata —
  linear value_transforms commute with linear interpolation.
- `_read_zarr` and `_read_zmp` route through `open_array().to_volume()`
  for a single source of truth.
- `DucknArray.to_volume()` simplified: always returns
  `Volume(raw=arr.zarr[:], metadata=copy)`. The wrapper's
  `apply_value_transforms` toggle is irrelevant for `to_volume` since
  Volume handles transforms itself.

## 0.1.6 — 2026-05-04

### Changed (breaking)
- Renamed `Volume.meta` → `Volume.metadata` for consistency with
  `DucknArray.metadata`. Also renames the constructor kwarg
  (`Volume(metadata=...)`) and the `meta=` parameter in `from_sitk`,
  `from_nifti`, `from_vtk` to `metadata=`. Mechanical migration: replace
  `vol.meta` with `vol.metadata` and `meta=` with `metadata=` at call
  sites for these APIs.

### Added
- `DucknMetadata.add_transform`, `DucknMetadata.get_extension`,
  `DucknMetadata.set_extension` — metadata-only operations now live on
  `DucknMetadata` so they're callable from any handle (`arr.metadata
  .add_transform(...)`, `vol.metadata.add_transform(...)`).
  `Volume.add_transform` etc. continue to work as thin delegations
  (Volume's wrapper additionally invalidates the cached geometry).
- `DucknArray.geometry` — cached `VolumeGeometry` computed from
  metadata + shape, parallel to `Volume.geometry`.
- `DucknArray.to_volume()` — eager bridge to `Volume`. Materializes via
  the wrapper's current settings (`apply_value_transforms`,
  `transform_dtype`); strips `value_transforms` from the returned
  metadata when transforms have already been applied so consumers don't
  double-apply.

## 0.1.5 — 2026-05-04

### Added
- `duckn.open_array(source, *, apply_value_transforms=True, transform_dtype=None)`
  returns a `DucknArray` — a thin wrapper around `zarr.Array` that
  applies linear value transforms on slice. Toggle via the mutable
  `arr.apply_value_transforms` attribute at any time. `arr.metadata`
  exposes the parsed `DucknMetadata` snapshot; `arr.zarr` exposes the
  underlying `zarr.Array` (use `arr.zarr.metadata` for zarr-level array
  info — shape/codecs/chunk grid). `arr.attrs`, `shape`, `chunks`,
  `ndim`, `size` forward to the zarr handle. `arr.dtype` is dynamic and
  reflects the effective output dtype under current settings. Supports
  the context-manager protocol so the store is closed on exit (relevant
  for `.zarr.zip` and `.zmp`).
- `transform_dtype` lets callers pin the output dtype of slicing when
  transforms apply (e.g., `np.float64` for high-precision computation,
  `np.int16` to write back into integer space). `None` (default) keeps
  the existing behavior: float32 for non-identity transforms, native
  dtype for identity. Float targets compute in their own dtype if
  float64; otherwise float32 working precision (no excessive memory).
  Integer targets round with `np.rint` before cast (no overflow check —
  caller's responsibility).

### Removed
- `read_array` (added in 0.1.3) — superseded by `open_array(p)[:]` or
  `np.asarray(open_array(p))`. One fewer name to maintain.

## 0.1.3 — 2026-05-04

### Added
- `read_array(source, *, apply_value_transforms=True)` — high-level reader
  that returns a numpy array with linear value transforms (slope/intercept)
  applied by default. Pass `apply_value_transforms=False` for raw stored
  values. Identity transforms preserve the source dtype; otherwise output
  is float32. Multiple linear transforms compose into a single rescale.
- `read_metadata(source)` — short-form alias of `read_duckn_metadata`.

## 0.1.2 — 2026-05-04

### Added
- DICOM round-trip now preserves `BitsStored`, `HighBit`, `SamplesPerPixel`,
  `PhotometricInterpretation`, and `PlanarConfiguration` in the dicom
  extension tags. Previously these were dropped on import and hardcoded
  on export, silently losing 12-bit-in-16 precision metadata and turning
  `MONOCHROME1` sources into `MONOCHROME2` (inverted display).
- RGB DICOM support (read + write). Sources with `SamplesPerPixel=3,
  PhotometricInterpretation=RGB` round-trip as 4D arrays
  `(slices, rows, cols, 3)` with a trailing `RGB-color` axis.
  `PlanarConfiguration=1` (color-by-plane) is canonicalized to
  channel-last on import; export emits Multi-frame True Color SC
  (`1.2.840.10008.5.1.4.1.1.7.4`).

### Changed
- `zarr_to_dicom` now validates pixel-description tags from the dicom
  extension and raises on conflicts (e.g., stored `SamplesPerPixel=3`
  on an array without an `RGB-color` axis) instead of silently
  emitting wrong DICOM.
- Renamed internal `DicomGeometry` dataclass to `DicomImageInfo` —
  the struct already carried dtype, value transforms, and per-slice
  samples in addition to geometry.

### Limitations
- Multi-frame RGB SC files (single-file-multi-frame color) raise
  `NotImplementedError`. Use a directory of single-frame files.
- YBR_*, PALETTE_COLOR, ARGB photometric interpretations are rejected.
- Time + RGB combination (would be 5D) is not supported.

## 0.1.1 — 2026-04-05

### Fixed
- `open_store` now handles `.zmp` files via `ZMPStore`, fixing `to-nifti` and
  other commands that failed on `.zmp` inputs with `ArrayNotFoundError`.

## 0.1.0

Initial release.
