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
    SegmentationExtension,
)
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

    Each segment's binary channel is assigned a unique integer label.
    Non-overlapping segments assumed — if multiple segments claim the
    same voxel, the last segment (highest index) wins.

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

    n_segments = data.shape[0]
    spatial_shape = data.shape[1:]

    # Merge to labelmap — last writer wins
    # n_segments + 1 values needed (0 = background, 1..n = segments)
    n_labels = n_segments + 1
    if n_labels <= 256:
        dtype = np.uint8
    elif n_labels <= 65536:
        dtype = np.uint16
    else:
        dtype = np.uint32
    labelmap = np.zeros(spatial_shape, dtype=dtype)
    for si in range(n_segments):
        labelmap[data[si] > 0] = si + 1

    # Update segment metadata
    seg_ext = None
    if meta.extensions and "seg" in meta.extensions:
        seg_ext = SegmentationExtension(**meta.extensions["seg"])
        for i, seg in enumerate(seg_ext.segments):
            seg.label_value = i + 1
            seg.layer = None

    # Build 3D metadata — drop the list axis, keep spatial axes
    spatial_axes = [ax for ax in meta.axes if ax.space_direction is not None]

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
        axes=spatial_axes,
        extensions=extensions or None,
    )

    return labelmap, new_meta


def seg_labelmap_to_binary(
    data: np.ndarray,
    meta: DucknMetadata,
) -> tuple[np.ndarray, DucknMetadata]:
    """Convert a 3D integer labelmap to a 4D binary segmentation.

    Each unique non-zero label becomes a binary channel.

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

    # Find all unique labels
    labels = sorted(set(int(v) for v in np.unique(data) if v != 0))
    n_segments = len(labels)

    # Build 4D binary array
    binary = np.zeros((n_segments, *data.shape), dtype=np.uint8)
    for i, label in enumerate(labels):
        binary[i] = (data == label).astype(np.uint8)

    # Update segment metadata
    seg_ext = None
    if meta.extensions and "seg" in meta.extensions:
        seg_ext = SegmentationExtension(**meta.extensions["seg"])
        for i, seg in enumerate(seg_ext.segments):
            seg.label_value = 1
            seg.layer = i

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
