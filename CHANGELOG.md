# Changelog

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
