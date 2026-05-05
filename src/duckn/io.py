"""Unified read/write for duckn volumes.

Supports: zarr directory, zarr.zip, ZMP, NRRD, NIfTI, DICOM.
Format is detected from file extension or specified explicitly.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np

from .models import DucknMetadata
from .volume import Volume


def _detect_format(path: str | Path) -> str:
    """Detect format from file extension."""
    p = str(path)
    if p.endswith(".zarr.zip"):
        return "zarr.zip"
    if p.endswith(".zarr"):
        return "zarr"
    if p.endswith(".zmp"):
        return "zmp"
    if p.endswith(".nrrd") or p.endswith(".nhdr"):
        return "nrrd"
    if p.endswith(".nii") or p.endswith(".nii.gz"):
        return "nifti"
    if p.endswith(".dcm"):
        return "dicom"
    # Check if directory (could be zarr or DICOM)
    path_obj = Path(p)
    if path_obj.is_dir():
        if (path_obj / "zarr.json").exists() or (path_obj / ".zarray").exists():
            return "zarr"
        return "dicom"
    return "unknown"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read(source: str | Path | BinaryIO, *, format: str | None = None) -> Volume:
    """Read a volume from any supported format.

    Parameters
    ----------
    source : file path, or file-like object (requires format=)
    format : explicit format ("zarr", "zarr.zip", "zmp", "nrrd",
             "nifti", "dicom"). Auto-detected from extension if omitted.

    Returns
    -------
    Volume
    """
    if isinstance(source, (str, Path)):
        fmt = format or _detect_format(source)
    else:
        if format is None:
            raise ValueError("format= required when reading from file-like object")
        fmt = format

    if fmt in ("zarr", "zarr.zip"):
        return _read_zarr(source)
    elif fmt == "zmp":
        return _read_zmp(source)
    elif fmt == "nrrd":
        return _read_nrrd(source)
    elif fmt == "nifti":
        return _read_nifti(source)
    elif fmt == "dicom":
        return _read_dicom(source)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")


def _read_zarr(source: str | Path) -> Volume:
    import zarr

    p = Path(source)
    if str(p).endswith(".zarr.zip"):
        store = zarr.storage.ZipStore(str(p), mode="r")
    else:
        store = zarr.storage.LocalStore(str(p), read_only=True)

    arr = zarr.open_array(store, mode="r")
    meta = DucknMetadata(**arr.attrs["duckn"])
    return Volume(data=arr[:], metadata=meta)


def _read_zmp(source: str | Path) -> Volume:
    import json

    import zarr
    from zarr_zmp import Manifest, ZMPStore

    store = ZMPStore.from_file(str(source))

    # Check if root is an array or a group
    m = Manifest(str(source))
    root_entry = m.get_entry("/zarr.json")
    if root_entry and root_entry.text:
        root_meta = json.loads(root_entry.text)
        if root_meta.get("node_type") == "group":
            raise ValueError(
                f"{source} is a group ZMP (patient hierarchy). "
                f"Use read(path, format='zmp') with a specific array path, "
                f"or use ZMPStore + zarr.open_array(store, path='...') directly."
            )

    arr = zarr.open_array(store, mode="r")
    meta = DucknMetadata(**arr.attrs["duckn"])
    return Volume(data=arr[:], metadata=meta)


def _read_nrrd(source: str | Path) -> Volume:
    from .convert import _header_to_metadata

    import nrrd

    data, header = nrrd.read(str(source))
    meta, _ = _header_to_metadata(header, data.ndim)
    return Volume(data=data, metadata=meta)


def _read_nifti(source: str | Path) -> Volume:
    from .nibabel_adapter import from_nifti

    import nibabel as nib

    img = nib.load(str(source))
    return from_nifti(img)


def _read_dicom(source: str | Path) -> Volume:
    from .dicom_convert import dicom_to_zarr

    import tempfile

    # Convert DICOM to zarr in memory
    with tempfile.TemporaryDirectory() as tmpdir:
        zarr_path = Path(tmpdir) / "temp.zarr"
        dicom_to_zarr(str(source), str(zarr_path))
        return _read_zarr(zarr_path)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write(
    vol: Volume,
    dest: str | Path | BinaryIO,
    *,
    format: str | None = None,
    chunks: tuple[int, ...] | None = None,
    compressor: str = "zstd",
    level: int = 3,
    overwrite: bool = False,
) -> None:
    """Write a volume to any supported format.

    Parameters
    ----------
    vol : Volume to write
    dest : file path, or file-like object (requires format=)
    format : explicit format ("zarr", "zarr.zip", "zmp", "nrrd",
             "nifti", "dicom"). Auto-detected from extension if omitted.
    chunks : chunk shape for zarr formats. Auto-computed if omitted.
    compressor : compression codec for zarr formats ("zstd", "gzip", "none")
    level : compression level
    overwrite : overwrite existing files
    """
    if isinstance(dest, (str, Path)):
        fmt = format or _detect_format(dest)
    else:
        if format is None:
            raise ValueError("format= required when writing to file-like object")
        fmt = format

    if fmt == "zarr":
        _write_zarr(vol, dest, chunks=chunks, compressor=compressor,
                    level=level, overwrite=overwrite)
    elif fmt == "zarr.zip":
        _write_zarr_zip(vol, dest, chunks=chunks, compressor=compressor,
                        level=level, overwrite=overwrite)
    elif fmt == "zmp":
        _write_zmp(vol, dest, chunks=chunks, compressor=compressor,
                   level=level, overwrite=overwrite)
    elif fmt == "nrrd":
        _write_nrrd(vol, dest, overwrite=overwrite)
    elif fmt == "nifti":
        _write_nifti(vol, dest, overwrite=overwrite)
    elif fmt == "dicom":
        _write_dicom(vol, dest, overwrite=overwrite)
    else:
        raise ValueError(f"Unknown format: {fmt!r}")


def _auto_chunks(shape: tuple[int, ...], dtype: np.dtype) -> tuple[int, ...]:
    """Compute reasonable chunk shape targeting ~1MB chunks."""
    from .convert import _auto_chunks as _ac
    return _ac(shape, dtype)


def _write_zarr(vol, dest, *, chunks, compressor, level, overwrite):
    import zarr
    from .convert import _build_compressors
    from .zarr_io import open_store

    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"{dest} exists")

    chunks = chunks or _auto_chunks(vol.shape, vol.data.dtype)
    compressors = _build_compressors(compressor, level)
    attrs = {"duckn": vol.metadata.model_dump(exclude_none=True)}

    with open_store(dest, mode="w", overwrite=overwrite) as store:
        zarr.create_array(
            store, data=vol.data, chunks=chunks,
            compressors=compressors, attributes=attrs,
            fill_value=0, overwrite=overwrite,
        )


def _write_zarr_zip(vol, dest, *, chunks, compressor, level, overwrite):
    import zarr
    from .convert import _build_compressors

    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"{dest} exists")
    if dest.exists() and overwrite:
        dest.unlink()

    chunks = chunks or _auto_chunks(vol.shape, vol.data.dtype)
    compressors = _build_compressors(compressor, level)
    attrs = {"duckn": vol.metadata.model_dump(exclude_none=True)}

    store = zarr.storage.ZipStore(str(dest), mode="w")
    zarr.create_array(
        store, data=vol.data, chunks=chunks,
        compressors=compressors, attributes=attrs,
        fill_value=0,
    )
    store.close()


def _write_zmp(vol, dest, *, chunks, compressor, level, overwrite):
    import asyncio

    import zarr
    from zarr_zmp import ZMPWritableStore

    if isinstance(dest, (str, Path)):
        dest_path = Path(dest)
        if dest_path.exists() and not overwrite:
            raise FileExistsError(f"{dest_path} exists")
        if dest_path.exists() and overwrite:
            dest_path.unlink()
        output = str(dest_path)
    else:
        output = dest  # BytesIO

    chunks = chunks or _auto_chunks(vol.shape, vol.data.dtype)
    attrs = {"duckn": vol.metadata.model_dump(exclude_none=True)}

    store = ZMPWritableStore(output)
    arr = zarr.open_array(
        store, mode="w",
        shape=vol.shape, dtype=vol.data.dtype,
        chunks=chunks, attributes=attrs,
    )
    arr[:] = vol.data

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        new_loop = asyncio.new_event_loop()
        try:
            new_loop.run_until_complete(store.close())
        finally:
            new_loop.close()
    else:
        asyncio.run(store.close())


def _write_nrrd(vol, dest, *, overwrite):
    from .convert import _metadata_to_header

    import nrrd

    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"{dest} exists")

    header = _metadata_to_header(vol.metadata)
    nrrd.write(str(dest), vol.data, header)


def _write_nifti(vol, dest, *, overwrite):
    from .nibabel_adapter import to_nifti

    import nibabel as nib

    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"{dest} exists")

    img = to_nifti(vol, space="world")
    nib.save(img, str(dest))


def _write_dicom(vol, dest, *, overwrite):
    from .dicom_convert import zarr_to_dicom

    import tempfile

    dest = Path(dest)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"{dest} exists")

    # Write to zarr first, then convert
    with tempfile.TemporaryDirectory() as tmpdir:
        zarr_path = Path(tmpdir) / "temp.zarr"
        _write_zarr(vol, zarr_path, chunks=None, compressor="zstd",
                    level=3, overwrite=True)
        zarr_to_dicom(str(zarr_path), str(dest), overwrite=overwrite)
