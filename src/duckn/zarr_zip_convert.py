"""Convert Zarr v2/v3 zip stores to duckn ZMP manifests.

Reads zip central directories (local or remote via remotezip) to build
virtual or hydrated ZMP manifests pointing at chunk byte ranges within
the source zip file. No data is decompressed or converted — chunks are
referenced or copied as-is.

Supports:
- Local zip files (file:// URIs)
- Remote zip files (HTTP/HTTPS with range request support)
- Zarr v2 (.zarray/.zattrs) and v3 (zarr.json) metadata
- Virtual (byte-range references) or hydrated (inline data) output
- Optional duckn metadata injection

Requires remotezip for remote access: ``pip install remotezip``
"""

from __future__ import annotations

import json
import struct
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Zip entry scanning
# ---------------------------------------------------------------------------


def _scan_local_zip(zip_path: Path) -> list[dict[str, Any]]:
    """Scan a local zip file's central directory.

    Returns a list of dicts with keys: name, data_offset, comp_size,
    uncomp_size, compress_type.
    """
    entries = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            # Compute data offset from local file header
            with open(zip_path, "rb") as f:
                f.seek(info.header_offset + 26)
                fname_len, extra_len = struct.unpack("<HH", f.read(4))
            data_offset = info.header_offset + 30 + fname_len + extra_len
            entries.append({
                "name": info.filename,
                "data_offset": data_offset,
                "comp_size": info.compress_size,
                "uncomp_size": info.file_size,
                "compress_type": info.compress_type,
            })
    return entries


def _scan_remote_zip(url: str) -> list[dict[str, Any]]:
    """Scan a remote zip file's central directory via range requests.

    Uses remotezip to read the central directory without downloading
    the entire file. Computes data offsets by fetching local file
    headers in parallel.
    """
    import asyncio
    import httpx
    from remotezip import RemoteZip

    entries = []
    with RemoteZip(url) as rz:
        for info in rz.infolist():
            if info.file_size == 0 or info.filename.endswith("/"):
                continue
            entries.append({
                "name": info.filename,
                "header_offset": info.header_offset,
                "comp_size": info.compress_size,
                "uncomp_size": info.file_size,
                "compress_type": info.compress_type,
            })

    # Fetch local file headers in parallel to get exact data offsets
    async def _fetch_offsets():
        sem = asyncio.Semaphore(50)

        async def _fetch_one(e: dict) -> None:
            async with sem:
                resp = await client.get(url, headers={
                    "Range": f"bytes={e['header_offset']}-{e['header_offset'] + 30 + 300}"
                })
                if resp.status_code in (200, 206):
                    hdr = resp.content
                    fname_len = struct.unpack_from("<H", hdr, 26)[0]
                    extra_len = struct.unpack_from("<H", hdr, 28)[0]
                    e["data_offset"] = e["header_offset"] + 30 + fname_len + extra_len
                else:
                    e["data_offset"] = None

        async with httpx.AsyncClient(
            timeout=30, follow_redirects=True,
            limits=httpx.Limits(max_connections=50),
        ) as client:
            await asyncio.gather(*[_fetch_one(e) for e in entries])

    asyncio.run(_fetch_offsets())

    return [e for e in entries if e.get("data_offset") is not None]


def _scan_zip(source: str | Path) -> tuple[list[dict[str, Any]], str, int]:
    """Scan a zip file (local or remote).

    Returns (entries, uri, file_size).
    """
    source_str = str(source)

    if source_str.startswith(("http://", "https://")):
        entries = _scan_remote_zip(source_str)
        import httpx
        client = httpx.Client(timeout=10, follow_redirects=True)
        resp = client.head(source_str)
        file_size = int(resp.headers.get("content-length", 0))
        client.close()
        return entries, source_str, file_size
    else:
        path = Path(source_str)
        entries = _scan_local_zip(path)
        uri = path.resolve().as_uri()
        return entries, uri, path.stat().st_size


# ---------------------------------------------------------------------------
# Zarr metadata detection and conversion
# ---------------------------------------------------------------------------


_ZARR_V2_DTYPE_MAP: dict[str, str] = {
    "<i1": "int8", ">i1": "int8", "|i1": "int8",
    "<u1": "uint8", ">u1": "uint8", "|u1": "uint8",
    "<i2": "int16", ">i2": "int16",
    "<u2": "uint16", ">u2": "uint16",
    "<i4": "int32", ">i4": "int32",
    "<u4": "uint32", ">u4": "uint32",
    "<i8": "int64", ">i8": "int64",
    "<u8": "uint64", ">u8": "uint64",
    "<f4": "float32", ">f4": "float32",
    "<f8": "float64", ">f8": "float64",
}


def _v2_compressor_to_v3_codec(compressor: dict | None) -> list[dict]:
    """Convert a Zarr v2 compressor spec to v3 codec pipeline."""
    codecs: list[dict] = [
        {"name": "bytes", "configuration": {"endian": "little"}},
    ]
    if compressor is None:
        return codecs

    cid = compressor.get("id", "")
    if cid == "blosc":
        codecs.append({
            "name": "blosc",
            "configuration": {
                "cname": compressor.get("cname", "lz4"),
                "clevel": compressor.get("clevel", 5),
                "shuffle": "shuffle" if compressor.get("shuffle", 1) else "noshuffle",
                "typesize": compressor.get("typesize", 0) or 2,
                "blocksize": compressor.get("blocksize", 0),
            },
        })
    elif cid == "zstd":
        codecs.append({
            "name": "zstd",
            "configuration": {"level": compressor.get("level", 3)},
        })
    elif cid == "gzip":
        codecs.append({
            "name": "gzip",
            "configuration": {"level": compressor.get("level", 5)},
        })
    elif cid == "lz4":
        codecs.append({
            "name": "blosc",
            "configuration": {
                "cname": "lz4",
                "clevel": compressor.get("acceleration", 1),
                "shuffle": "noshuffle",
                "typesize": 1,
                "blocksize": 0,
            },
        })
    else:
        # Unknown compressor — try passthrough
        codecs.append({"name": cid, "configuration": compressor})

    return codecs


def _read_zip_entry(source: str | Path, entry: dict) -> bytes:
    """Read the raw bytes of a zip entry."""
    source_str = str(source)
    if source_str.startswith(("http://", "https://")):
        import httpx
        client = httpx.Client(timeout=30, follow_redirects=True)
        resp = client.get(source_str, headers={
            "Range": f"bytes={entry['data_offset']}-{entry['data_offset'] + entry['comp_size'] - 1}"
        })
        client.close()
        return resp.content
    else:
        with open(source_str, "rb") as f:
            f.seek(entry["data_offset"])
            return f.read(entry["comp_size"])


def _parse_zarr_metadata(
    entries: list[dict],
    source: str | Path,
    prefix: str = "",
) -> tuple[int, dict, dict | None]:
    """Detect Zarr version and parse metadata from zip entries.

    Returns (zarr_version, zarr_json_dict, zattrs_dict).
    """
    entry_map = {e["name"]: e for e in entries}

    # Check for v3 zarr.json
    v3_key = f"{prefix}zarr.json" if prefix else "zarr.json"
    if v3_key in entry_map:
        data = _read_zip_entry(source, entry_map[v3_key])
        meta = json.loads(data)
        return 3, meta, None

    # Check for v2 .zarray
    v2_key = f"{prefix}.zarray" if prefix else ".zarray"
    if v2_key in entry_map:
        data = _read_zip_entry(source, entry_map[v2_key])
        zarray = json.loads(data)

        # Read .zattrs if present
        zattrs_key = f"{prefix}.zattrs" if prefix else ".zattrs"
        zattrs = None
        if zattrs_key in entry_map:
            zattrs_data = _read_zip_entry(source, entry_map[zattrs_key])
            zattrs = json.loads(zattrs_data)

        # Convert v2 to v3 zarr.json
        dtype_str = _ZARR_V2_DTYPE_MAP.get(zarray["dtype"], "int16")
        endian = "big" if zarray["dtype"].startswith(">") else "little"
        codecs = _v2_compressor_to_v3_codec(zarray.get("compressor"))
        # Fix endian in bytes codec
        for c in codecs:
            if c["name"] == "bytes":
                c["configuration"]["endian"] = endian

        # Determine chunk key separator
        separator = zarray.get("dimension_separator", ".")

        zarr_json = {
            "zarr_format": 3,
            "node_type": "array",
            "shape": zarray["shape"],
            "data_type": dtype_str,
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": zarray["chunks"]},
            },
            "chunk_key_encoding": {
                "name": "default",
                "configuration": {"separator": separator},
            },
            "fill_value": zarray.get("fill_value", 0),
            "codecs": codecs,
        }

        if zattrs:
            zarr_json["attributes"] = zattrs

        return 2, zarr_json, zattrs

    raise ValueError(f"No zarr.json or .zarray found with prefix '{prefix}'")


# ---------------------------------------------------------------------------
# duckn metadata from OME-NGFF or existing attributes
# ---------------------------------------------------------------------------


def _inject_duckn_metadata(
    zarr_json: dict,
    zattrs: dict | None,
) -> dict:
    """Add duckn metadata to zarr_json from OME-NGFF multiscales or existing attrs.

    If duckn metadata is already present, returns zarr_json unchanged.
    If OME-NGFF multiscales are present, extracts spatial calibration.
    Otherwise adds minimal duckn metadata.
    """
    attrs = zarr_json.get("attributes", {})

    # Already has duckn
    if "duckn" in attrs:
        return zarr_json

    shape = zarr_json["shape"]
    ndim = len(shape)

    # Try OME-NGFF multiscales
    multiscales = attrs.get("multiscales", [])
    if multiscales:
        ms = multiscales[0]
        ome_axes = ms.get("axes", [])
        datasets = ms.get("datasets", [])

        # Find scale for this array's path
        scale = None
        for ds in datasets:
            ct = ds.get("coordinateTransformations", [])
            for t in ct:
                if t.get("type") == "scale":
                    scale = t["scale"]
                    break

        if scale and ome_axes:
            axes = []
            dim_names = []
            for i, ax in enumerate(ome_axes):
                ax_meta: dict[str, Any] = {}
                name = ax.get("name", f"d{i}")
                dim_names.append(name)

                if ax.get("type") == "space":
                    ax_meta["kind"] = "space"
                    ax_meta["centering"] = "cell"
                    direction = [0.0] * len(ome_axes)
                    direction[i] = scale[i] if i < len(scale) else 1.0
                    # Only include spatial dimensions in direction
                    spatial_dims = sum(1 for a in ome_axes if a.get("type") == "space")
                    spatial_dir = [0.0] * spatial_dims
                    spatial_idx = sum(1 for a in ome_axes[:i] if a.get("type") == "space")
                    if spatial_idx < spatial_dims:
                        spatial_dir[spatial_idx] = scale[i] if i < len(scale) else 1.0
                    ax_meta["space_direction"] = spatial_dir

                    unit = ax.get("unit")
                    if unit:
                        # Map OME unit names to structured units
                        unit_map = {
                            "micrometer": {"symbol": "µm", "scheme": "UCUM", "code": "um"},
                            "millimeter": "mm",
                            "nanometer": {"symbol": "nm", "scheme": "UCUM", "code": "nm"},
                        }
                        ax_meta["unit"] = unit_map.get(unit, unit)
                elif ax.get("type") == "time":
                    ax_meta["kind"] = "time"
                    unit = ax.get("unit")
                    if unit:
                        ax_meta["unit"] = unit
                elif ax.get("type") == "channel":
                    ax_meta["kind"] = "list"

                axes.append(ax_meta)

            spatial_dims = sum(1 for a in ome_axes if a.get("type") == "space")
            duckn = {
                "version": "1.0",
                "space_dimension": spatial_dims,
                "space_origin": [0.0] * spatial_dims,
                "axes": axes,
            }
            attrs["duckn"] = duckn
            zarr_json["attributes"] = attrs
            zarr_json["dimension_names"] = dim_names
            return zarr_json

    # Fallback: minimal duckn with no spatial info
    duckn: dict[str, Any] = {"version": "1.0"}
    if ndim >= 2:
        duckn["space_dimension"] = min(ndim, 3)
        duckn["space_origin"] = [0.0] * min(ndim, 3)
    attrs["duckn"] = duckn
    zarr_json["attributes"] = attrs
    return zarr_json


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def zarr_zip_to_zmp(
    source: str | Path,
    output_path: str | Path,
    *,
    prefix: str = "",
    hydrate: bool = False,
    duckn: bool = True,
    overwrite: bool = False,
) -> Path:
    """Convert a Zarr v2/v3 zip store to a duckn ZMP manifest.

    Scans the zip central directory (locally or via HTTP range requests)
    and builds a ZMP with virtual byte-range references or hydrated
    inline data.

    Parameters
    ----------
    source : path or URL to the .zarr.zip file
    output_path : path for the output .zmp file
    prefix : path prefix inside the zip (e.g. "data.zarr/" if entries
        are nested under a directory)
    hydrate : if True, embed chunk data inline in the ZMP.
        If False (default), chunks are virtual byte-range references.
    duckn : if True (default), inject duckn metadata from OME-NGFF
        multiscales or add minimal duckn metadata.
    overwrite : if True, overwrite existing output file

    Returns
    -------
    Path to the created .zmp file
    """
    from zarr_zmp import Builder as ZMPBuilder

    output_path = Path(output_path)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists")

    # Scan zip
    entries, uri, file_size = _scan_zip(source)
    entry_map = {e["name"]: e for e in entries}

    # Auto-detect prefix if not provided
    if not prefix:
        # Look for zarr.json or .zarray at various prefixes
        for e in entries:
            name = e["name"]
            if name.endswith("zarr.json") or name.endswith(".zarray"):
                prefix = name.rsplit("zarr.json", 1)[0].rsplit(".zarray", 1)[0]
                break

    # Detect group structure (multi-array / multi-resolution)
    # Find all arrays: paths that have zarr.json or .zarray
    array_prefixes = set()
    for e in entries:
        name = e["name"]
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        if rel == "zarr.json" or rel == ".zarray":
            array_prefixes.add("")
        elif rel.endswith("/zarr.json") or rel.endswith("/.zarray"):
            array_prefix = rel.rsplit("zarr.json", 1)[0].rsplit(".zarray", 1)[0]
            array_prefixes.add(array_prefix)

    is_group = len(array_prefixes) > 1 or (len(array_prefixes) == 1 and "" not in array_prefixes)

    builder = ZMPBuilder()

    if is_group:
        # Multi-array store: read group metadata, then each array
        group_meta = {"zarr_format": 3, "node_type": "group", "attributes": {}}

        # Check for group-level .zattrs (OME-NGFF multiscales)
        zattrs_key = f"{prefix}.zattrs"
        if zattrs_key in entry_map:
            zattrs_data = _read_zip_entry(source, entry_map[zattrs_key])
            group_attrs = json.loads(zattrs_data)
            group_meta["attributes"] = group_attrs

        builder.add("zarr.json", text=json.dumps(group_meta))

        for array_prefix in sorted(array_prefixes):
            full_prefix = prefix + array_prefix
            zarr_version, zarr_json, zattrs = _parse_zarr_metadata(
                entries, source, full_prefix,
            )

            # Inject OME-NGFF scale from group attrs if available
            if "multiscales" in group_meta.get("attributes", {}):
                ms = group_meta["attributes"]["multiscales"][0]
                for ds in ms.get("datasets", []):
                    if ds.get("path") == array_prefix.rstrip("/"):
                        ct = ds.get("coordinateTransformations", [])
                        for t in ct:
                            if t.get("type") == "scale":
                                # Inject into array-level attrs for duckn
                                if "attributes" not in zarr_json:
                                    zarr_json["attributes"] = {}
                                if "multiscales" not in zarr_json["attributes"]:
                                    zarr_json["attributes"]["multiscales"] = [{
                                        "axes": ms.get("axes", []),
                                        "datasets": [ds],
                                    }]

            if duckn:
                zarr_json = _inject_duckn_metadata(zarr_json, zattrs)

            zmp_array_prefix = array_prefix.rstrip("/") + "/" if array_prefix else ""
            builder.add(f"{zmp_array_prefix}zarr.json", text=json.dumps(zarr_json))

            # Add chunk entries
            _add_chunk_entries(
                builder, entries, source, uri, file_size,
                zip_prefix=full_prefix,
                zmp_prefix=zmp_array_prefix,
                zarr_version=zarr_version,
                zarr_json=zarr_json,
                hydrate=hydrate,
            )
    else:
        # Single array
        zarr_version, zarr_json, zattrs = _parse_zarr_metadata(
            entries, source, prefix,
        )

        if duckn:
            zarr_json = _inject_duckn_metadata(zarr_json, zattrs)

        builder.add("zarr.json", text=json.dumps(zarr_json))

        _add_chunk_entries(
            builder, entries, source, uri, file_size,
            zip_prefix=prefix,
            zmp_prefix="",
            zarr_version=zarr_version,
            zarr_json=zarr_json,
            hydrate=hydrate,
        )

    if output_path.exists() and overwrite:
        output_path.unlink()

    builder.write(output_path)
    return output_path


def _add_chunk_entries(
    builder: Any,
    entries: list[dict],
    source: str | Path,
    uri: str,
    file_size: int,
    *,
    zip_prefix: str,
    zmp_prefix: str,
    zarr_version: int,
    zarr_json: dict,
    hydrate: bool,
) -> None:
    """Add chunk entries to the builder."""
    # Determine chunk key separator
    separator = "/"
    if zarr_version == 2:
        cke = zarr_json.get("chunk_key_encoding", {})
        separator = cke.get("configuration", {}).get("separator", "/")

    # Skip metadata files
    skip_suffixes = ("zarr.json", ".zarray", ".zattrs", ".zgroup", ".zmetadata")

    for e in entries:
        name = e["name"]
        if not name.startswith(zip_prefix):
            continue
        if name.endswith("/") or any(name.endswith(s) for s in skip_suffixes):
            continue
        if e["compress_type"] != 0:
            continue  # only ZIP_STORED entries can be virtual

        rel = name[len(zip_prefix):]

        # Convert v2 chunk key to v3 format
        if zarr_version == 2:
            # v2 keys: "0.0.0" or "0/0/0" depending on dimension_separator
            # v3 keys: "c/0/0/0"
            chunk_key = "c/" + rel.replace(".", "/")
        else:
            # v3 keys already have "c/" prefix
            chunk_key = rel

        zmp_path = f"{zmp_prefix}{chunk_key}"

        if hydrate:
            chunk_data = _read_zip_entry(source, e)
            builder.add(zmp_path, data=chunk_data)
        else:
            builder.add(
                zmp_path,
                resolve={"http": {
                    "url": uri,
                    "offset": e["data_offset"],
                    "length": e["comp_size"],
                }},
                size=file_size,
            )
