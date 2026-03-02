"""Thin wrappers for reading/writing Zarr v3 arrays with nrrd attributes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import zarr

from .models import NrrdMetadata


def read_nrrdz(path: str | Path) -> tuple[np.ndarray, NrrdMetadata]:
    """Read a nrrdz Zarr v3 array and return (data, metadata).

    Returns
    -------
    data : numpy array
    meta : parsed NrrdMetadata from the "nrrd" attribute
    """
    store = zarr.storage.LocalStore(str(path))
    arr = zarr.open_array(store, mode="r")
    data = arr[:]
    nrrd_attrs = arr.attrs.get("nrrd", {})
    meta = NrrdMetadata(**nrrd_attrs)
    return data, meta


def read_nrrdz_metadata(path: str | Path) -> NrrdMetadata:
    """Read only the nrrd metadata from a Zarr store (no data loaded)."""
    store = zarr.storage.LocalStore(str(path))
    arr = zarr.open_array(store, mode="r")
    nrrd_attrs = arr.attrs.get("nrrd", {})
    return NrrdMetadata(**nrrd_attrs)


def get_zarr_attrs(path: str | Path) -> dict[str, Any]:
    """Return the raw attributes dict from a Zarr store."""
    store = zarr.storage.LocalStore(str(path))
    arr = zarr.open_array(store, mode="r")
    return dict(arr.attrs)
