"""Build a ZMP (Zarr Manifest Parquet) for an IDC DICOM series.

Given a CRDC series UUID, fetches DICOM headers via HTTP range requests,
computes spatial geometry, and creates a ZMP manifest with virtual entries
pointing to pixel data byte ranges in the IDC public S3 bucket.

No full DICOM files are downloaded — only headers (~5KB each) are fetched.
The resulting ZMP can be opened as a Zarr store where each chunk is lazily
fetched from S3 via byte-range requests.

Requires httpx and zarr-zmp: ``pip install httpx zarr-zmp``
"""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import numpy as np

from .dicom_convert import (
    _UNCOMPRESSED_TRANSFER_SYNTAXES,
    _build_nrrd_metadata,
    _geometry_from_headers,
    DicomGeometry,
)

IDC_S3_BASE = "https://idc-open-data.s3.amazonaws.com"


# ---------------------------------------------------------------------------
# S3 listing
# ---------------------------------------------------------------------------


def _list_series_files(
    series_uuid: str,
    *,
    base_url: str = IDC_S3_BASE,
    client: Any = None,
) -> list[str]:
    """List .dcm file keys in an IDC series via S3 ListObjectsV2."""
    import httpx

    _client = client or httpx.Client(timeout=30)
    keys: list[str] = []
    continuation_token = None

    while True:
        params: dict[str, str] = {
            "list-type": "2",
            "prefix": f"{series_uuid}/",
        }
        if continuation_token:
            params["continuation-token"] = continuation_token

        resp = _client.get(base_url, params=params)
        resp.raise_for_status()

        root = ElementTree.fromstring(resp.text)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

        for content in root.findall("s3:Contents", ns):
            key_el = content.find("s3:Key", ns)
            if key_el is not None and key_el.text and key_el.text.endswith(".dcm"):
                keys.append(key_el.text)

        # Check for truncation
        is_truncated = root.findtext("s3:IsTruncated", namespaces=ns)
        if is_truncated == "true":
            continuation_token = root.findtext(
                "s3:NextContinuationToken", namespaces=ns
            )
        else:
            break

    if client is None:
        _client.close()

    if not keys:
        raise FileNotFoundError(
            f"No .dcm files found for series UUID {series_uuid} at {base_url}"
        )

    return keys


# ---------------------------------------------------------------------------
# Header fetching via range requests
# ---------------------------------------------------------------------------


_CHUNK_SIZES = [5120, 7680, 10240, 51200, 51200, 524288, 10 * 1024 * 1024]


def _fetch_header(
    url: str,
    *,
    client: Any,
) -> tuple[Any, int, int, int]:
    """Fetch a DICOM header via progressive range requests.

    Returns (dataset, file_size, pixel_offset, pixel_length).
    """
    import pydicom

    accumulated = b""

    for chunk_size in _CHUNK_SIZES:
        start = len(accumulated)
        end = start + chunk_size - 1
        resp = client.get(url, headers={"Range": f"bytes={start}-{end}"})

        if resp.status_code == 206:
            accumulated += resp.content
            # Parse content-range for total file size
            cr = resp.headers.get("content-range", "")
            file_size = int(cr.split("/")[-1]) if "/" in cr else 0
        elif resp.status_code == 200:
            # Server returned full file (no range support or small file)
            accumulated = resp.content
            file_size = len(accumulated)
        else:
            resp.raise_for_status()

        try:
            ds = pydicom.dcmread(
                BytesIO(accumulated), stop_before_pixels=True, force=True
            )
            if not hasattr(ds, "Rows"):
                continue

            # Get pixel data offset
            pixel_offset, pixel_length = _find_pixel_range(
                accumulated, ds, file_size
            )
            return ds, file_size, pixel_offset, pixel_length
        except Exception:
            continue

    raise ValueError(f"Failed to parse DICOM header from {url}")


def _find_pixel_range(
    header_bytes: bytes,
    ds: Any,
    file_size: int,
) -> tuple[int, int]:
    """Find pixel data offset and length from partial DICOM bytes."""
    # Search for PixelData tag (7FE0,0010) in the accumulated bytes
    tag_bytes = b"\xe0\x7f\x10\x00"  # little-endian (7FE0,0010)
    pos = header_bytes.rfind(tag_bytes)

    if pos < 0:
        # Tag not in the fetched range — estimate from file size
        rows = int(ds.Rows)
        cols = int(ds.Columns)
        bits = int(ds.BitsAllocated)
        pixel_length = rows * cols * (bits // 8)
        pixel_offset = file_size - pixel_length
        return pixel_offset, pixel_length

    tsuid = str(getattr(ds.file_meta, "TransferSyntaxUID", ""))
    is_implicit = tsuid == "1.2.840.10008.1.2"

    if is_implicit:
        # tag(4) + length(4) = 8
        length_start = pos + 4
        if length_start + 4 <= len(header_bytes):
            pixel_length = int.from_bytes(
                header_bytes[length_start : length_start + 4], "little"
            )
        else:
            rows, cols, bits = int(ds.Rows), int(ds.Columns), int(ds.BitsAllocated)
            pixel_length = rows * cols * (bits // 8)
        pixel_offset = pos + 8
    else:
        # tag(4) + VR(2) + reserved(2) + length(4) = 12
        length_start = pos + 8
        if length_start + 4 <= len(header_bytes):
            pixel_length = int.from_bytes(
                header_bytes[length_start : length_start + 4], "little"
            )
        else:
            rows, cols, bits = int(ds.Rows), int(ds.Columns), int(ds.BitsAllocated)
            pixel_length = rows * cols * (bits // 8)
        pixel_offset = pos + 12

    if pixel_length == 0xFFFFFFFF:
        pixel_length = file_size - pixel_offset

    return pixel_offset, pixel_length


# ---------------------------------------------------------------------------
# ZMP builder
# ---------------------------------------------------------------------------


@dataclass
class _SliceInfo:
    """Per-slice data collected during header scan."""

    url: str
    dataset: Any
    file_size: int
    pixel_offset: int
    pixel_length: int
    projection: float


def build_idc_zmp(
    series_uuid: str,
    output_path: str | Path,
    *,
    base_url: str = IDC_S3_BASE,
    tags: bool = True,
    binary_tags: bool = False,
    content_hash: bool = False,
    inline_data: bool = False,
    overwrite: bool = False,
) -> Path:
    """Build a ZMP manifest for an IDC DICOM series.

    Fetches only DICOM headers via HTTP range requests unless inline_data
    is True, in which case pixel data is also fetched and stored directly
    in the ZMP file.

    Parameters
    ----------
    series_uuid : CRDC series UUID (e.g. "bfa2aab6-85de-4f92-b311-e6c8a52b9299")
    output_path : path for the output .zmp file
    base_url : base URL for the IDC bucket (default: public S3)
    tags : if True, include DICOM tags in metadata
    binary_tags : if True, include binary VR tags as base64
    content_hash : if True, compute git-sha1 retrieval keys for chunks.
        When inline_data is True, hashes are computed from the fetched data
        at no extra cost. When False, requires separate range requests.
    inline_data : if True, fetch pixel data and store it inline in the
        ZMP file's ``data`` column. Creates a self-contained archive.
        Implies content_hash=True.
    overwrite : if True, overwrite existing file

    Returns
    -------
    Path to the created .zmp file
    """
    import httpx
    from zarr_zmp import ZMPBuilder

    if inline_data:
        content_hash = True  # free when we already have the bytes

    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists")

    client = httpx.Client(timeout=60)

    try:
        # Phase 1: list files
        keys = _list_series_files(series_uuid, base_url=base_url, client=client)

        # Phase 2: fetch headers and pixel data ranges
        slices: list[_SliceInfo] = []
        for key in keys:
            url = f"{base_url}/{key}"
            ds, file_size, pixel_offset, pixel_length = _fetch_header(
                url, client=client
            )
            slices.append(
                _SliceInfo(
                    url=url,
                    dataset=ds,
                    file_size=file_size,
                    pixel_offset=pixel_offset,
                    pixel_length=pixel_length,
                    projection=0.0,  # filled after sorting
                )
            )

        # Validate
        if not slices:
            raise ValueError(f"No valid DICOM files found for {series_uuid}")

        # Check transfer syntax
        tsuid = str(
            getattr(slices[0].dataset.file_meta, "TransferSyntaxUID", "")
        )
        if tsuid not in _UNCOMPRESSED_TRANSFER_SYNTAXES:
            raise ValueError(
                f"Series uses compressed transfer syntax {tsuid}. "
                f"ZMP virtual references require uncompressed DICOM."
            )

        # Sort by slice position
        ds0 = slices[0].dataset
        iop = [float(x) for x in ds0.ImageOrientationPatient]
        row_cos = np.array(iop[:3])
        col_cos = np.array(iop[3:])
        slice_normal = np.cross(row_cos, col_cos)
        nrm = np.linalg.norm(slice_normal)
        if nrm > 0:
            slice_normal = slice_normal / nrm

        for s in slices:
            pos = np.array(
                [float(x) for x in s.dataset.ImagePositionPatient]
            )
            s.projection = float(np.dot(pos, slice_normal))

        slices.sort(key=lambda s: s.projection)

        # Validate uniform dimensions
        shapes = {(int(s.dataset.Rows), int(s.dataset.Columns)) for s in slices}
        if len(shapes) > 1:
            raise ValueError(f"Non-uniform slice dimensions: {shapes}")

        # Phase 3: compute geometry and metadata
        headers = [s.dataset for s in slices]
        geometry = _geometry_from_headers(headers)
        meta = _build_nrrd_metadata(geometry, headers, None, tags, binary_tags)
        n_slices, rows, cols = geometry.shape

        # Phase 4: build zarr.json
        ds0 = slices[0].dataset
        bits = int(ds0.BitsAllocated)
        signed = int(ds0.PixelRepresentation)
        _dtype_map = {
            (8, 0): "uint8", (8, 1): "int8",
            (16, 0): "uint16", (16, 1): "int16",
            (32, 0): "uint32", (32, 1): "int32",
        }
        dtype_str = _dtype_map.get((bits, signed), "uint16")

        zarr_meta = {
            "zarr_format": 3,
            "node_type": "array",
            "shape": [n_slices, rows, cols],
            "data_type": dtype_str,
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": [1, rows, cols]},
            },
            "chunk_key_encoding": {
                "name": "default",
                "configuration": {"separator": "/"},
            },
            "fill_value": 0,
            "codecs": [
                {
                    "name": "bytes",
                    "configuration": {"endian": "little"},
                }
            ],
            "attributes": {"nrrd": meta.model_dump(exclude_none=True)},
            "dimension_names": ["k", "j", "i"],
        }

        zarr_json_text = json.dumps(zarr_meta)
        pixel_bytes_per_slice = rows * cols * (bits // 8)

        # Phase 5: build ZMP via ZMPBuilder
        builder = ZMPBuilder(
            metadata={"idc_series_uuid": series_uuid},
        )
        builder.add_metadata("zarr.json", zarr_json_text)

        for k, s in enumerate(slices):
            chunk_path = f"c/{k}/0/0"

            if inline_data or content_hash:
                # Fetch pixel data
                resp = client.get(
                    s.url,
                    headers={
                        "Range": f"bytes={s.pixel_offset}-{s.pixel_offset + pixel_bytes_per_slice - 1}"
                    },
                )
                resp.raise_for_status()
                pixel_data = resp.content

            if inline_data:
                builder.add_chunk(chunk_path, data=pixel_data, source=s.url)
            elif content_hash:
                from zarr_zmp.builder import _git_blob_hash

                builder.add_virtual(
                    chunk_path,
                    uri=s.url,
                    offset=s.pixel_offset,
                    length=pixel_bytes_per_slice,
                    source_size=s.file_size,
                    retrieval_key=_git_blob_hash(pixel_data),
                )
            else:
                builder.add_virtual(
                    chunk_path,
                    uri=s.url,
                    offset=s.pixel_offset,
                    length=pixel_bytes_per_slice,
                    source_size=s.file_size,
                )

        if output_path.exists() and overwrite:
            output_path.unlink()

        builder.write(output_path)

    finally:
        client.close()

    return output_path
