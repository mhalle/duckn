# Changelog

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
