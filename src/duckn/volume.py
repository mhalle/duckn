"""Thin wrapper pairing array data with duckn metadata.

Provides a single object to pass around instead of ``(data, meta)`` tuples.
Lazily computes spatial geometry on first access.

The volume holds **raw** stored values in :attr:`raw`. :attr:`data` is a
cached, calibrated view: linear ``value_transforms`` declared in the
metadata are applied lazily on first access. When there are no
transforms (or they are identity), ``vol.data`` returns ``vol.raw``
unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

import numpy as np

from .extensions import Extensions
from .models import DucknMetadata
from .spatial import VolumeGeometry


@dataclass
class Volume:
    """A duckn volume: raw array data paired with spatial metadata.

    Access patterns
    ---------------
    ``vol.raw`` : numpy array of stored values (whatever was passed at
        construction). Use this for writes that should preserve the
        source representation and for raw-data round-trip.
    ``vol.data`` : calibrated view — applies the metadata's
        ``value_transforms`` (slope/intercept) lazily on first access
        and caches the result. When no transforms are declared, this
        is the same array as ``vol.raw``.
    ``vol.dtype`` : the dtype users will see via ``vol.data``
        (float32 if a non-identity transform applies, else
        ``vol.raw.dtype``).
    """

    raw: np.ndarray
    metadata: DucknMetadata
    # Pre-composed slope/intercept, populated in __post_init__.
    _slope: float = field(init=False, repr=False, default=1.0)
    _intercept: float = field(init=False, repr=False, default=0.0)

    def __post_init__(self) -> None:
        from .zarr_io import _compose_linear_transforms

        self._slope, self._intercept = _compose_linear_transforms(
            self.metadata.value_transforms
        )

    @cached_property
    def data(self) -> np.ndarray:
        """Calibrated view of ``raw`` (transforms applied, cached)."""
        from .zarr_io import _rescale

        if not self.metadata.value_transforms:
            return self.raw
        return _rescale(self.raw, self._slope, self._intercept, None)

    def add_transform(
        self,
        to_space: str,
        *,
        affine: "np.ndarray | list[list[float]] | None" = None,
        inverse: "np.ndarray | list[list[float]] | None" = None,
        identity: bool = False,
        metadata: dict | None = None,
    ) -> None:
        """Add a space transform from world to a named space.

        Delegates to :meth:`DucknMetadata.add_transform` and invalidates
        the cached geometry so it picks up the new transform.
        """
        self.metadata.add_transform(
            to_space,
            affine=affine,
            inverse=inverse,
            identity=identity,
            metadata=metadata,
        )
        # Invalidate cached geometry so it picks up the new transform
        if "geometry" in self.__dict__:
            del self.__dict__["geometry"]

    @property
    def extensions(self) -> Extensions:
        """Typed access to extensions."""
        return Extensions(self.metadata.extensions)

    def get_extension(self, name: str) -> Any | None:
        """Get a top-level extension by name, or None if not present."""
        return self.metadata.get_extension(name)

    def set_extension(self, name: str, value: Any) -> None:
        """Set a top-level extension. Overwrites if already present."""
        self.metadata.set_extension(name, value)

    @cached_property
    def geometry(self) -> VolumeGeometry:
        """Spatial geometry, computed lazily from metadata + shape."""
        return VolumeGeometry.from_metadata(self.metadata, self.raw.shape)

    @property
    def shape(self) -> tuple[int, ...]:
        return self.raw.shape

    @property
    def dtype(self) -> np.dtype:
        """Effective dtype users see via ``vol.data``."""
        if (
            not self.metadata.value_transforms
            or (self._slope == 1.0 and self._intercept == 0.0)
        ):
            return self.raw.dtype
        return np.dtype(np.float32)

    @property
    def ndim(self) -> int:
        return self.raw.ndim
