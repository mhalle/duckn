"""Thin wrapper pairing array data with duckn metadata.

Provides a single object to pass around instead of ``(data, meta)`` tuples.
Lazily computes spatial geometry on first access.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Any

import numpy as np

from .models import DucknMetadata
from .spatial import VolumeGeometry


@dataclass
class Volume:
    """A duckn volume: array data paired with spatial metadata."""

    data: np.ndarray
    meta: DucknMetadata

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
