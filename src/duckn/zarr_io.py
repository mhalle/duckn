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


# Short-form alias of read_duckn_metadata. Pairs with open_array.
read_metadata = read_duckn_metadata


def _compose_linear_transforms(
    transforms: list[Any] | None,
) -> tuple[float, float]:
    """Compose a sequence of linear ValueTransforms into one (slope, intercept).

    Unknown transform names are skipped with a warning. Returns (1.0, 0.0)
    when there are no applicable transforms — i.e., identity.
    """
    import warnings

    composed_slope = 1.0
    composed_intercept = 0.0
    if not transforms:
        return composed_slope, composed_intercept

    for vt in transforms:
        # Accept either ValueTransform models or raw dicts
        if hasattr(vt, "name"):
            name = vt.name
            params = vt.parameters or {}
        else:
            name = vt.get("name")
            params = vt.get("parameters") or {}

        if name == "linear":
            slope = float(params.get("slope", 1.0))
            intercept = float(params.get("intercept", 0.0))
            # Compose: y = slope * (cs*x + ci) + intercept
            composed_slope = slope * composed_slope
            composed_intercept = slope * composed_intercept + intercept
        else:
            warnings.warn(
                f"Skipping unsupported value_transform name={name!r}",
                stacklevel=3,
            )

    return composed_slope, composed_intercept


class DucknArray:
    """Wrapper around a ``zarr.Array`` that applies value transforms on read.

    Slicing returns numpy arrays with linear value transforms (slope and
    intercept) applied when ``apply_value_transforms`` is True (the
    default). Toggle the flag at any time to switch between raw and
    calibrated reads on the same handle.

    Forwards ``shape``, ``chunks``, ``ndim``, ``size``, and ``attrs`` to
    the underlying ``zarr.Array``. ``dtype`` is dynamic: float32 when a
    non-identity transform is being applied, otherwise the stored dtype.
    Use ``.metadata`` for the parsed duckn metadata snapshot and
    ``.zarr`` for the underlying ``zarr.Array`` (whose own ``.metadata``
    gives zarr-level array info: shape/codecs/chunk grid).

    Supports the context-manager protocol so the underlying store is
    closed on exit (relevant for ZipStore and ZMPStore).
    """

    def __init__(
        self,
        zarr_array: Any,
        metadata: DucknMetadata | None = None,
        *,
        apply_value_transforms: bool = True,
        _store_to_close: Any = None,
    ) -> None:
        self._arr = zarr_array
        if metadata is None:
            metadata = DucknMetadata(**zarr_array.attrs.get("duckn", {}))
        self._metadata = metadata
        self.apply_value_transforms = apply_value_transforms
        self._slope, self._intercept = _compose_linear_transforms(
            metadata.value_transforms
        )
        self._store_to_close = _store_to_close

    @property
    def metadata(self) -> DucknMetadata:
        """Parsed duckn metadata, cached at construction.

        For zarr-level array metadata (shape/codecs/chunk grid), use
        ``arr.zarr.metadata``.
        """
        return self._metadata

    @property
    def attrs(self):
        """Underlying ``zarr.Array.attrs`` (raw dict, mutable)."""
        return self._arr.attrs

    @property
    def zarr(self):
        """Underlying ``zarr.Array`` (lazy, no transform application).

        Use this for raw byte access or zarr-native operations like
        ``arr.zarr.metadata`` (zarr-level array info: shape, codecs,
        chunk grid, etc.).
        """
        return self._arr

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(self._arr.shape)

    @property
    def chunks(self) -> tuple[int, ...]:
        return tuple(self._arr.chunks)

    @property
    def ndim(self) -> int:
        return int(self._arr.ndim)

    @property
    def size(self) -> int:
        return int(self._arr.size)

    @property
    def dtype(self) -> np.dtype:
        if self.apply_value_transforms and not self._is_identity:
            return np.dtype(np.float32)
        return np.dtype(self._arr.dtype)

    @property
    def _is_identity(self) -> bool:
        return self._slope == 1.0 and self._intercept == 0.0

    def __getitem__(self, key):
        data = self._arr[key]
        if not self.apply_value_transforms or self._is_identity:
            return data
        out = data.astype(np.float32, copy=False) * np.float32(self._slope)
        if self._intercept != 0.0:
            out = out + np.float32(self._intercept)
        return out

    def __array__(self, dtype=None, copy=None):
        out = self[...]
        if dtype is not None and np.dtype(dtype) != out.dtype:
            return out.astype(dtype, copy=True if copy else False)
        return out

    def __len__(self) -> int:
        return self.shape[0] if self.shape else 0

    def __repr__(self) -> str:
        mode = "transformed" if self.apply_value_transforms and not self._is_identity else "raw"
        return (
            f"DucknArray(shape={self.shape}, dtype={self.dtype}, "
            f"chunks={self.chunks}, mode={mode!r})"
        )

    def close(self) -> None:
        """Close the underlying store if this handle owns it."""
        if self._store_to_close is not None:
            try:
                self._store_to_close.close()
            except AttributeError:
                pass
            self._store_to_close = None

    def __enter__(self) -> DucknArray:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def open_array(
    source: str | Path | Any,
    *,
    apply_value_transforms: bool = True,
) -> DucknArray:
    """Open a duckn Zarr store and return a ``DucknArray`` handle.

    By default, slicing the returned handle yields numpy arrays with
    linear value transforms applied (float32 output). Set
    ``apply_value_transforms=False`` (or toggle the attribute on the
    returned object) to get raw stored values instead.

    For ``.zarr.zip`` and ``.zmp`` paths the returned handle owns the
    store; close it with ``handle.close()`` or use it as a context
    manager. ``LocalStore`` (directory) inputs need no cleanup.

    Parameters
    ----------
    source : path to a Zarr store (directory, ``.zarr.zip``, or ``.zmp``)
        or any object implementing the Zarr Store interface.
    apply_value_transforms : if True (default), apply linear value
        transforms on read.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        store: Any
        store_to_close: Any = None
        if path.suffix == ".zmp":
            from zarr_zmp import ZMPStore
            store = ZMPStore.from_file(str(path))
            store_to_close = store
        elif _is_zip_path(path):
            store = zarr.storage.ZipStore(path, mode="r", compression=ZIP_STORED)
            store_to_close = store
        else:
            store = zarr.storage.LocalStore(str(path))
        zarr_arr = zarr.open_array(store=store, mode="r")
        return DucknArray(
            zarr_arr,
            apply_value_transforms=apply_value_transforms,
            _store_to_close=store_to_close,
        )
    else:
        # Assume Store object (or already-opened zarr.Array)
        if hasattr(source, "shape"):
            zarr_arr = source
        else:
            zarr_arr = zarr.open_array(store=source, mode="r")
        return DucknArray(
            zarr_arr,
            apply_value_transforms=apply_value_transforms,
        )


def get_zarr_attrs(path: str | Path) -> dict[str, Any]:
    """Return the raw attributes dict from a Zarr store."""
    with open_store(path, mode="r") as store:
        arr = zarr.open_array(store, mode="r")
        attrs = dict(arr.attrs)
    return attrs
