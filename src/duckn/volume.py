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
    meta: DucknMetadata

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

        Parameters
        ----------
        to_space : target space name (e.g., "nifti:mni152")
        affine : N×(N+1) forward affine matrix (world → target)
        inverse : N×(N+1) inverse affine matrix (target → world).
                  Useful when a registration tool returns the inverse
                  direction (e.g., SimpleITK's fixed→moving transform).
                  The forward will be computed by matrix inversion.
        identity : if True, declares world space IS the target space
        metadata : optional provenance dict (software, method, date, etc.)

        Exactly one of affine, inverse, or identity must be specified.
        """
        import numpy as np

        from .models import (
            SpaceReference,
            SpaceTransformEntry,
            TransformObject,
        )

        n_specified = sum(x is not None for x in (affine, inverse)) + int(identity)
        if n_specified != 1:
            raise ValueError("Exactly one of affine, inverse, or identity must be specified")

        forward = None
        inverse_obj = None

        if identity:
            forward = TransformObject(identity=True)
        elif affine is not None:
            matrix = np.asarray(affine, dtype=float).tolist()
            forward = TransformObject(affine=matrix)
        elif inverse is not None:
            matrix = np.asarray(inverse, dtype=float).tolist()
            inverse_obj = TransformObject(affine=matrix)

        entry = SpaceTransformEntry(
            to=SpaceReference(name=to_space),
            forward=forward,
            inverse=inverse_obj,
            metadata=metadata,
        )

        if self.meta.space_transforms is None:
            self.meta.space_transforms = []
        self.meta.space_transforms.append(entry)

        # Invalidate cached geometry so it picks up the new transform
        if "geometry" in self.__dict__:
            del self.__dict__["geometry"]

    @property
    def extensions(self) -> Extensions:
        """Typed access to extensions."""
        return Extensions(self.meta.extensions)

    def get_extension(self, name: str) -> Any | None:
        """Get a top-level extension by name, or None if not present."""
        if self.meta.extensions is None:
            return None
        return self.meta.extensions.get(name)

    def set_extension(self, name: str, value: Any) -> None:
        """Set a top-level extension. Overwrites if already present."""
        if self.meta.extensions is None:
            self.meta.extensions = {}
        self.meta.extensions[name] = value

    @cached_property
    def geometry(self) -> VolumeGeometry:
        """Spatial geometry, computed lazily from metadata + shape."""
        return VolumeGeometry.from_metadata(self.meta, self.data.shape)

    @property
    def shape(self) -> tuple[int, ...]:
        return self.data.shape

    @property
    def dtype(self) -> np.dtype:
        return self.data.dtype

    @property
    def ndim(self) -> int:
        return self.data.ndim
