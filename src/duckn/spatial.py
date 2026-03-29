"""Spatial coordinate transforms for duckn volumes.

Provides conversions between the four canonical coordinate spaces
defined by the duckn specification:

- **index**: discrete array coordinates (0, 1, 2, ...)
- **world**: continuous physical coordinates defined by space_origin
  and space_direction
- **axis-aligned**: axis-aligned physical coordinates (rotation removed,
  same origin and voxel scale as world)
- **axis-aligned-centered**: same as axis-aligned but origin at volume center

All transforms assume uniform sampling (no per-sample positions).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .models import Centering, DucknMetadata


@dataclass(frozen=True)
class VolumeGeometry:
    """Spatial geometry of a uniformly sampled duckn volume.

    Constructed from DucknMetadata + array shape. Provides coordinate
    transforms and spatial queries.
    """

    # Array shape (C-order: slowest to fastest)
    shape: tuple[int, ...]

    # Space dimension
    ndim: int

    # Direction matrix: columns are space_direction vectors
    # D[i, j] = space_direction[j][i]
    D: np.ndarray  # (ndim, ndim)

    # Space origin
    origin: np.ndarray  # (ndim,)

    # Centering offset: 0.5 for cell, 0.0 for node
    centering: np.ndarray  # (ndim,)

    # Derived quantities (computed once)
    spacing: np.ndarray  # per-axis spacing (magnitude of direction vectors)
    direction_cosines: np.ndarray  # unit direction vectors (ndim, ndim)
    R: np.ndarray  # rotation matrix from polar decomposition
    S: np.ndarray  # symmetric spacing/shear matrix

    # Affine: index → world (ndim × ndim+1)
    affine: np.ndarray

    # Inverse affine: world → index
    affine_inv: np.ndarray

    @staticmethod
    def from_metadata(
        meta: DucknMetadata,
        shape: tuple[int, ...],
    ) -> VolumeGeometry:
        """Build VolumeGeometry from duckn metadata and array shape.

        Only considers spatial axes (those with space_direction).
        Assumes uniform sampling (no per-sample positions).
        """
        if meta.axes is None:
            raise ValueError("Metadata has no axes")

        spatial_axes = [ax for ax in meta.axes if ax.space_direction is not None]
        ndim = len(spatial_axes)
        if ndim == 0:
            raise ValueError("No spatial axes found")

        # Extract spatial dimensions from shape (in axis order)
        spatial_indices = [
            i for i, ax in enumerate(meta.axes)
            if ax.space_direction is not None
        ]
        spatial_shape = tuple(shape[i] for i in spatial_indices)

        # Direction matrix: column j = space_direction of axis j
        D = np.zeros((ndim, ndim))
        for j, ax in enumerate(spatial_axes):
            D[:, j] = ax.space_direction

        # Origin
        origin = np.array(meta.space_origin or [0.0] * ndim)

        # Centering
        centering = np.array([
            0.5 if ax.centering == Centering.CELL or ax.centering is None
            else 0.0
            for ax in spatial_axes
        ])

        # Spacing (magnitude of each direction vector)
        spacing = np.array([np.linalg.norm(D[:, j]) for j in range(ndim)])

        # Direction cosines (unit vectors)
        direction_cosines = np.zeros_like(D)
        for j in range(ndim):
            mag = spacing[j]
            if mag > 0:
                direction_cosines[:, j] = D[:, j] / mag

        # Polar decomposition: D = R @ S
        # R = rotation, S = symmetric positive definite (spacing/shear)
        from scipy.linalg import polar
        R, S = polar(D)

        # Affine: world = D @ (index + c) + o
        # As (ndim × ndim+1): [D | o + D @ c]
        Dc = D @ centering
        affine = np.zeros((ndim, ndim + 1))
        affine[:, :ndim] = D
        affine[:, ndim] = origin + Dc

        # Inverse: index = D^{-1} @ (world - o) - c
        D_inv = np.linalg.inv(D)
        affine_inv = np.zeros((ndim, ndim + 1))
        affine_inv[:, :ndim] = D_inv
        affine_inv[:, ndim] = -D_inv @ origin - centering

        return VolumeGeometry(
            shape=spatial_shape,
            ndim=ndim,
            D=D,
            origin=origin,
            centering=centering,
            spacing=spacing,
            direction_cosines=direction_cosines,
            R=R,
            S=S,
            affine=affine,
            affine_inv=affine_inv,
        )

    # ------------------------------------------------------------------
    # Spatial properties
    # ------------------------------------------------------------------

    @property
    def voxel_size(self) -> np.ndarray:
        """Physical size of a voxel along each axis."""
        return self.spacing

    @property
    def volume_size(self) -> np.ndarray:
        """Physical extent of the volume along each axis (axis-aligned)."""
        # Extent in axis-aligned space
        extents = np.zeros(self.ndim)
        for j in range(self.ndim):
            extents[j] = self.S[j, j] * self.shape[j]
        return extents

    @property
    def volume_center_world(self) -> np.ndarray:
        """World coordinates of the volume center."""
        center_index = np.array([(n - 1) / 2.0 for n in self.shape])
        return self.index_to_world(center_index)

    @property
    def is_axis_aligned(self) -> bool:
        """True if direction vectors are aligned to coordinate axes."""
        return np.allclose(self.R, np.eye(self.ndim), atol=1e-6)

    @property
    def is_isotropic(self) -> bool:
        """True if all voxel spacings are equal."""
        return np.allclose(self.spacing, self.spacing[0], rtol=1e-6)

    # ------------------------------------------------------------------
    # Index ↔ World
    # ------------------------------------------------------------------

    def index_to_world(self, index: np.ndarray) -> np.ndarray:
        """Convert index coordinates to world coordinates.

        Parameters
        ----------
        index : array of shape (ndim,) or (N, ndim)

        Returns
        -------
        world : same shape as input
        """
        index = np.asarray(index, dtype=float)
        if index.ndim == 1:
            return self.affine[:, :self.ndim] @ index + self.affine[:, self.ndim]
        else:
            return (self.affine[:, :self.ndim] @ index.T).T + self.affine[:, self.ndim]

    def world_to_index(self, world: np.ndarray) -> np.ndarray:
        """Convert world coordinates to continuous index coordinates.

        Parameters
        ----------
        world : array of shape (ndim,) or (N, ndim)

        Returns
        -------
        index : same shape as input (continuous, not rounded)
        """
        world = np.asarray(world, dtype=float)
        if world.ndim == 1:
            return self.affine_inv[:, :self.ndim] @ world + self.affine_inv[:, self.ndim]
        else:
            return (self.affine_inv[:, :self.ndim] @ world.T).T + self.affine_inv[:, self.ndim]

    # ------------------------------------------------------------------
    # World ↔ Axis-Aligned
    # ------------------------------------------------------------------

    def world_to_axis_aligned(self, world: np.ndarray) -> np.ndarray:
        """Convert world coordinates to axis-aligned coordinates.

        Removes rotation around the adjusted origin p = o + D @ c.
        """
        world = np.asarray(world, dtype=float)
        p = self.origin + self.D @ self.centering
        if world.ndim == 1:
            return self.R.T @ (world - p) + p
        else:
            return ((world - p) @ self.R).T.T + p  # broadcast

    def axis_aligned_to_world(self, aa: np.ndarray) -> np.ndarray:
        """Convert axis-aligned coordinates to world coordinates."""
        aa = np.asarray(aa, dtype=float)
        p = self.origin + self.D @ self.centering
        if aa.ndim == 1:
            return self.R @ (aa - p) + p
        else:
            return ((aa - p) @ self.R.T).T.T + p

    # ------------------------------------------------------------------
    # Axis-Aligned ↔ Axis-Aligned-Centered
    # ------------------------------------------------------------------

    @property
    def _aa_center(self) -> np.ndarray:
        """Center of the volume in axis-aligned coordinates."""
        p_aa = self.origin + self.D @ self.centering
        p_aa_in_aa = self.R.T @ (p_aa - p_aa) + p_aa  # = p_aa
        extent = np.zeros(self.ndim)
        for j in range(self.ndim):
            extent[j] = self.S[j, j] * self.shape[j]
        return p_aa + extent / 2

    def axis_aligned_to_centered(self, aa: np.ndarray) -> np.ndarray:
        """Convert axis-aligned to axis-aligned-centered coordinates."""
        return np.asarray(aa, dtype=float) - self._aa_center

    def centered_to_axis_aligned(self, aac: np.ndarray) -> np.ndarray:
        """Convert axis-aligned-centered to axis-aligned coordinates."""
        return np.asarray(aac, dtype=float) + self._aa_center

    # ------------------------------------------------------------------
    # Convenience: any space → index
    # ------------------------------------------------------------------

    def to_index(
        self,
        coords: np.ndarray,
        space: str = "world",
        *,
        round: bool = False,
        clamp: bool = False,
    ) -> np.ndarray:
        """Convert coordinates from any named space to index.

        Parameters
        ----------
        coords : array of shape (ndim,) or (N, ndim)
        space : one of "world", "axis-aligned", "axis-aligned-centered", "index"
        round : if True, round to nearest integer voxel
        clamp : if True, clamp to valid range [0, shape-1]

        Returns
        -------
        index : index coordinates (float if not rounded, int if rounded)
        """
        if space == "index":
            idx = np.asarray(coords, dtype=float)
        elif space == "world":
            idx = self.world_to_index(coords)
        elif space == "axis-aligned":
            world = self.axis_aligned_to_world(coords)
            idx = self.world_to_index(world)
        elif space == "axis-aligned-centered":
            aa = self.centered_to_axis_aligned(coords)
            world = self.axis_aligned_to_world(aa)
            idx = self.world_to_index(world)
        else:
            raise ValueError(f"Unknown space: {space!r}")

        if clamp:
            idx = self.clamp_index(idx)
        if round:
            idx = self.round_index(idx)

        return idx

    def from_index(
        self,
        index: np.ndarray,
        space: str = "world",
    ) -> np.ndarray:
        """Convert index coordinates to any named space.

        Parameters
        ----------
        index : array of shape (ndim,) or (N, ndim)
        space : one of "world", "axis-aligned", "axis-aligned-centered", "index"

        Returns
        -------
        coords : coordinates in the target space
        """
        if space == "index":
            return np.asarray(index, dtype=float)
        elif space == "world":
            return self.index_to_world(index)
        elif space == "axis-aligned":
            world = self.index_to_world(index)
            return self.world_to_axis_aligned(world)
        elif space == "axis-aligned-centered":
            world = self.index_to_world(index)
            aa = self.world_to_axis_aligned(world)
            return self.axis_aligned_to_centered(aa)
        else:
            raise ValueError(f"Unknown space: {space!r}")

    # ------------------------------------------------------------------
    # Bounds checking
    # ------------------------------------------------------------------

    def in_bounds(
        self,
        coords: np.ndarray,
        space: str = "world",
    ) -> np.ndarray | bool:
        """Check if coordinates are within the volume bounds.

        Parameters
        ----------
        coords : array of shape (ndim,) or (N, ndim)
        space : coordinate space of the input

        Returns
        -------
        bool or array of bool
        """
        idx = self.to_index(coords, space=space)
        idx = np.asarray(idx, dtype=float)

        if idx.ndim == 1:
            return all(0 <= idx[j] < self.shape[j] for j in range(self.ndim))
        else:
            return np.all(
                (idx >= 0) & (idx < np.array(self.shape)),
                axis=-1,
            )

    def clamp_index(self, index: np.ndarray) -> np.ndarray:
        """Clamp continuous index coordinates to valid range [0, shape-1]."""
        index = np.asarray(index, dtype=float)
        for j in range(self.ndim):
            index[..., j] = np.clip(index[..., j], 0, self.shape[j] - 1)
        return index

    def round_index(self, index: np.ndarray) -> np.ndarray:
        """Round continuous index to nearest integer voxel coordinates."""
        return np.round(np.asarray(index, dtype=float)).astype(int)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        sp = ", ".join(f"{s:.4f}" for s in self.spacing)
        sz = ", ".join(f"{s:.2f}" for s in self.volume_size)
        return (
            f"VolumeGeometry(shape={self.shape}, spacing=[{sp}], "
            f"volume_size=[{sz}], isotropic={self.is_isotropic}, "
            f"axis_aligned={self.is_axis_aligned})"
        )
