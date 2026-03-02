"""Bidirectional conversion between NRRD files and nrrdz Zarr stores."""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any

import nrrd
import numpy as np
import zarr

from .models import (
    AxisKind, AxisMetadata, Centering, DwmriAxisExtension, DwmriExtension,
    NrrdMetadata, SegmentationExtension, SpaceName, _SPACE_ABBREVS,
)
from .dwi_nrrd import parse_dwi_keyvalues, serialize_dwi_extension
from .seg_nrrd import parse_seg_keyvalues, serialize_seg_extension


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


# NRRD type string -> numpy dtype string
_NRRD_TYPE_MAP: dict[str, str] = {
    "signed char": "int8", "int8": "int8", "int8_t": "int8",
    "uchar": "uint8", "unsigned char": "uint8", "uint8": "uint8", "uint8_t": "uint8",
    "short": "int16", "short int": "int16", "signed short": "int16",
    "signed short int": "int16", "int16": "int16", "int16_t": "int16",
    "ushort": "uint16", "unsigned short": "uint16", "unsigned short int": "uint16",
    "uint16": "uint16", "uint16_t": "uint16",
    "int": "int32", "signed int": "int32", "int32": "int32", "int32_t": "int32",
    "uint": "uint32", "unsigned int": "uint32", "uint32": "uint32", "uint32_t": "uint32",
    "longlong": "int64", "long long": "int64", "long long int": "int64",
    "signed long long": "int64", "signed long long int": "int64",
    "int64": "int64", "int64_t": "int64",
    "ulonglong": "uint64", "unsigned long long": "uint64",
    "unsigned long long int": "uint64", "uint64": "uint64", "uint64_t": "uint64",
    "float": "float32",
    "double": "float64",
}

# numpy dtype -> default NRRD type string
_DTYPE_TO_NRRD_TYPE: dict[str, str] = {
    "int8": "int8", "uint8": "uint8",
    "int16": "short", "uint16": "ushort",
    "int32": "int", "uint32": "uint",
    "int64": "longlong", "uint64": "ulonglong",
    "float32": "float", "float64": "double",
}


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


def _nrrd_dtype(header: dict) -> np.dtype:
    """Map NRRD type string to numpy dtype."""
    type_str = header["type"].lower().strip()
    np_type = _NRRD_TYPE_MAP.get(type_str)
    if np_type is None:
        raise ValueError(f"Unsupported NRRD type: {header['type']!r}")
    return np.dtype(np_type)


def _fmt_float(x: float) -> str:
    """Format a float for NRRD header output."""
    x = float(x)
    if x != x:  # NaN
        return "NaN"
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.17g}"


def _codecs_for_encoding(encoding: str) -> tuple[Any, list[Any] | None]:
    """Return (serializer, compressors) for a zero-copy Zarr array.

    The serializer is always BytesCodec(endian="little").
    The compressors list matches the NRRD encoding.
    """
    from zarr.codecs import BytesCodec, GzipCodec

    serializer = BytesCodec(endian="little")
    enc = encoding.lower().strip()
    if enc == "raw":
        return serializer, None
    elif enc in ("gzip", "gz"):
        return serializer, [GzipCodec(level=5)]
    else:
        raise ValueError(
            f"Zero-copy not supported for encoding {encoding!r}. "
            f"Supported: raw, gzip"
        )


# ---------------------------------------------------------------------------
# Shared metadata building
# ---------------------------------------------------------------------------


def _header_to_metadata(
    header: dict[str, Any],
    ndim: int,
    *,
    reverse_axes: bool = True,
) -> tuple[NrrdMetadata, list[str] | None, dict[str, Any]]:
    """Build NrrdMetadata from a pynrrd header dict.

    Parameters
    ----------
    header : dict from pynrrd's read() or read_header()
    ndim : number of array dimensions
    reverse_axes : if True, reverse per-axis fields from NRRD order to C order

    Returns
    -------
    meta : NrrdMetadata
    dimension_names : list of strings or None
    extra_attrs : dict of extra attributes (e.g., content)
    """
    # --- Per-axis fields from NRRD header ---
    kinds_raw = header.get("kinds")
    centerings_raw = header.get("centerings")
    space_dirs_raw = header.get("space directions")
    thicknesses_raw = header.get("thicknesses")
    labels_raw = header.get("labels")
    units_raw = header.get("units")
    space_units_raw = header.get("space units")

    def _rev(arr: list | np.ndarray | None) -> list | None:
        if arr is None:
            return None
        return list(reversed(arr))

    if reverse_axes:
        kinds = _rev(kinds_raw)
        centerings = _rev(centerings_raw)
        space_dirs = _rev(space_dirs_raw)
        thicknesses = _rev(thicknesses_raw)
        labels = _rev(labels_raw)
        units = _rev(units_raw)
    else:
        kinds = list(kinds_raw) if kinds_raw is not None else None
        centerings = list(centerings_raw) if centerings_raw is not None else None
        space_dirs = list(space_dirs_raw) if space_dirs_raw is not None else None
        thicknesses = list(thicknesses_raw) if thicknesses_raw is not None else None
        labels = list(labels_raw) if labels_raw is not None else None
        units = list(units_raw) if units_raw is not None else None
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

    # --- Build AxisMetadata for each axis ---
    axes: list[AxisMetadata] = []
    spatial_count = 0
    for i in range(ndim):
        ax_kwargs: dict[str, Any] = {}

        if kinds is not None and i < len(kinds):
            k = kinds[i]
            if k and k != "???":
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
        mf_list = [_clean_float_list(row) for row in mf_raw]
        meta_kwargs["measurement_frame"] = _transpose_matrix(mf_list)

    sample_units_raw = header.get("sample units")
    if sample_units_raw:
        meta_kwargs["sample_units"] = sample_units_raw

    # --- Preserve NRRD key/value pairs (`:=` lines) ---
    keyvalues: dict[str, str] = {}
    for k, v in header.items():
        if k not in _NRRD_SPEC_FIELDS:
            keyvalues[k] = str(v)
    if keyvalues:
        extensions: dict[str, Any] = {}
        seg_ext, remaining = parse_seg_keyvalues(keyvalues)
        if seg_ext is not None:
            extensions["segmentation"] = seg_ext.model_dump(exclude_none=True)

        dwi_ext, dwi_axis_ext, remaining = parse_dwi_keyvalues(remaining)
        if dwi_ext is not None:
            extensions["dwmri"] = dwi_ext.model_dump(exclude_none=True)
        if dwi_axis_ext is not None:
            dwi_axis_dict = dwi_axis_ext.model_dump(exclude_none=True)
            for ax in axes:
                if ax.kind in (AxisKind.LIST, AxisKind.VECTOR):
                    ax_exts = dict(ax.extensions) if ax.extensions else {}
                    ax_exts["dwmri"] = dwi_axis_dict
                    ax.extensions = ax_exts
                    break

        if remaining:
            extensions["keyvalues"] = remaining
        if extensions:
            meta_kwargs["extensions"] = extensions

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

    return meta, dimension_names, extra_attrs


def _metadata_to_header(
    meta: NrrdMetadata,
    *,
    reverse_axes: bool = True,
    dim_names: tuple[str, ...] | list[str] | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    """Reconstruct NRRD header dict from NrrdMetadata.

    Parameters
    ----------
    meta : NrrdMetadata from zarr attributes
    reverse_axes : if True, reverse axes from C order to NRRD order
    dim_names : dimension names from zarr metadata
    content : content string from zarr attributes

    Returns
    -------
    header : dict suitable for pynrrd's write() or manual header writing
    """
    header: dict[str, Any] = {}

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
        mf_cols = meta.measurement_frame
        mf_rows = _transpose_matrix(mf_cols)
        header["measurement frame"] = np.array(mf_rows)

    # --- Per-axis fields ---
    axes = meta.axes or []
    if axes:
        if reverse_axes:
            nrrd_axes = list(reversed(axes))
        else:
            nrrd_axes = list(axes)

        # kinds
        kinds_out = [ax.kind if ax.kind else "???" for ax in nrrd_axes]
        if any(k != "???" for k in kinds_out):
            header["kinds"] = kinds_out

        # centerings
        centerings_out = [ax.centering if ax.centering else "???" for ax in nrrd_axes]
        if any(c != "???" for c in centerings_out):
            header["centerings"] = centerings_out

        # space directions
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

        # space units
        if space_dim is not None:
            spatial_units: list[str] = []
            for ax in nrrd_axes:
                if ax.space_direction is not None:
                    u = ax.unit
                    if isinstance(u, str):
                        spatial_units.append(u)
                    elif u is not None:
                        spatial_units.append(u.symbol)  # type: ignore[union-attr]
                    else:
                        spatial_units.append("")
            if any(spatial_units):
                header["space units"] = spatial_units

    # --- labels from dimension_names ---
    if dim_names is not None:
        if reverse_axes:
            nrrd_labels = list(reversed(dim_names))
        else:
            nrrd_labels = list(dim_names)
        if any(nrrd_labels):
            header["labels"] = nrrd_labels

    # --- content ---
    if content:
        header["content"] = content

    # --- sample units ---
    if meta.sample_units is not None:
        if isinstance(meta.sample_units, str):
            header["sample units"] = meta.sample_units
        else:
            header["sample units"] = meta.sample_units.symbol  # type: ignore[union-attr]

    # --- Restore NRRD key/value pairs ---
    if meta.extensions:
        if "segmentation" in meta.extensions:
            seg_ext = SegmentationExtension(**meta.extensions["segmentation"])
            for k, v in serialize_seg_extension(seg_ext).items():
                header[k] = v
        if "dwmri" in meta.extensions:
            dwi_axis_dict = None
            for ax in (meta.axes or []):
                if ax.extensions and "dwmri" in ax.extensions:
                    dwi_axis_dict = ax.extensions["dwmri"]
                    break
            dwi_top = DwmriExtension(**meta.extensions["dwmri"])
            dwi_axis = DwmriAxisExtension(**dwi_axis_dict) if dwi_axis_dict else DwmriAxisExtension()
            for k, v in serialize_dwi_extension(dwi_top, dwi_axis).items():
                header[k] = v
        if "keyvalues" in meta.extensions:
            for k, v in meta.extensions["keyvalues"].items():
                header[k] = v

    return header


# ---------------------------------------------------------------------------
# NRRD header writer (for zero-copy zarr -> nrrd)
# ---------------------------------------------------------------------------


def _format_nrrd_field(key: str, value: Any) -> str:
    """Format a single NRRD header field value as a string."""
    if isinstance(value, np.ndarray):
        if key == "space origin":
            return "(" + ",".join(_fmt_float(x) for x in value) + ")"
        elif key == "space directions":
            parts = []
            for row in value:
                if _is_nan_vector(row):
                    parts.append("none")
                else:
                    parts.append("(" + ",".join(_fmt_float(x) for x in row) + ")")
            return " ".join(parts)
        elif key == "measurement frame":
            parts = []
            for row in value:
                parts.append("(" + ",".join(_fmt_float(x) for x in row) + ")")
            return " ".join(parts)
        else:
            return " ".join(str(x) for x in value.flat)
    elif isinstance(value, list):
        if key in ("kinds", "centerings"):
            return " ".join(str(v) for v in value)
        elif key in ("space units", "labels"):
            return " ".join(f'"{v}"' for v in value)
        elif key == "thicknesses":
            parts = []
            for t in value:
                if isinstance(t, float) and math.isnan(t):
                    parts.append("NaN")
                else:
                    parts.append(_fmt_float(t))
            return " ".join(parts)
        else:
            return " ".join(str(v) for v in value)
    return str(value)


# Conventional order for NRRD header fields
_NRRD_FIELD_ORDER: list[str] = [
    "space", "space dimension", "space origin", "space directions",
    "kinds", "centerings", "space units", "labels",
    "thicknesses", "measurement frame", "content", "sample units",
]


def _write_nrrd_header(
    fh: Any,
    *,
    sizes: list[int] | tuple[int, ...],
    nrrd_type: str,
    encoding: str,
    itemsize: int,
    header: dict[str, Any],
) -> None:
    """Write an NRRD header to a binary file handle.

    After this returns, the file handle is positioned right after the
    blank-line separator.  The caller should write data bytes next.
    """
    lines: list[str] = ["NRRD0005"]
    lines.append(f"type: {nrrd_type}")
    lines.append(f"dimension: {len(sizes)}")
    lines.append(f"sizes: {' '.join(str(s) for s in sizes)}")
    lines.append(f"encoding: {encoding}")
    if itemsize > 1:
        lines.append("endian: little")

    # Standard fields in conventional order
    for field in _NRRD_FIELD_ORDER:
        if field in header:
            lines.append(f"{field}: {_format_nrrd_field(field, header[field])}")

    # Key/value pairs (`:=` separator)
    for k, v in header.items():
        if k not in _NRRD_SPEC_FIELDS:
            lines.append(f"{k}:={v}")

    fh.write(("\n".join(lines) + "\n\n").encode("ascii"))


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

    Uses pynrrd to decompress and zarr to recompress the data.
    Per-axis fields are stored in C order (slowest-first).

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

    compressors_list = _build_compressors(compressor, level)

    meta, dimension_names, extra_attrs = _header_to_metadata(
        header, ndim, reverse_axes=True
    )

    if chunks is None:
        chunks = _auto_chunks(shape, data.dtype)

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

    Reads the data through zarr and writes via pynrrd.
    Expects per-axis fields in C order (slowest-first).

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

    dim_names = arr.metadata.dimension_names
    content = arr.attrs.get("content")

    header = _metadata_to_header(
        meta,
        reverse_axes=True,
        dim_names=dim_names,
        content=content,
    )
    header["encoding"] = encoding

    nrrd.write(str(nrrd_path), data, header, index_order="C")


# ---------------------------------------------------------------------------
# Zero-copy: NRRD -> nrrdz Zarr
# ---------------------------------------------------------------------------


def nrrd_to_zarr_zerocopy(
    nrrd_path: str | Path,
    zarr_path: str | Path,
    *,
    overwrite: bool = False,
) -> None:
    """Convert an NRRD file to a nrrdz Zarr v3 store using zero-copy.

    Copies the compressed (or raw) data blob directly from the NRRD file
    into the Zarr chunk file without decompression or recompression.

    Requirements:
    - Encoding must be ``raw`` or ``gzip``
    - Data must be little-endian (or single-byte type)
    - File must not use a detached header

    The Zarr store uses NRRD axis order (fastest-first), so ``shape`` in
    ``zarr.json`` matches NRRD ``sizes`` directly.
    """
    nrrd_path = Path(nrrd_path)
    zarr_path = Path(zarr_path)

    if zarr_path.exists() and not overwrite:
        raise FileExistsError(f"{zarr_path} already exists (use --overwrite)")

    with open(nrrd_path, "rb") as fh:
        header = nrrd.read_header(fh)

        # Validate encoding
        encoding = header.get("encoding", "raw").lower().strip()
        if encoding not in ("raw", "gzip", "gz"):
            raise ValueError(
                f"Zero-copy requires raw or gzip encoding, got {encoding!r}. "
                f"Use nrrd_to_zarr() instead."
            )

        # Validate not detached
        if header.get("data file") or header.get("datafile"):
            raise ValueError(
                "Zero-copy does not support detached headers (.nhdr). "
                "Use nrrd_to_zarr() instead."
            )

        # Map dtype and validate endianness
        dtype = _nrrd_dtype(header)
        if dtype.itemsize > 1:
            endian = header.get("endian", "").lower().strip()
            if endian != "little":
                raise ValueError(
                    f"Zero-copy requires little-endian data, got {endian!r}. "
                    f"Use nrrd_to_zarr() instead."
                )

        # Handle line skip / byte skip
        line_skip = int(header.get("line skip", header.get("lineskip", 0)))
        byte_skip = int(header.get("byte skip", header.get("byteskip", 0)))
        for _ in range(line_skip):
            fh.readline()
        if byte_skip > 0:
            fh.seek(byte_skip, 1)  # relative seek
        elif byte_skip < 0:
            raise ValueError("Negative byte skip not supported for zero-copy")

        # Read raw data blob (compressed or raw bytes)
        raw_blob = fh.read()

    # Shape in NRRD order (fastest-first) -- NOT reversed
    sizes = [int(s) for s in header["sizes"]]
    shape = tuple(sizes)
    ndim = len(shape)

    # Build codecs matching the NRRD encoding
    serializer, compressors = _codecs_for_encoding(encoding)

    # Build metadata (NRRD order, no reversal)
    meta, dimension_names, extra_attrs = _header_to_metadata(
        header, ndim, reverse_axes=False
    )

    # Serialize metadata and add legacy info for round-trip
    nrrd_dict = meta.model_dump(exclude_none=True)
    if "extensions" not in nrrd_dict:
        nrrd_dict["extensions"] = {}
    nrrd_dict["extensions"]["legacy"] = {
        "nrrd_type": header["type"],
        "encoding": encoding,
    }

    attrs: dict[str, Any] = {"nrrd": nrrd_dict}
    attrs.update(extra_attrs)

    # Remove existing store if overwriting
    if zarr_path.exists() and overwrite:
        shutil.rmtree(zarr_path)

    # Create Zarr array (metadata only, no data written)
    store = zarr.storage.LocalStore(str(zarr_path))
    zarr.create_array(
        store,
        shape=shape,
        dtype=dtype,
        chunks=shape,  # single chunk = full array
        serializer=serializer,
        compressors=compressors,
        dimension_names=dimension_names,
        attributes=attrs,
        fill_value=0,
    )

    # Write raw blob directly as the single chunk file
    chunk_path = zarr_path / "c"
    for _ in range(ndim):
        chunk_path = chunk_path / "0"
    chunk_path.parent.mkdir(parents=True, exist_ok=True)
    chunk_path.write_bytes(raw_blob)


# ---------------------------------------------------------------------------
# Zero-copy: nrrdz Zarr -> NRRD
# ---------------------------------------------------------------------------


def zarr_to_nrrd_zerocopy(
    zarr_path: str | Path,
    nrrd_path: str | Path,
    *,
    overwrite: bool = False,
) -> None:
    """Convert a nrrdz Zarr v3 store to an NRRD file using zero-copy.

    Copies the chunk data blob directly into the NRRD file without
    decompression or recompression.  Requires the store to have been
    created by ``nrrd_to_zarr_zerocopy`` (single chunk, NRRD axis order,
    ``legacy`` metadata present).
    """
    zarr_path = Path(zarr_path)
    nrrd_path = Path(nrrd_path)

    if nrrd_path.exists() and not overwrite:
        raise FileExistsError(f"{nrrd_path} already exists (use --overwrite)")

    store = zarr.storage.LocalStore(str(zarr_path))
    arr = zarr.open_array(store, mode="r")
    nrrd_attrs = arr.attrs.get("nrrd", {})
    meta = NrrdMetadata(**nrrd_attrs)

    # Get legacy info
    legacy: dict[str, Any] = {}
    if meta.extensions:
        legacy = meta.extensions.get("legacy", {})

    # Determine encoding and NRRD type
    encoding = legacy.get("encoding", "raw")
    nrrd_type = legacy.get("nrrd_type")
    if nrrd_type is None:
        dtype_str = str(arr.dtype)
        nrrd_type = _DTYPE_TO_NRRD_TYPE.get(dtype_str)
        if nrrd_type is None:
            raise ValueError(f"Cannot map dtype {dtype_str} to NRRD type")

    # Shape is in NRRD order (fastest-first)
    shape = arr.shape
    ndim = arr.ndim
    sizes = list(shape)

    # Read chunk file as raw bytes
    chunk_path = zarr_path / "c"
    for _ in range(ndim):
        chunk_path = chunk_path / "0"
    if not chunk_path.exists():
        raise FileNotFoundError(f"Chunk file not found: {chunk_path}")
    raw_blob = chunk_path.read_bytes()

    # Build NRRD header (no axis reversal -- already in NRRD order)
    dim_names = arr.metadata.dimension_names
    content = arr.attrs.get("content")
    header = _metadata_to_header(
        meta,
        reverse_axes=False,
        dim_names=dim_names,
        content=content,
    )

    # Write NRRD file: header + raw data blob
    with open(nrrd_path, "wb") as fh:
        _write_nrrd_header(
            fh,
            sizes=sizes,
            nrrd_type=nrrd_type,
            encoding=encoding,
            itemsize=arr.dtype.itemsize,
            header=header,
        )
        fh.write(raw_blob)


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
