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

from .models import (
    Centering,
    DucknMetadata,
    SpaceTransformEntry,
    TransformObject,
)


_BUILTIN_SPACES = {"world", "axis-aligned", "axis-aligned-centered", "index"}


def has_uniform_spacing(meta: DucknMetadata) -> bool:
    """Check if a volume has uniform spacing (no per-sample positions).

    Can be called before constructing a VolumeGeometry to decide
    whether the uniform-spacing code path is valid.
    """
    if meta.axes is None:
        return True
    spatial_axes = [ax for ax in meta.axes if ax.space_direction is not None]
    return all(
        ax.samples is None
        or all(
            s.position is None and s.origin is None
            for s in ax.samples
        )
        for ax in spatial_axes
    )


@dataclass
class _NamedTransform:
    """A parsed named space transform."""

    name: str           # target space name
    from_space: str     # built-in source space
    forward: np.ndarray | None   # (ndim, ndim+1) affine matrix or None
    inverse: np.ndarray | None   # (ndim, ndim+1) affine matrix or None
    is_identity: bool
    metadata: dict | None


def _parse_transform_object(
    obj: TransformObject, ndim: int,
) -> tuple[np.ndarray | None, bool]:
    """Parse a TransformObject into an affine matrix.

    Returns (matrix_or_None, is_identity).
    """
    if obj.identity:
        return np.eye(ndim, ndim + 1), True
    if obj.affine is not None:
        m = np.array(obj.affine, dtype=float)
        if m.shape != (ndim, ndim + 1):
            raise ValueError(
                f"Affine matrix must be {ndim}×{ndim + 1}, got {m.shape}"
            )
        return m, False
    return None, False


def _invert_affine(m: np.ndarray) -> np.ndarray:
    """Invert an (N, N+1) affine matrix."""
    ndim = m.shape[0]
    A = m[:, :ndim]
    t = m[:, ndim]
    A_inv = np.linalg.inv(A)
    result = np.zeros_like(m)
    result[:, :ndim] = A_inv
    result[:, ndim] = -A_inv @ t
    return result


def _parse_space_transforms(
    entries: list[SpaceTransformEntry] | None,
    ndim: int,
) -> dict[str, _NamedTransform]:
    """Parse space_transforms metadata into _NamedTransform objects."""
    if not entries:
        return {}

    transforms: dict[str, _NamedTransform] = {}

    for entry in entries:
        # Target space name
        to_ref = entry.to
        if to_ref.name is None:
            continue  # skip built-in → built-in (not useful here)
        name = to_ref.name

        # Source space (defaults to "world")
        from_ref = entry.from_
        if from_ref is None or from_ref.space is None:
            from_space = "world"
        else:
            from_space = from_ref.space

        if from_space not in _BUILTIN_SPACES:
            continue  # can only chain from built-in spaces

        # Parse forward/inverse
        fwd = None
        fwd_identity = False
        inv = None
        inv_identity = False

        if entry.forward is not None:
            fwd, fwd_identity = _parse_transform_object(entry.forward, ndim)
        if entry.inverse is not None:
            inv, inv_identity = _parse_transform_object(entry.inverse, ndim)

        # Compute missing direction by inversion
        is_identity = fwd_identity or inv_identity
        if is_identity:
            eye = np.eye(ndim, ndim + 1)
            fwd = eye
            inv = eye
        else:
            if fwd is not None and inv is None:
                inv = _invert_affine(fwd)
            elif inv is not None and fwd is None:
                fwd = _invert_affine(inv)

        transforms[name] = _NamedTransform(
            name=name,
            from_space=from_space,
            forward=fwd,
            inverse=inv,
            is_identity=is_identity,
            metadata=entry.metadata,
        )

    return transforms


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

    # Whether all spatial axes have uniform spacing (no per-sample positions)
    _uniform: bool

    # Named space transforms from space_transforms metadata
    # Maps target space name → _NamedTransform
    _named_transforms: dict

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

        # Affine: world = D @ index + o
        # space_origin is the position of the first sample (index 0),
        # whether cell-centered or node-centered. Centering affects
        # extent calculations, not the index→world transform.
        affine = np.zeros((ndim, ndim + 1))
        affine[:, :ndim] = D
        affine[:, ndim] = origin

        # Inverse: index = D^{-1} @ (world - o)
        D_inv = np.linalg.inv(D)
        affine_inv = np.zeros((ndim, ndim + 1))
        affine_inv[:, :ndim] = D_inv
        affine_inv[:, ndim] = -D_inv @ origin

        # Check for per-sample positions (non-uniform spacing)
        uniform = all(
            ax.samples is None
            or all(
                s.position is None and s.origin is None
                for s in ax.samples
            )
            for ax in spatial_axes
        )

        if not uniform:
            import warnings
            warnings.warn(
                "Volume has per-sample positions (non-uniform spacing). "
                "VolumeGeometry assumes uniform spacing — transforms may "
                "be approximate for non-uniformly sampled axes.",
                stacklevel=2,
            )

        # Parse named space transforms
        named = _parse_space_transforms(meta.space_transforms, ndim)

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
            _uniform=uniform,
            _named_transforms=named,
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
        """Physical extent of the volume along each axis (axis-aligned).

        For cell-centered data, extent = n × spacing (covers full cells).
        For node-centered data, extent = (n-1) × spacing (between nodes).
        """
        extents = np.zeros(self.ndim)
        for j in range(self.ndim):
            n = self.shape[j]
            s = self.S[j, j]
            if self.centering[j] == 0.5:  # cell
                extents[j] = n * s
            else:  # node
                extents[j] = (n - 1) * s
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
    def has_uniform_spacing(self) -> bool:
        """True if spacing is constant along each axis (no per-sample positions)."""
        return self._uniform

    @property
    def is_isotropic(self) -> bool:
        """True if all voxel spacings are equal."""
        return np.allclose(self.spacing, self.spacing[0], rtol=1e-6)

    @property
    def named_spaces(self) -> list[str]:
        """List available named coordinate spaces (from space_transforms)."""
        return list(self._named_transforms.keys())

    # ------------------------------------------------------------------
    # Named space helpers
    # ------------------------------------------------------------------

    def _apply_affine(self, m: np.ndarray, coords: np.ndarray) -> np.ndarray:
        """Apply an (N, N+1) affine matrix to coordinates."""
        coords = np.asarray(coords, dtype=float)
        A = m[:, :self.ndim]
        t = m[:, self.ndim]
        if coords.ndim == 1:
            return A @ coords + t
        else:
            return (A @ coords.T).T + t

    def _to_builtin(self, coords: np.ndarray, space: str) -> np.ndarray:
        """Convert from a built-in space to world coordinates."""
        if space == "world":
            return np.asarray(coords, dtype=float)
        elif space == "axis-aligned":
            return self.axis_aligned_to_world(coords)
        elif space == "axis-aligned-centered":
            aa = self.centered_to_axis_aligned(coords)
            return self.axis_aligned_to_world(aa)
        elif space == "index":
            return self.index_to_world(coords)
        raise ValueError(f"Unknown built-in space: {space!r}")

    def _from_builtin(self, world: np.ndarray, space: str) -> np.ndarray:
        """Convert from world coordinates to a built-in space."""
        if space == "world":
            return world
        elif space == "axis-aligned":
            return self.world_to_axis_aligned(world)
        elif space == "axis-aligned-centered":
            aa = self.world_to_axis_aligned(world)
            return self.axis_aligned_to_centered(aa)
        elif space == "index":
            return self.world_to_index(world)
        raise ValueError(f"Unknown built-in space: {space!r}")

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

        Removes rotation around the origin.
        """
        world = np.asarray(world, dtype=float)
        o = self.origin
        if world.ndim == 1:
            return self.R.T @ (world - o) + o
        else:
            return ((world - o) @ self.R).T.T + o  # broadcast

    def axis_aligned_to_world(self, aa: np.ndarray) -> np.ndarray:
        """Convert axis-aligned coordinates to world coordinates."""
        aa = np.asarray(aa, dtype=float)
        o = self.origin
        if aa.ndim == 1:
            return self.R @ (aa - o) + o
        else:
            return ((aa - o) @ self.R.T).T.T + o

    # ------------------------------------------------------------------
    # Axis-Aligned ↔ Axis-Aligned-Centered
    # ------------------------------------------------------------------

    @property
    def _aa_center(self) -> np.ndarray:
        """Center of the volume in axis-aligned coordinates."""
        o = self.origin  # = position of first sample in world
        # In axis-aligned space, the origin stays the same (rotation around origin)
        extent = self.volume_size
        # For cell centering, first sample is at center of first cell,
        # so the volume extends from origin - 0.5*spacing to origin + (n-0.5)*spacing.
        # Center = origin + (n-1)/2 * spacing (for both cell and node).
        half_extent = np.zeros(self.ndim)
        for j in range(self.ndim):
            half_extent[j] = (self.shape[j] - 1) / 2.0 * self.S[j, j]
        return o + half_extent

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
        if space in _BUILTIN_SPACES:
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
        elif space in self._named_transforms:
            # Named → inverse transform → built-in from_space → index
            nt = self._named_transforms[space]
            if nt.inverse is None:
                raise ValueError(f"No inverse transform for space {space!r}")
            builtin_coords = self._apply_affine(nt.inverse, coords)
            world = self._to_builtin(builtin_coords, nt.from_space)
            idx = self.world_to_index(world)
        else:
            raise ValueError(
                f"Unknown space: {space!r}. "
                f"Built-in: {sorted(_BUILTIN_SPACES)}. "
                f"Named: {self.named_spaces}"
            )

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
        if space in _BUILTIN_SPACES:
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
        elif space in self._named_transforms:
            # index → world → built-in from_space → forward transform → named
            nt = self._named_transforms[space]
            if nt.forward is None:
                raise ValueError(f"No forward transform for space {space!r}")
            world = self.index_to_world(index)
            builtin_coords = self._from_builtin(world, nt.from_space)
            return self._apply_affine(nt.forward, builtin_coords)
        else:
            raise ValueError(
                f"Unknown space: {space!r}. "
                f"Built-in: {sorted(_BUILTIN_SPACES)}. "
                f"Named: {self.named_spaces}"
            )

    # ------------------------------------------------------------------
    # Bounds checking
    # ------------------------------------------------------------------

    def transform(
        self,
        coords: np.ndarray,
        from_space: str,
        to_space: str,
    ) -> np.ndarray:
        """Transform coordinates between any two spaces.

        Chains through index as the hub:
        from_space → index → to_space.

        Parameters
        ----------
        coords : array of shape (ndim,) or (N, ndim)
        from_space : source space (built-in or named)
        to_space : destination space (built-in or named)
        """
        if from_space == to_space:
            return np.asarray(coords, dtype=float)
        idx = self.to_index(coords, space=from_space)
        return self.from_index(idx, space=to_space)

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
