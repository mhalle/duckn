# Changelog

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
