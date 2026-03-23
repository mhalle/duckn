"""Convert a Zarr ZIP store to a ZMP manifest with virtual references.

Each entry in the ZIP file becomes a ZMP entry that points back into the
ZIP via byte-range references. No data is extracted or copied.

Supports:
- ZIP_STORED (compress_type=0): raw bytes, no content_encoding
- ZIP_DEFLATED (compress_type=8): deflate-compressed, content_encoding="deflate"

Checksums are computed on the stored bytes (what the byte-range reference
points to), not the decompressed content. This is consistent with how
ZMP identifies content: the checksum matches the bytes you'd get from
fetching the reference.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path
from typing import Any


def _zip_data_offset(zip_path: str, info: zipfile.ZipInfo) -> int:
    """Calculate the byte offset of an entry's data within the ZIP file.

    The local file header is 30 bytes fixed, followed by variable-length
    filename and extra fields.
    """
    with open(zip_path, "rb") as f:
        f.seek(info.header_offset)
        header = f.read(30)
        _, _, _, _, _, _, _, _, _, fnamelen, extralen = struct.unpack(
            "<IHHHHHIIIHH", header
        )
    return info.header_offset + 30 + fnamelen + extralen


def zarr_zip_to_zmp(
    zip_path: str | Path,
    output_path: str | Path,
    *,
    uri: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Convert a Zarr ZIP store to a ZMP manifest.

    Creates virtual references pointing to byte ranges within the ZIP
    file. Metadata (zarr.json) is inlined as text; chunk data is
    referenced virtually.

    For ZIP_STORED entries, the bytes are referenced directly. For
    ZIP_DEFLATED entries, content_encoding="deflate" is set so the
    reader knows to decompress.

    Parameters
    ----------
    zip_path : path to the .zarr.zip file
    output_path : path for the output .zmp file
    uri : URI for the ZIP file in virtual references. If None, uses
        the local file path. Set to an HTTP URL to create a manifest
        for remote access.
    overwrite : if True, overwrite existing output

    Returns
    -------
    Path to the created .zmp file
    """
    from zarr_zmp import Builder

    zip_path = Path(zip_path)
    output_path = Path(output_path)

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists")

    zip_uri = uri or str(zip_path)

    builder = Builder()

    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue

            data_offset = _zip_data_offset(str(zip_path), info)
            stored_size = info.compress_size
            logical_size = info.file_size

            # Determine content encoding
            if info.compress_type == zipfile.ZIP_STORED:
                content_encoding = None
            elif info.compress_type == zipfile.ZIP_DEFLATED:
                content_encoding = "deflate"
            else:
                raise ValueError(
                    f"Unsupported ZIP compression type {info.compress_type} "
                    f"for entry {info.filename}"
                )

            path = info.filename

            # Inline metadata as text
            if path.endswith(".json"):
                raw = zf.read(info.filename)
                builder.add(path, text=raw.decode("utf-8"))
            else:
                # Virtual reference into the ZIP
                add_kwargs: dict[str, Any] = {
                    "resolve": {
                        "http": {
                            "url": zip_uri,
                            "offset": data_offset,
                            "length": stored_size,
                        }
                    },
                    "size": logical_size,
                }
                if content_encoding:
                    add_kwargs["content_encoding"] = content_encoding
                    add_kwargs["content_size"] = stored_size
                builder.add(path, **add_kwargs)

    if output_path.exists() and overwrite:
        output_path.unlink()

    builder.write(output_path)
    return output_path


def zarr_zip_to_zmp_hydrated(
    zip_path: str | Path,
    output_path: str | Path,
    *,
    compress: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Convert a Zarr ZIP store to a self-contained ZMP manifest.

    Extracts all data from the ZIP and stores it inline in the ZMP.
    Metadata is inlined as text; chunk data is inlined as binary.

    Parameters
    ----------
    zip_path : path to the .zarr.zip file
    output_path : path for the output .zmp file
    compress : optional compression for inline data ("deflate", "zstd").
        If None, data is stored uncompressed in the data column (parquet
        may still apply column-level compression).
    overwrite : if True, overwrite existing output

    Returns
    -------
    Path to the created .zmp file
    """
    from zarr_zmp import Builder

    zip_path = Path(zip_path)
    output_path = Path(output_path)

    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists")

    builder = Builder()

    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue

            path = info.filename
            raw = zf.read(info.filename)  # always decompressed by zipfile

            if path.endswith(".json"):
                builder.add(path, text=raw.decode("utf-8"))
            elif compress:
                builder.add(path, data=raw, compress=compress)
            else:
                builder.add(path, data=raw)

    if output_path.exists() and overwrite:
        output_path.unlink()

    builder.write(output_path)
    return output_path
