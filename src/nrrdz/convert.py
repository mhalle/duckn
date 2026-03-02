"""Bidirectional conversion between NRRD files and nrrdz Zarr stores."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import nrrd
import numpy as np
import zarr

from .models import AxisKind, AxisMetadata, Centering, NrrdMetadata, SpaceName, _SPACE_ABBREVS


# NRRD spec fields (`: ` delimiter).  Anything else is a key/value pair (`:=`).
_NRRD_SPEC_FIELDS: frozenset[str] = frozenset({
    "type", "dimension", "space dimension", "space", "sizes",
    "space directions", "kinds", "endian", "encoding",
    "min", "max", "oldmin", "old min", "oldmax", "old max",
    "content", "sample units", "spacings", "thicknesses",
    "axis mins", "axismins", "axis maxs", "axismaxs",
    "centerings", "labels", "units", "space units",
    "space origin", "measurement frame",
    "data file", "datafile",
    "lineskip", "line skip", "byteskip", "byte skip",
    "number",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_nan_vector(v: Any) -> bool:
    """Check whether a space-direction row is the NaN sentinel (non-spatial axis)."""
    if v is None:
        return True
    try:
        return all(math.isnan(float(x)) for x in v)
    except (TypeError, ValueError):
        return False


def _auto_chunks(shape: tuple[int, ...], dtype: np.dtype, target_bytes: int = 1_048_576) -> tuple[int, ...]:
    """Compute chunk shape by halving the largest dimension until under target size."""
    chunks = list(shape)
    itemsize = dtype.itemsize
    while _chunk_bytes(chunks, itemsize) > target_bytes and max(chunks) > 1:
        largest = chunks.index(max(chunks))
        chunks[largest] = max(1, chunks[largest] // 2)
    return tuple(chunks)


def _chunk_bytes(chunks: list[int], itemsize: int) -> int:
    result = itemsize
    for c in chunks:
        result *= c
    return result


def _transpose_matrix(mat: list[list[float]]) -> list[list[float]]:
    """Transpose a list-of-lists matrix."""
    n = len(mat)
    return [[mat[j][i] for j in range(n)] for i in range(n)]


def _clean_float_list(v: list) -> list[float]:
    """Ensure all elements are native Python floats."""
    return [float(x) for x in v]


# ---------------------------------------------------------------------------
# NRRD -> nrrdz Zarr
# ---------------------------------------------------------------------------


def nrrd_to_zarr(
    nrrd_path: str | Path,
    zarr_path: str | Path,
    *,
    chunks: tuple[int, ...] | None = None,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
) -> None:
    """Convert an NRRD file to a nrrdz Zarr v3 store.

    Parameters
    ----------
    nrrd_path : path to the input .nrrd file
    zarr_path : path for the output Zarr store (directory)
    chunks : explicit chunk shape, or None for auto-chunking
    compressor : "zstd", "gzip", or "none"
    level : compression level
    overwrite : if True, overwrite existing store
    """
    data, header = nrrd.read(str(nrrd_path), index_order="C")
    ndim = data.ndim
    shape = data.shape

    # --- Build compressor for Zarr v3 ---
    compressors_list = _build_compressors(compressor, level)

    # --- Per-axis fields from NRRD header ---
    # NRRD stores per-axis fields in fastest-first order.
    # pynrrd with index_order='C' returns data in C order (last dim fastest).
    # We need to reverse the per-axis header arrays to match C order.

    kinds_raw = header.get("kinds")
    centerings_raw = header.get("centerings")
    space_dirs_raw = header.get("space directions")
    thicknesses_raw = header.get("thicknesses")
    labels_raw = header.get("labels")
    units_raw = header.get("units")
    space_units_raw = header.get("space units")

    # Reverse to C order
    def _rev(arr: list | np.ndarray | None) -> list | None:
        if arr is None:
            return None
        return list(reversed(arr))

    kinds = _rev(kinds_raw)
    centerings = _rev(centerings_raw)
    space_dirs = _rev(space_dirs_raw)
    thicknesses = _rev(thicknesses_raw)
    labels = _rev(labels_raw)
    units = _rev(units_raw)
    # space units is indexed by space dimension, NOT reversed

    # --- Determine space info ---
    space_name_raw = header.get("space")
    space_dim_raw = header.get("space dimension")
    space_origin_raw = header.get("space origin")
    mf_raw = header.get("measurement frame")

    space_name: SpaceName | None = None
    space_dimension: int | None = None
    if space_name_raw:
        normalized = _SPACE_ABBREVS.get(space_name_raw, space_name_raw)
        space_name = SpaceName(normalized)
    elif space_dim_raw:
        space_dimension = int(space_dim_raw)

    # Figure out which axes are spatial (have a non-NaN space direction)
    spatial_axis_indices: list[int] = []
    if space_dirs is not None:
        for i in range(ndim):
            if not _is_nan_vector(space_dirs[i]):
                spatial_axis_indices.append(i)

    # --- Build AxisMetadata for each C-order axis ---
    axes: list[AxisMetadata] = []
    # Track spatial axis counter for space_units assignment
    spatial_count = 0
    for i in range(ndim):
        ax_kwargs: dict[str, Any] = {}

        if kinds is not None and i < len(kinds):
            k = kinds[i]
            if k and k != "???":
                # NRRD uses "none" to mean unknown kind; we omit it
                if k.lower() != "none":
                    ax_kwargs["kind"] = k

        if centerings is not None and i < len(centerings):
            c = centerings[i]
            if c and c != "???":
                if c.lower() != "none":
                    ax_kwargs["centering"] = c

        is_spatial = i in spatial_axis_indices
        if space_dirs is not None and i < len(space_dirs) and is_spatial:
            ax_kwargs["space_direction"] = _clean_float_list(space_dirs[i])

        if thicknesses is not None and i < len(thicknesses):
            t = thicknesses[i]
            if t is not None and not (isinstance(t, float) and math.isnan(t)):
                ax_kwargs["thickness"] = float(t)

        # Units: for spatial axes prefer space units, else use per-axis units
        if is_spatial and space_units_raw is not None:
            if spatial_count < len(space_units_raw):
                su = space_units_raw[spatial_count]
                if su and su != "???":
                    ax_kwargs["unit"] = su
            spatial_count += 1
        elif units is not None and i < len(units):
            u = units[i]
            if u and u != "???":
                ax_kwargs["unit"] = u

        if not is_spatial and space_dirs is None and units is not None and i < len(units):
            # No space directions at all — use per-axis units for everything
            pass  # already handled above

        axes.append(AxisMetadata(**ax_kwargs))

    # --- Build NrrdMetadata ---
    meta_kwargs: dict[str, Any] = {"version": "1.0", "axes": axes}
    if space_name:
        meta_kwargs["space"] = space_name
    elif space_dimension:
        meta_kwargs["space_dimension"] = space_dimension

    if space_origin_raw is not None:
        meta_kwargs["space_origin"] = _clean_float_list(space_origin_raw)

    if mf_raw is not None:
        # pynrrd returns measurement frame as row vectors (each row is a basis vector).
        # The nrrdz spec stores column vectors. Transpose.
        mf_list = [_clean_float_list(row) for row in mf_raw]
        meta_kwargs["measurement_frame"] = _transpose_matrix(mf_list)

    # sample_units from NRRD "sample units" field (rare but exists)
    sample_units_raw = header.get("sample units")
    if sample_units_raw:
        meta_kwargs["sample_units"] = sample_units_raw

    # --- Preserve NRRD key/value pairs (`:=` lines) ---
    # Any header key not in the NRRD spec field set is a key/value pair.
    # Store them in insertion order so round-trip preserves original ordering.
    keyvalues: dict[str, str] = {}
    for k, v in header.items():
        if k not in _NRRD_SPEC_FIELDS:
            keyvalues[k] = str(v)
    if keyvalues:
        meta_kwargs["extensions"] = {"keyvalues": keyvalues}

    meta = NrrdMetadata(**meta_kwargs)

    # --- dimension_names from labels ---
    dimension_names: list[str] | None = None
    if labels:
        clean_labels = [lb if lb and lb != "???" else "" for lb in labels]
        if any(clean_labels):
            dimension_names = clean_labels

    # --- content as top-level attribute (outside nrrd key) ---
    extra_attrs: dict[str, Any] = {}
    content_raw = header.get("content")
    if content_raw:
        extra_attrs["content"] = content_raw

    # --- Chunk shape ---
    if chunks is None:
        chunks = _auto_chunks(shape, data.dtype)

    # --- Write Zarr v3 ---
    store = zarr.storage.LocalStore(str(zarr_path))
    attrs = {"nrrd": meta.model_dump(exclude_none=True)}
    attrs.update(extra_attrs)

    zarr.create_array(
        store,
        data=data,
        chunks=chunks,
        compressors=compressors_list,
        dimension_names=dimension_names,
        attributes=attrs,
        overwrite=overwrite,
        fill_value=0,
    )


# ---------------------------------------------------------------------------
# nrrdz Zarr -> NRRD
# ---------------------------------------------------------------------------


def zarr_to_nrrd(
    zarr_path: str | Path,
    nrrd_path: str | Path,
    *,
    encoding: str = "gzip",
    overwrite: bool = False,
) -> None:
    """Convert a nrrdz Zarr v3 store to an NRRD file.

    Parameters
    ----------
    zarr_path : path to the input Zarr store
    nrrd_path : path for the output .nrrd file
    encoding : NRRD encoding ("gzip", "raw", "bzip2")
    overwrite : if True, overwrite existing file
    """
    nrrd_path = Path(nrrd_path)
    if nrrd_path.exists() and not overwrite:
        raise FileExistsError(f"{nrrd_path} already exists (use --overwrite)")

    store = zarr.storage.LocalStore(str(zarr_path))
    arr = zarr.open_array(store, mode="r")
    data = arr[:]
    nrrd_attrs = arr.attrs.get("nrrd", {})

    meta = NrrdMetadata(**nrrd_attrs)
    ndim = data.ndim

    header: dict[str, Any] = {}
    header["encoding"] = encoding

    # --- space ---
    if meta.space is not None:
        header["space"] = meta.space.value
    elif meta.space_dimension is not None:
        header["space dimension"] = meta.space_dimension

    # --- space origin ---
    if meta.space_origin is not None:
        header["space origin"] = np.array(meta.space_origin)

    # --- measurement frame ---
    if meta.measurement_frame is not None:
        # nrrdz stores column vectors; pynrrd expects row vectors. Transpose.
        mf_cols = meta.measurement_frame
        mf_rows = _transpose_matrix(mf_cols)
        header["measurement frame"] = np.array(mf_rows)

    # --- Per-axis fields: reverse from C order to NRRD fastest-first ---
    axes = meta.axes or []
    if axes:
        # Reverse axes to NRRD order (fastest first = last C-order axis first)
        nrrd_axes = list(reversed(axes))

        # kinds
        kinds_out = [ax.kind if ax.kind else "???" for ax in nrrd_axes]
        if any(k != "???" for k in kinds_out):
            header["kinds"] = kinds_out

        # centerings
        centerings_out = [ax.centering if ax.centering else "???" for ax in nrrd_axes]
        if any(c != "???" for c in centerings_out):
            header["centerings"] = centerings_out

        # space directions: non-spatial axes get NaN vectors
        space_dim = meta._get_space_dim()
        has_any_dir = any(ax.space_direction is not None for ax in nrrd_axes)
        if has_any_dir and space_dim is not None:
            dirs_out = []
            for ax in nrrd_axes:
                if ax.space_direction is not None:
                    dirs_out.append(np.array(ax.space_direction))
                else:
                    dirs_out.append(np.full(space_dim, np.nan))
            header["space directions"] = np.array(dirs_out)

        # thicknesses
        thicknesses_out = []
        has_thickness = False
        for ax in nrrd_axes:
            if ax.thickness is not None:
                thicknesses_out.append(ax.thickness)
                has_thickness = True
            else:
                thicknesses_out.append(np.nan)
        if has_thickness:
            header["thicknesses"] = thicknesses_out

        # space units: extracted from spatial axes' unit strings, in space-dimension order
        # Spatial axes in NRRD order
        if space_dim is not None:
            spatial_units: list[str] = []
            for ax in nrrd_axes:
                if ax.space_direction is not None:
                    u = ax.unit
                    if isinstance(u, str):
                        spatial_units.append(u)
                    elif u is not None:
                        # UnitObject — use symbol as the NRRD string
                        spatial_units.append(u.symbol)  # type: ignore[union-attr]
                    else:
                        spatial_units.append("")
            if any(spatial_units):
                header["space units"] = spatial_units

    # --- labels from dimension_names (reversed to NRRD order) ---
    dim_names = arr.metadata.dimension_names
    if dim_names is not None:
        nrrd_labels = list(reversed(dim_names))
        if any(nrrd_labels):
            header["labels"] = nrrd_labels

    # --- content ---
    content = arr.attrs.get("content")
    if content:
        header["content"] = content

    # --- sample units ---
    if meta.sample_units is not None:
        if isinstance(meta.sample_units, str):
            header["sample units"] = meta.sample_units
        else:
            header["sample units"] = meta.sample_units.symbol  # type: ignore[union-attr]

    # --- Restore NRRD key/value pairs ---
    if meta.extensions and "keyvalues" in meta.extensions:
        for k, v in meta.extensions["keyvalues"].items():
            header[k] = v

    nrrd.write(str(nrrd_path), data, header, index_order="C")


# ---------------------------------------------------------------------------
# Codec helpers
# ---------------------------------------------------------------------------


def _build_compressors(compressor: str, level: int) -> list[Any] | None:
    """Build a Zarr v3 compressors list."""
    from zarr.codecs import GzipCodec, ZstdCodec

    if compressor == "zstd":
        return [ZstdCodec(level=level)]
    elif compressor == "gzip":
        return [GzipCodec(level=level)]
    elif compressor == "none":
        return None
    else:
        raise ValueError(f"Unknown compressor: {compressor!r}")
