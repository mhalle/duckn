"""Thin wrappers for reading/writing Zarr v3 arrays with nrrd attributes."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from zipfile import ZIP_STORED

import numpy as np
import zarr

from .models import NrrdMetadata


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
    if _is_zip_path(path):
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


def read_duckn(path: str | Path) -> tuple[np.ndarray, NrrdMetadata]:
    """Read a duckn Zarr v3 array and return (data, metadata).

    Returns
    -------
    data : numpy array
    meta : parsed NrrdMetadata from the "nrrd" attribute
    """
    with open_store(path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        data = arr[:]
        nrrd_attrs = arr.attrs.get("nrrd", {})
        meta = NrrdMetadata(**nrrd_attrs)
    return data, meta


def read_duckn_metadata(path: str | Path) -> NrrdMetadata:
    """Read only the nrrd metadata from a Zarr store (no data loaded)."""
    with open_store(path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        nrrd_attrs = arr.attrs.get("nrrd", {})
        meta = NrrdMetadata(**nrrd_attrs)
    return meta


def get_zarr_attrs(path: str | Path) -> dict[str, Any]:
    """Return the raw attributes dict from a Zarr store."""
    with open_store(path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        attrs = dict(arr.attrs)
    return attrs
