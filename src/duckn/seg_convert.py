"""Convert between segmentation representations.

- 4D binary channels (one layer per segment) → 3D labelmap
- 3D labelmap → 4D binary channels
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


def seg_4d_to_labelmap(
    input_source: str | Path | Any,
    output_path: str | Path,
    *,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
) -> None:
    """Convert a 4D binary segmentation to a 3D labelmap.

    Each segment in the 4D array (one binary channel per segment)
    is assigned a unique integer label. The output is a 3D volume
    where each voxel's value identifies its segment.

    Non-overlapping segments are required — if multiple segments
    claim the same voxel, the last segment (highest index) wins.

    Parameters
    ----------
    input_source : path to a duckn Zarr store, or a Zarr Store object
        (e.g. ZMPStore)
    output_path : path for the output 3D labelmap Zarr store
    compressor : "zstd", "gzip", or "none"
    level : compression level
    overwrite : if True, overwrite existing output
    """
    output_path = Path(output_path)

    data_4d, meta = read_duckn(input_source)

    if data_4d.ndim != 4:
        raise ValueError(f"Expected 4D input, got {data_4d.ndim}D")

    n_segments = data_4d.shape[0]
    spatial_shape = data_4d.shape[1:]

    # Merge to labelmap — last writer wins
    labelmap = np.zeros(spatial_shape, dtype=np.uint8 if n_segments < 256 else np.uint16)
    for si in range(n_segments):
        labelmap[data_4d[si] > 0] = si + 1

    # Update segment metadata: convert from layer-based to labelmap
    seg_ext = None
    if meta.extensions and "seg" in meta.extensions:
        seg_ext = SegmentationExtension(**meta.extensions["seg"])
        for i, seg in enumerate(seg_ext.segments):
            # Replace layer/label_value=1 with label_value=i+1, no layer
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

    # Write
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


def labelmap_to_seg_4d(
    input_source: str | Path | Any,
    output_path: str | Path,
    *,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
) -> None:
    """Convert a 3D labelmap to a 4D binary segmentation.

    Each unique non-zero label becomes a binary channel in the output.
    The segment axis is prepended as a ``list`` axis.

    Parameters
    ----------
    input_path : path to the input 3D labelmap duckn Zarr store
    output_path : path for the output 4D binary segmentation Zarr store
    compressor : "zstd", "gzip", or "none"
    level : compression level
    overwrite : if True, overwrite existing output
    """
    output_path = Path(output_path)

    data_3d, meta = read_duckn(input_source)

    if data_3d.ndim != 3:
        raise ValueError(f"Expected 3D input, got {data_3d.ndim}D")

    # Get segment info
    seg_ext = None
    segments = []
    if meta.extensions and "seg" in meta.extensions:
        seg_ext = SegmentationExtension(**meta.extensions["seg"])
        segments = seg_ext.segments

    # Find all unique labels
    labels = sorted(set(int(v) for v in np.unique(data_3d) if v != 0))
    n_segments = len(labels)

    # Build 4D binary array
    data_4d = np.zeros((n_segments, *data_3d.shape), dtype=np.uint8)
    for i, label in enumerate(labels):
        data_4d[i] = (data_3d == label).astype(np.uint8)

    # Update segment metadata: convert from labelmap to layer-based
    if seg_ext:
        for i, seg in enumerate(segments):
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

    # Write with one chunk per segment
    chunks = (1, data_3d.shape[0], data_3d.shape[1], data_3d.shape[2])
    compressors_list = _build_compressors(compressor, level)
    attrs = {"duckn": new_meta.model_dump(exclude_none=True)}

    is_zip = _is_zip_path(output_path)
    with open_store(output_path, mode="w", overwrite=overwrite) as store:
        zarr.create_array(
            store,
            data=data_4d,
            chunks=chunks,
            compressors=compressors_list,
            dimension_names=["segment", "k", "j", "i"],
            attributes=attrs,
            overwrite=False if is_zip else overwrite,
            fill_value=0,
        )
