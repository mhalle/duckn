# Zero-Copy Axis Order: Unifying to `slowest_first`

## Problem

nrrdz has two code paths for NRRD-to-Zarr conversion:

| Path | Axis order in Zarr | Shape |
|------|--------------------|-------|
| `nrrd_to_zarr` (normal) | `slowest_first` | `(z, y, x)` |
| `nrrd_to_zarr_zerocopy` | `fastest_first` | `(x, y, z)` |

The zero-copy path adopted `fastest_first` order to match NRRD's native memory layout, avoiding decompression and recompression. But this means the same NRRD file produces structurally different stores depending on which path created it. Consumers must know which convention they're reading.

### Naming convention

The internal parameter `axis_order` uses two values:

- **`slowest_first`** — the slowest-varying axis is first in the shape. This is the default for nrrdz stores and the convention used by NumPy, Zarr, and most array libraries.
- **`fastest_first`** — the fastest-varying axis is first in the shape. This is the native axis order used by NRRD files.

These names describe the memory layout directly without referencing programming languages.

## Key Insight

A `fastest_first` array with shape `(x, y, z)` where x varies fastest is **the same byte layout** as a `slowest_first` array with shape `(z, y, x)` where x varies fastest. The raw data bytes are identical under both interpretations — only the axis labeling differs.

This means zero-copy can use `slowest_first` order by reversing the shape and per-axis metadata, without touching the data blob.

## What Changes

### Per-axis fields (reverse order)

These are indexed by axis and must be reversed from `fastest_first` (NRRD) to `slowest_first` (Zarr):

- **shape** — `(x, y, z)` becomes `(z, y, x)` in `zarr.json`
- **space_directions** — order of direction vectors
- **kinds** — e.g., `[space, space, list]` becomes `[list, space, space]`
- **centerings**
- **thicknesses**
- **dimension_names** / labels
- **per-axis extensions** — e.g., DWI gradient axis metadata

Per-axis extensions are part of `AxisMetadata` objects, so they reverse automatically when the axes list is reversed.

### Axis-order-independent fields (no change)

These use space coordinates or domain-specific values, not axis indices:

- **Raw chunk bytes** — bit-identical in both orderings
- **space_origin** — position in space coordinates
- **measurement_frame** — matrix in space coordinates
- **space** name — e.g., `right-anterior-superior`
- **value_transforms** — slope/intercept
- **Top-level extensions** — nifti, dicom, segmentation, dwmri, legacy

## Implementation

Four lines change. The `legacy` extension continues to carry `nrrd_type` and `encoding` for round-trip fidelity.

### `nrrd_to_zarr_zerocopy` (NRRD to Zarr)

```python
# Before (fastest_first):
sizes = [int(s) for s in header["sizes"]]
shape = tuple(sizes)
meta, dim_names, extra = _header_to_metadata(
    header, ndim, axis_order="fastest_first",
)

# After (slowest_first):
sizes = [int(s) for s in header["sizes"]]
shape = tuple(reversed(sizes))
meta, dim_names, extra = _header_to_metadata(
    header, ndim, axis_order="slowest_first",
)
```

### `zarr_to_nrrd_zerocopy` (Zarr to NRRD)

```python
# Before (fastest_first):
sizes = list(shape)
header = _metadata_to_header(meta, axis_order="fastest_first", ...)

# After (slowest_first):
sizes = list(reversed(shape))
header = _metadata_to_header(meta, axis_order="slowest_first", ...)
```

### Internal API

The `_header_to_metadata` and `_metadata_to_header` functions accept:

```python
axis_order: Literal["slowest_first", "fastest_first"] = "slowest_first"
```

- `slowest_first` (default): reverses per-axis fields from NRRD order to array order. Used by all Zarr stores.
- `fastest_first`: keeps per-axis fields in NRRD native order. Used only when writing NRRD files from `fastest_first` stores (legacy path).

## Result

After this change, every nrrdz store uses `slowest_first` axis order regardless of how it was created. `read_nrrdz()`, `arr[:]`, and all metadata queries return data in consistent axis order. The zero-copy round-trip remains bit-exact — the raw bytes are never decompressed or recompressed.
