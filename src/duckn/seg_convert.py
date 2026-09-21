"""Convert between segmentation representations.

DICOM defines two segmentation types:
- BINARY: multi-channel, one binary mask per segment (4D)
- LABELMAP: single-channel, integer labels per voxel (3D)

This module provides both in-memory array functions and file I/O wrappers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import zarr

from .models import (
    AxisKind,
    AxisMetadata,
    DucknMetadata,
    SampleMetadata,
)
from .seg_model import seg_for_derived_array
from .seg_read import read_seg_extension
from .zarr_io import open_store, read_duckn, _is_zip_path
from .convert import _auto_chunks, _build_compressors


# ---------------------------------------------------------------------------
# In-memory conversions
# ---------------------------------------------------------------------------


def seg_binary_to_labelmap(
    data: np.ndarray,
    meta: DucknMetadata,
) -> tuple[np.ndarray, DucknMetadata]:
    """Convert a 4D binary segmentation to a 3D integer labelmap.

    Each segment is assigned a unique integer label, in ``segments`` order.
    **Lossy where segments overlap**: a voxel several segments claim goes to
    the last of them, and the others lose it. To keep overlap, export with
    ``seg_nrrd.export_seg_nrrd`` or keep the layered array.

    Parameters
    ----------
    data : 4D uint8 array (n_segments, z, y, x)
    meta : duckn metadata for the 4D array

    Returns
    -------
    (labelmap, new_meta) : 3D array and updated metadata
    """
    if data.ndim != 4:
        raise ValueError(f"Expected 4D input, got {data.ndim}D")

    spatial_shape = data.shape[1:]

    # An older extension (a DICOM import's) is migrated on the way in.
    seg_ext = None
    if meta.extensions and "seg" in meta.extensions:
        seg_ext, _ = read_seg_extension(meta.extensions["seg"])
        # A background segment is what the labelmap's 0 already says.
        structures = [s for s in seg_ext.segments if s.role != "background"]
        channels = [(s.effective_layer, sorted(s.values)) for s in structures]
    else:
        channels = [(si, None) for si in range(data.shape[0])]

    # Merge to labelmap — last writer wins
    # n + 1 values needed (0 = background, 1..n = segments)
    n_labels = len(channels) + 1
    if n_labels <= 256:
        dtype = np.uint8
    elif n_labels <= 65536:
        dtype = np.uint16
    else:
        dtype = np.uint32
    labelmap = np.zeros(spatial_shape, dtype=dtype)
    for i, (layer, values) in enumerate(channels):
        mask = data[layer] > 0 if values is None else np.isin(data[layer], values)
        labelmap[mask] = i + 1

    # Update segment metadata. Overlap was resolved last-writer-wins, so a
    # cached extent may no longer hold, and the source strings are stale (§3.3).
    if seg_ext is not None:
        for i, seg in enumerate(structures):
            seg.label_values = [i + 1]
            seg.layer = None
            seg.extent = None
        seg_ext.segments = structures
        seg_ext.implicit_background = None
        seg_ext.legacy = None

    # Build 3D metadata — drop the list axis, keep spatial axes
    spatial_axes = [ax for ax in meta.axes if ax.space_direction is not None]

    extensions: dict[str, Any] = {}
    if seg_ext:
        # the voxels changed: what was in voxel coordinates goes (seg spec §3.3)
        extensions["seg"] = seg_for_derived_array(seg_ext.model_dump(exclude_none=True))
    if meta.extensions:
        for key, val in meta.extensions.items():
            if key != "seg":
                extensions[key] = val

    new_meta = DucknMetadata(
        version=meta.version,
        space=meta.space,
        space_origin=meta.space_origin,
        axes=spatial_axes,
        extensions=extensions or None,
    )

    return labelmap, new_meta


def seg_labelmap_to_binary(
    data: np.ndarray,
    meta: DucknMetadata,
) -> tuple[np.ndarray, DucknMetadata]:
    """Convert a 3D integer labelmap to a 4D binary segmentation.

    Each segment becomes a binary channel holding the voxels of all its
    values, so segments that share values overlap in the result. Without a
    seg extension, each unique non-zero label becomes a channel.

    Parameters
    ----------
    data : 3D integer array (z, y, x)
    meta : duckn metadata for the 3D array

    Returns
    -------
    (binary_4d, new_meta) : 4D uint8 array and updated metadata
    """
    if data.ndim != 3:
        raise ValueError(f"Expected 3D input, got {data.ndim}D")

    # One channel per segment; without a seg extension, one per non-zero label.
    seg_ext = None
    if meta.extensions and "seg" in meta.extensions:
        seg_ext, _ = read_seg_extension(meta.extensions["seg"])
        structures = [s for s in seg_ext.segments if s.role != "background"]
        channels = [sorted(s.values) for s in structures]
    else:
        channels = [[label] for label in sorted(set(int(v) for v in np.unique(data) if v != 0))]

    # Build 4D binary array
    binary = np.zeros((len(channels), *data.shape), dtype=np.uint8)
    for i, values in enumerate(channels):
        binary[i] = np.isin(data, values).astype(np.uint8)

    # Update segment metadata: each segment is value 1 of its own layer, whose
    # 0 is its background. The voxels are unchanged, so `extent` still holds.
    if seg_ext is not None:
        for i, seg in enumerate(structures):
            seg.label_values = [1]
            seg.layer = i or None
        seg_ext.segments = structures
        seg_ext.implicit_background = None
        seg_ext.legacy = None

    # Build 4D metadata — prepend list axis
    axes = [AxisMetadata(kind=AxisKind.LIST)] + list(meta.axes)

    extensions: dict[str, Any] = {}
    if seg_ext:
        extensions["seg"] = seg_ext.model_dump(exclude_none=True)
    if meta.extensions:
        for key, val in meta.extensions.items():
            if key != "seg":
                extensions[key] = val

    new_meta = DucknMetadata(
        version=meta.version,
        space=meta.space,
        space_origin=meta.space_origin,
        axes=axes,
        extensions=extensions or None,
    )

    return binary, new_meta


# ---------------------------------------------------------------------------
# File I/O wrappers
# ---------------------------------------------------------------------------


def write_seg_binary_to_labelmap(
    input_source: str | Path | Any,
    output_path: str | Path,
    *,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
) -> None:
    """Convert a 4D binary segmentation file to a 3D labelmap file.

    Parameters
    ----------
    input_source : path to a duckn Zarr store, or a Zarr Store object
    output_path : path for the output 3D labelmap Zarr store
    compressor : "zstd", "gzip", or "none"
    level : compression level
    overwrite : if True, overwrite existing output
    """
    output_path = Path(output_path)
    data_4d, meta = read_duckn(input_source)
    labelmap, new_meta = seg_binary_to_labelmap(data_4d, meta)

    chunks = _auto_chunks(labelmap.shape, labelmap.dtype)
    compressors_list = _build_compressors(compressor, level)
    attrs = {"duckn": new_meta.model_dump(exclude_none=True)}

    is_zip = _is_zip_path(output_path)
    with open_store(output_path, mode="w", overwrite=overwrite) as store:
        zarr.create_array(
            store,
            data=labelmap,
            chunks=chunks,
            compressors=compressors_list,
            dimension_names=["k", "j", "i"],
            attributes=attrs,
            overwrite=False if is_zip else overwrite,
            fill_value=0,
        )


def write_seg_labelmap_to_binary(
    input_source: str | Path | Any,
    output_path: str | Path,
    *,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
) -> None:
    """Convert a 3D labelmap file to a 4D binary segmentation file.

    Parameters
    ----------
    input_source : path to a duckn Zarr store, or a Zarr Store object
    output_path : path for the output 4D binary segmentation Zarr store
    compressor : "zstd", "gzip", or "none"
    level : compression level
    overwrite : if True, overwrite existing output
    """
    output_path = Path(output_path)
    data_3d, meta = read_duckn(input_source)
    binary, new_meta = seg_labelmap_to_binary(data_3d, meta)

    chunks = (1, data_3d.shape[0], data_3d.shape[1], data_3d.shape[2])
    compressors_list = _build_compressors(compressor, level)
    attrs = {"duckn": new_meta.model_dump(exclude_none=True)}

    is_zip = _is_zip_path(output_path)
    with open_store(output_path, mode="w", overwrite=overwrite) as store:
        zarr.create_array(
            store,
            data=binary,
            chunks=chunks,
            compressors=compressors_list,
            dimension_names=["segment", "k", "j", "i"],
            attributes=attrs,
            overwrite=False if is_zip else overwrite,
            fill_value=0,
        )
