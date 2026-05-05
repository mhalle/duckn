"""Thin wrapper pairing array data with duckn metadata.

Provides a single object to pass around instead of ``(data, meta)`` tuples.
Lazily computes spatial geometry on first access.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np

from .extensions import Extensions
from .models import DucknMetadata
from .spatial import VolumeGeometry


@dataclass
class Volume:
    """A duckn volume: array data paired with spatial metadata."""

    data: np.ndarray
    metadata: DucknMetadata

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
        return VolumeGeometry.from_metadata(self.metadata, self.data.shape)

    @property
    def shape(self) -> tuple[int, ...]:
        return self.data.shape

    @property
    def dtype(self) -> np.dtype:
        return self.data.dtype

    @property
    def ndim(self) -> int:
        return self.data.ndim
