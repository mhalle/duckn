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


# Alias under the canonical short name. ``read_metadata`` is the
# language-neutral pair to ``read_array``; ``read_duckn_metadata`` is
# the original long-form spelling kept for back-compat.
read_metadata = read_duckn_metadata


def read_array(
    source: str | Path | Any,
    *,
    apply_value_transforms: bool = True,
) -> np.ndarray:
    """Read a duckn Zarr array and return the data as a numpy array.

    By default, linear value transforms from the duckn metadata
    (``value_transforms``) are applied to the stored values, returning
    physical-unit data (e.g., HU for CT) as float32. Pass
    ``apply_value_transforms=False`` to receive the raw stored values
    unchanged.

    Parameters
    ----------
    source : path to a Zarr store (directory, ``.zarr.zip``, or ``.zmp``)
        or any object implementing the Zarr Store interface.
    apply_value_transforms : if True (default), apply linear
        transforms (slope/intercept) declared in the duckn metadata.
        Non-linear transforms (if any) are skipped with a warning.

    Returns
    -------
    numpy array. dtype is the stored dtype when transforms are not
    applied (or none exist), otherwise float32.
    """
    if isinstance(source, (str, Path)):
        with open_store(source, mode="r") as store:
            arr = zarr.open_array(store, mode="r")
            data = arr[:]
            duckn_attrs = arr.attrs.get("duckn", {})
    else:
        arr = zarr.open_array(store=source, mode="r")
        data = arr[:]
        duckn_attrs = arr.attrs.get("duckn", {})

    if not apply_value_transforms or not duckn_attrs.get("value_transforms"):
        return data

    return _apply_value_transforms(data, duckn_attrs["value_transforms"])


def _apply_value_transforms(
    data: np.ndarray, transforms: list[dict[str, Any]]
) -> np.ndarray:
    """Apply duckn ``value_transforms`` (in order) to a numpy array.

    Linear transforms are folded into a single composed slope/intercept
    so the data is rescaled exactly once. Unknown transform names are
    skipped with a warning rather than failing.
    """
    import warnings

    composed_slope = 1.0
    composed_intercept = 0.0
    for vt in transforms:
        name = vt.get("name")
        params = vt.get("parameters") or {}
        if name == "linear":
            slope = float(params.get("slope", 1.0))
            intercept = float(params.get("intercept", 0.0))
            # Compose: y = slope * (composed_slope * x + composed_intercept) + intercept
            composed_slope = slope * composed_slope
            composed_intercept = slope * composed_intercept + intercept
        else:
            warnings.warn(
                f"Skipping unsupported value_transform name={name!r}",
                stacklevel=3,
            )

    if composed_slope == 1.0 and composed_intercept == 0.0:
        return data

    out = data.astype(np.float32, copy=False) * np.float32(composed_slope)
    if composed_intercept != 0.0:
        out = out + np.float32(composed_intercept)
    return out


def get_zarr_attrs(path: str | Path) -> dict[str, Any]:
    """Return the raw attributes dict from a Zarr store."""
    with open_store(path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        attrs = dict(arr.attrs)
    return attrs
