"""Build a ZMP manifest for a DICOM Whole Slide Image.

Creates virtual references to JPEG 2000 (or JPEG) compressed tiles
within DICOM WSI files. Each tile maps to a Zarr chunk, with the
image codec declared so readers can decode the compressed bytes
directly.

Supports multi-level pyramids (one DICOM instance per level) and
both local files and remote URLs.
"""

from __future__ import annotations

import json
import struct
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np


# Transfer syntax → codec mapping
_TS_TO_CODEC = {
    "1.2.840.10008.1.2.4.90": {  # JPEG 2000 Image Compression (Lossless Only)
        "name": "imagecodecs_jpeg2k",
        "configuration": {},
    },
    "1.2.840.10008.1.2.4.91": {  # JPEG 2000 Image Compression
        "name": "imagecodecs_jpeg2k",
        "configuration": {},
    },
    "1.2.840.10008.1.2.4.50": {  # JPEG Baseline
        "name": "imagecodecs_jpeg8",
        "configuration": {},
    },
    "1.2.840.10008.1.2.4.51": {  # JPEG Extended
        "name": "imagecodecs_jpeg8",
        "configuration": {},
    },
    "1.2.840.10008.1.2.4.57": {  # JPEG Lossless, Non-Hierarchical
        "name": "imagecodecs_jpeg8",
        "configuration": {},
    },
    "1.2.840.10008.1.2.4.70": {  # JPEG Lossless, SV1 (most common lossless CT)
        "name": "imagecodecs_jpeg8",
        "configuration": {},
    },
    "1.2.840.10008.1.2.4.80": {  # JPEG-LS Lossless
        "name": "imagecodecs_jpegls",
        "configuration": {},
    },
    "1.2.840.10008.1.2.4.81": {  # JPEG-LS Near-Lossless
        "name": "imagecodecs_jpegls",
        "configuration": {},
    },
}


def _scan_frame_offsets(
    fh: Any,
    n_frames: int,
) -> list[tuple[int, int]]:
    """Scan encapsulated pixel data for per-frame byte offsets.

    Call with file handle positioned at the start of the PixelData tag.

    Returns list of (file_offset, length) for each frame's compressed data.
    """
    # Skip pixel data tag header: tag(4) + VR(2) + reserved(2) + length(4) = 12
    # or implicit VR: tag(4) + length(4) = 8
    tag = fh.read(4)
    next2 = fh.read(2)

    # Check if explicit VR (next 2 bytes are ASCII letters)
    if next2[0:1].isalpha() and next2[1:2].isalpha():
        # Explicit VR: skip reserved(2) + length(4)
        fh.read(2)  # reserved
        fh.read(4)  # undefined length (FFFFFFFF)
    else:
        # Implicit VR: next2 is part of length, read 2 more
        fh.read(2)  # remaining length bytes

    # Basic Offset Table item: tag(4) + length(4)
    bot_tag = fh.read(4)
    bot_len = struct.unpack("<I", fh.read(4))[0]
    if bot_len > 0:
        fh.seek(bot_len, 1)  # skip BOT data

    # Scan frame items
    offsets: list[tuple[int, int]] = []
    for _ in range(n_frames):
        item_tag = fh.read(4)
        if len(item_tag) < 4 or item_tag == b"\xfe\xff\xdd\xe0":
            break  # sequence delimiter or EOF
        item_len = struct.unpack("<I", fh.read(4))[0]
        data_offset = fh.tell()
        offsets.append((data_offset, item_len))
        fh.seek(item_len, 1)

    return offsets


def _parse_wsi_level(
    path: str | Path,
    *,
    uri: str | None = None,
) -> dict[str, Any]:
    """Parse one DICOM WSI instance (one pyramid level).

    Returns a dict with geometry, tile layout, and frame byte offsets.
    """
    import pydicom

    path = Path(path)
    ds = pydicom.dcmread(str(path), stop_before_pixels=True)

    total_rows = int(getattr(ds, "TotalPixelMatrixRows", ds.Rows))
    total_cols = int(getattr(ds, "TotalPixelMatrixColumns", ds.Columns))
    tile_rows = int(ds.Rows)
    tile_cols = int(ds.Columns)
    n_frames = int(getattr(ds, "NumberOfFrames", 1))
    samples = int(ds.SamplesPerPixel)
    bits = int(ds.BitsAllocated)
    tsuid = str(ds.file_meta.TransferSyntaxUID)

    # Pixel spacing from shared functional groups
    pixel_spacing = [1.0, 1.0]
    shared = getattr(ds, "SharedFunctionalGroupsSequence", None)
    if shared and len(shared) > 0:
        pm = getattr(shared[0], "PixelMeasuresSequence", None)
        if pm and len(pm) > 0:
            pixel_spacing = [float(x) for x in pm[0].PixelSpacing]

    # Tile grid dimensions
    tiles_y = -(-total_rows // tile_rows)  # ceil division
    tiles_x = -(-total_cols // tile_cols)

    # Image type
    image_type = list(getattr(ds, "ImageType", []))

    # Get frame byte offsets
    is_encapsulated = tsuid not in (
        "1.2.840.10008.1.2",
        "1.2.840.10008.1.2.1",
        "1.2.840.10008.1.2.2",
    )

    frame_offsets: list[tuple[int, int]] = []
    if is_encapsulated and n_frames > 0:
        with open(path, "rb") as fh:
            pydicom.dcmread(fh, stop_before_pixels=True)
            frame_offsets = _scan_frame_offsets(fh, n_frames)

    return {
        "path": str(path),
        "uri": uri or str(path),
        "total_rows": total_rows,
        "total_cols": total_cols,
        "tile_rows": tile_rows,
        "tile_cols": tile_cols,
        "tiles_y": tiles_y,
        "tiles_x": tiles_x,
        "n_frames": n_frames,
        "samples": samples,
        "bits": bits,
        "tsuid": tsuid,
        "pixel_spacing": pixel_spacing,
        "image_type": image_type,
        "frame_offsets": frame_offsets,
        "is_encapsulated": is_encapsulated,
        "dataset": ds,
    }


def build_wsi_zmp(
    input_paths: list[str | Path],
    output_path: str | Path,
    *,
    uris: list[str] | None = None,
    overwrite: bool = False,
) -> Path:
    """Build a ZMP manifest for a DICOM Whole Slide Image pyramid.

    Each input path is one DICOM instance (one pyramid level). The
    resulting ZMP contains a Zarr group with one array per level,
    where each chunk is a virtual reference to a compressed tile
    within the DICOM file.

    Parameters
    ----------
    input_paths : list of paths to DICOM WSI instances (one per level),
        ordered from highest to lowest resolution
    output_path : path for the output .zmp file
    uris : optional list of URIs for each input (for remote references).
        If None, local file paths are used.
    overwrite : if True, overwrite existing file
    """
    from zarr_zmp import Builder as ZMPBuilder

    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists")

    if uris is None:
        uris = [str(p) for p in input_paths]

    # Parse all levels
    levels = []
    for path, uri in zip(input_paths, uris):
        level = _parse_wsi_level(path, uri=uri)
        # Skip thumbnails and labels
        if "THUMBNAIL" in level["image_type"] or "LABEL" in level["image_type"]:
            continue
        levels.append(level)

    # Sort by resolution (highest first = most total pixels)
    levels.sort(key=lambda lv: lv["total_rows"] * lv["total_cols"], reverse=True)

    if not levels:
        raise ValueError("No VOLUME levels found in input files")

    builder = ZMPBuilder()

    # Build OME-NGFF multiscales metadata for the root group
    # so OME-Zarr viewers can discover and navigate the pyramid
    ome_datasets = []
    for level_idx, level in enumerate(levels):
        ps = level["pixel_spacing"]
        downsample = (
            levels[0]["total_cols"] / level["total_cols"]
            if level_idx > 0 else 1.0
        )
        ome_datasets.append({
            "path": str(level_idx),
            "coordinateTransformations": [
                {"type": "scale", "scale": [ps[0], ps[1]]},
            ],
        })

    ps0 = levels[0]["pixel_spacing"]
    ome_multiscales = [{
        "version": "0.4",
        "name": "slide",
        "axes": [
            {"name": "y", "type": "space", "unit": "millimeter"},
            {"name": "x", "type": "space", "unit": "millimeter"},
        ],
        "datasets": ome_datasets,
        "type": "gaussian",
    }]

    # Root group with both duckn and OME-NGFF metadata
    root_meta = {
        "zarr_format": 3,
        "node_type": "group",
        "attributes": {
            "multiscales": ome_multiscales,
        },
    }
    builder.add("zarr.json", text=json.dumps(root_meta))

    for level_idx, level in enumerate(levels):
        array_prefix = f"{level_idx}/"
        total_rows = level["total_rows"]
        total_cols = level["total_cols"]
        tile_rows = level["tile_rows"]
        tile_cols = level["tile_cols"]
        samples = level["samples"]
        bits = level["bits"]
        tiles_y = level["tiles_y"]
        tiles_x = level["tiles_x"]

        # Determine codec
        tsuid = level["tsuid"]
        codec = _TS_TO_CODEC.get(tsuid)
        if codec is None and level["is_encapsulated"]:
            raise ValueError(
                f"Unsupported transfer syntax {tsuid} for level {level_idx}"
            )

        # Array shape: (total_rows, total_cols, samples) for RGB
        # or (total_rows, total_cols) for grayscale
        if samples > 1:
            shape = [total_rows, total_cols, samples]
            chunk_shape = [tile_rows, tile_cols, samples]
            dim_names = ["y", "x", "s"]
        else:
            shape = [total_rows, total_cols]
            chunk_shape = [tile_rows, tile_cols]
            dim_names = ["y", "x"]

        dtype = "uint8" if bits == 8 else "uint16"

        # Build codecs list
        codecs = []
        if codec:
            codecs.append(codec)
        else:
            codecs.append({"name": "bytes", "configuration": {"endian": "little"}})

        # Pixel spacing → space_direction
        ps = level["pixel_spacing"]
        if samples > 1:
            axes = [
                {"kind": "space", "centering": "cell",
                 "space_direction": [0, ps[0]], "unit": "mm"},
                {"kind": "space", "centering": "cell",
                 "space_direction": [ps[1], 0], "unit": "mm"},
                {"kind": "RGB-color"},
            ]
        else:
            axes = [
                {"kind": "space", "centering": "cell",
                 "space_direction": [0, ps[0]], "unit": "mm"},
                {"kind": "space", "centering": "cell",
                 "space_direction": [ps[1], 0], "unit": "mm"},
            ]

        # Build level zarr.json
        level_meta = {
            "zarr_format": 3,
            "node_type": "array",
            "shape": shape,
            "data_type": dtype,
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": chunk_shape},
            },
            "chunk_key_encoding": {
                "name": "default",
                "configuration": {"separator": "/"},
            },
            "fill_value": 0,
            "codecs": codecs,
            "dimension_names": dim_names,
            "attributes": {
                "duckn": {
                    "version": "1.0",
                    "space_dimension": 2,
                    "axes": axes,
                },
                "wsi_level": {
                    "level": level_idx,
                    "downsample": round(
                        levels[0]["total_cols"] / total_cols, 2
                    ) if level_idx > 0 else 1.0,
                    "transfer_syntax": tsuid,
                },
            },
        }
        builder.add(f"{array_prefix}zarr.json", text=json.dumps(level_meta))

        # Add virtual chunk references for each tile
        if level["is_encapsulated"] and level["frame_offsets"]:
            for frame_idx, (offset, length) in enumerate(level["frame_offsets"]):
                # TILED_FULL: row-major order
                ty = frame_idx // tiles_x
                tx = frame_idx % tiles_x
                if samples > 1:
                    chunk_path = f"{array_prefix}c/{ty}/{tx}/0"
                else:
                    chunk_path = f"{array_prefix}c/{ty}/{tx}"
                builder.add(
                    chunk_path,
                    uri=level["uri"],
                    offset=offset,
                    length=length,
                    size=length,
                )

    if output_path.exists() and overwrite:
        output_path.unlink()

    builder.write(output_path)
    return output_path
