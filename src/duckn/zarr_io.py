"""Thin wrappers for reading/writing Zarr v3 arrays with duckn attributes."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from zipfile import ZIP_STORED

import numpy as np
import zarr

from .models import DucknMetadata


def _is_zip_path(path: str | Path) -> bool:
    """Return True if path ends with .zarr.zip."""
    return str(path).endswith(".zarr.zip")


@contextmanager
def open_store(path: str | Path, *, mode: str = "r", overwrite: bool = False):
    """Context manager that yields a Zarr store for *path*.

    For paths ending in ``.zarr.zip`` a ``ZipStore`` is used; otherwise a
    ``LocalStore``.  ZipStore requires explicit ``close()`` so the context
    manager handles that automatically.

    Parameters
    ----------
    path : store path (directory or ``.zarr.zip`` file)
    mode : "r" for reading, "w" for writing
    overwrite : if True **and** zip, delete the file before opening
        (ZipStore cannot delete entries inside an existing archive)
    """
    path = Path(path)
    if path.suffix == ".zmp":
        from zarr_zmp import ZMPStore
        yield ZMPStore.from_file(str(path))
    elif _is_zip_path(path):
        if mode == "w" and path.exists():
            if not overwrite:
                raise FileExistsError(f"{path} already exists (use --overwrite)")
            os.remove(path)
        store = zarr.storage.ZipStore(path, mode=mode, compression=ZIP_STORED)
        try:
            yield store
        finally:
            try:
                store.close()
            except AttributeError:
                pass  # ZipStore uninitialised (no data written)
    else:
        yield zarr.storage.LocalStore(str(path))


def read_duckn(source: str | Path | Any) -> tuple[np.ndarray, DucknMetadata]:
    """Read a duckn Zarr v3 array and return (data, metadata).

    Parameters
    ----------
    source : path to a Zarr store (directory or .zarr.zip), or any
        object implementing the Zarr Store interface (e.g. ZMPStore).

    Returns
    -------
    data : numpy array
    meta : parsed DucknMetadata from the "duckn" attribute
    """
    if isinstance(source, (str, Path)):
        with open_store(source, mode="r") as store:
            arr = zarr.open_array(store, mode="r")
            data = arr[:]
            duckn_attrs = arr.attrs.get("duckn", {})
            meta = DucknMetadata(**duckn_attrs)
        return data, meta
    else:
        # Assume it's a Zarr Store object (e.g. ZMPStore)
        arr = zarr.open_array(store=source, mode="r")
        data = arr[:]
        duckn_attrs = arr.attrs.get("duckn", {})
        meta = DucknMetadata(**duckn_attrs)
        return data, meta


def read_duckn_metadata(source: str | Path | Any) -> DucknMetadata:
    """Read only the duckn metadata from a Zarr store (no data loaded).

    Parameters
    ----------
    source : path to a Zarr store, or a Zarr Store object (e.g. ZMPStore).
    """
    if isinstance(source, (str, Path)):
        with open_store(source, mode="r") as store:
            arr = zarr.open_array(store, mode="r")
            duckn_attrs = arr.attrs.get("duckn", {})
            meta = DucknMetadata(**duckn_attrs)
        return meta
    else:
        arr = zarr.open_array(store=source, mode="r")
        duckn_attrs = arr.attrs.get("duckn", {})
        return DucknMetadata(**duckn_attrs)


def get_zarr_attrs(path: str | Path) -> dict[str, Any]:
    """Return the raw attributes dict from a Zarr store."""
    with open_store(path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        attrs = dict(arr.attrs)
    return attrs
