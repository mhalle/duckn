"""Tests for duckn.resample."""

import numpy as np
import pytest

from duckn.models import AxisKind, AxisMetadata, Centering, DucknMetadata
from duckn.resample import Interpolation, resample
from duckn.volume import Volume


def _make_volume(shape=(20, 64, 64), spacing=(2.0, 0.7, 0.7), dtype="uint16"):
    """Create a test volume with a sphere for visual verification."""
    data = np.zeros(shape, dtype=dtype)
    # Place a sphere in the center
    center = np.array(shape) / 2
    for i in range(shape[0]):
        for j in range(shape[1]):
            for k in range(shape[2]):
                dist = np.sqrt(
                    ((i - center[0]) * spacing[0]) ** 2
                    + ((j - center[1]) * spacing[1]) ** 2
                    + ((k - center[2]) * spacing[2]) ** 2
                )
                if dist < 10:
                    data[i, j, k] = 1000

    meta = DucknMetadata(
        space="left-posterior-superior",
        space_origin=[0.0, 0.0, 0.0],
        axes=[
            AxisMetadata(
                kind=AxisKind.SPACE,
                centering=Centering.CELL,
                space_direction=[0, 0, spacing[0]],
            ),
            AxisMetadata(
                kind=AxisKind.SPACE,
                centering=Centering.CELL,
                space_direction=[0, spacing[1], 0],
            ),
            AxisMetadata(
                kind=AxisKind.SPACE,
                centering=Centering.CELL,
                space_direction=[spacing[2], 0, 0],
            ),
        ],
    )
    return Volume(raw=data, metadata=meta)


def _make_labelmap(shape=(20, 64, 64), spacing=(2.0, 0.7, 0.7)):
    """Create a test labelmap with three labeled regions."""
    data = np.zeros(shape, dtype="uint8")
    data[2:8, 10:30, 10:30] = 1
    data[8:14, 20:50, 20:50] = 2
    data[14:18, 30:60, 30:60] = 3

    meta = DucknMetadata(
        space="left-posterior-superior",
        space_origin=[0.0, 0.0, 0.0],
        axes=[
            AxisMetadata(
                kind=AxisKind.SPACE,
                centering=Centering.CELL,
                space_direction=[0, 0, spacing[0]],
            ),
            AxisMetadata(
                kind=AxisKind.SPACE,
                centering=Centering.CELL,
                space_direction=[0, spacing[1], 0],
            ),
            AxisMetadata(
                kind=AxisKind.SPACE,
                centering=Centering.CELL,
                space_direction=[spacing[2], 0, 0],
            ),
        ],
    )
    return Volume(raw=data, metadata=meta)


# ---- Default: isotropic ----


class TestIsotropicDefault:
    def test_makes_isotropic(self):
        vol = _make_volume()
        result = resample(vol)
        sp = result.geometry.voxel_size
        # Exact isotropy is generally unreachable: the sample count is an
        # integer, so the realized spacing is extent / round(n * zoom). The
        # residual is bounded by roughly half a sample over the axis.
        assert np.allclose(sp, sp[0], rtol=1e-2)

    def test_matches_finest_spacing(self):
        vol = _make_volume(spacing=(2.0, 0.7, 0.7))
        result = resample(vol)
        assert np.allclose(result.geometry.voxel_size, 0.7, rtol=1e-2)

    def test_already_isotropic_is_noop(self):
        vol = _make_volume(spacing=(1.0, 1.0, 1.0))
        result = resample(vol)
        assert result is vol  # same object, no copy

    def test_shape_changes_on_coarse_axis(self):
        vol = _make_volume(shape=(20, 64, 64), spacing=(2.0, 0.7, 0.7))
        result = resample(vol)
        # Slice axis should be upsampled: 20 * (2.0/0.7) ≈ 57
        assert result.shape[0] > vol.shape[0]
        # In-plane should stay same
        assert result.shape[1] == vol.shape[1]
        assert result.shape[2] == vol.shape[2]


# ---- Spacing ----


class TestSpacing:
    def test_isotropic_1mm(self):
        vol = _make_volume(spacing=(2.0, 0.7, 0.7))
        result = resample(vol, spacing=1.0)
        assert np.allclose(result.geometry.voxel_size, 1.0, rtol=1e-2)

    def test_upsample(self):
        vol = _make_volume(shape=(20, 64, 64), spacing=(2.0, 2.0, 2.0))
        result = resample(vol, spacing=1.0)
        assert result.shape == (40, 128, 128)

    def test_downsample(self):
        vol = _make_volume(shape=(20, 64, 64), spacing=(1.0, 1.0, 1.0))
        result = resample(vol, spacing=2.0)
        assert result.shape == (10, 32, 32)


# ---- Shape ----


class TestShape:
    def test_cube_scalar(self):
        vol = _make_volume(shape=(20, 64, 64))
        result = resample(vol, shape=32)
        assert result.shape == (32, 32, 32)

    def test_explicit_tuple(self):
        vol = _make_volume(shape=(20, 64, 64))
        result = resample(vol, shape=(10, 32, 32))
        assert result.shape == (10, 32, 32)

    def test_non_uniform_shape(self):
        vol = _make_volume(shape=(20, 64, 64))
        result = resample(vol, shape=(40, 32, 32))
        assert result.shape == (40, 32, 32)

    def test_wrong_length_raises(self):
        vol = _make_volume()
        with pytest.raises(ValueError, match="length"):
            resample(vol, shape=(128, 128))


# ---- Factor ----


class TestFactor:
    def test_uniform_double(self):
        vol = _make_volume(shape=(20, 64, 64))
        result = resample(vol, factor=2)
        assert result.shape == (40, 128, 128)

    def test_uniform_half(self):
        vol = _make_volume(shape=(20, 64, 64))
        result = resample(vol, factor=0.5)
        assert result.shape == (10, 32, 32)

    def test_per_axis(self):
        vol = _make_volume(shape=(20, 64, 64))
        result = resample(vol, factor=[2, 1, 1])
        assert result.shape == (40, 64, 64)

    def test_wrong_length_raises(self):
        vol = _make_volume()
        with pytest.raises(ValueError, match="length"):
            resample(vol, factor=[2, 1])


# ---- Interpolation ----


class TestInterpolation:
    def test_nearest_preserves_labels(self):
        seg = _make_labelmap()
        labels_before = set(np.unique(seg.data))
        result = resample(seg, order=Interpolation.NEAREST)
        labels_after = set(np.unique(result.data))
        assert labels_before == labels_after

    def test_nearest_preserves_dtype(self):
        seg = _make_labelmap()
        result = resample(seg, order=0)
        assert result.dtype == seg.dtype

    def test_linear_default(self):
        vol = _make_volume()
        result = resample(vol, spacing=1.0)
        # Linear interpolation produces float-ish values but
        # we don't cast back for non-nearest
        assert result.data.dtype in (np.float32, np.float64)

    def test_int_order_works(self):
        vol = _make_volume()
        r1 = resample(vol, spacing=1.0, order=Interpolation.LINEAR)
        r2 = resample(vol, spacing=1.0, order=1)
        assert np.array_equal(r1.data, r2.data)


# ---- Metadata ----


class TestMetadata:
    def test_spacing_updated(self):
        vol = _make_volume(spacing=(2.0, 0.7, 0.7))
        result = resample(vol, spacing=1.0)
        assert np.allclose(result.geometry.voxel_size, 1.0, rtol=1e-2)

    def test_origin_shifts_under_cell_centering(self):
        # Cell centering fixes the outer boundary, not the first sample, so
        # the first sample center moves inward by half the spacing change.
        vol = _make_volume(spacing=(2.0, 2.0, 2.0))
        result = resample(vol, spacing=1.0)
        before = np.array(vol.metadata.space_origin)
        after = np.array(result.metadata.space_origin)
        assert np.allclose(after - before, 0.5 * (1.0 - 2.0), atol=1e-9)

    def test_origin_preserved_under_node_centering(self):
        # Node centering fixes the first and last samples themselves.
        vol = _make_volume(spacing=(2.0, 2.0, 2.0))
        for ax in vol.metadata.axes:
            ax.centering = Centering.NODE
        result = resample(vol, spacing=1.0)
        assert np.allclose(
            result.metadata.space_origin, vol.metadata.space_origin, atol=1e-9
        )

    def test_space_preserved(self):
        vol = _make_volume()
        result = resample(vol, spacing=1.0)
        assert result.metadata.space == vol.metadata.space

    def test_samples_cleared(self):
        from duckn.models import SampleMetadata
        vol = _make_volume()
        # Add fake samples
        vol.metadata.axes[0].samples = [SampleMetadata(position=float(i)) for i in range(vol.shape[0])]
        # Clear cached geometry since we mutated meta
        if "geometry" in vol.__dict__:
            del vol.__dict__["geometry"]
        result = resample(vol, spacing=1.0)
        assert result.metadata.axes[0].samples is None


# ---- Mutual exclusivity ----


class TestValidation:
    def test_spacing_and_shape_raises(self):
        vol = _make_volume()
        with pytest.raises(ValueError, match="Only one"):
            resample(vol, spacing=1.0, shape=128)

    def test_spacing_and_factor_raises(self):
        vol = _make_volume()
        with pytest.raises(ValueError, match="Only one"):
            resample(vol, spacing=1.0, factor=2)

    def test_shape_and_factor_raises(self):
        vol = _make_volume()
        with pytest.raises(ValueError, match="Only one"):
            resample(vol, shape=128, factor=2)

    def test_all_three_raises(self):
        vol = _make_volume()
        with pytest.raises(ValueError, match="Only one"):
            resample(vol, spacing=1.0, shape=128, factor=2)


# ---- Centering: the sample/extent relationship ----


def _ramp_volume(n=10, spacing=2.0, origin=5.0, centering=Centering.CELL, direction=None):
    """A volume whose values are the world position of their own sample.

    Interpolation is exact on a linear function, so after any resample an
    interior sample must still hold its own world coordinate. That makes the
    data itself a check on the metadata: if the grid is misdescribed, the value
    and the declared position disagree, in world units.
    """
    unit = np.array(direction if direction is not None else [1.0, 0.0, 0.0], dtype=float)
    unit /= np.linalg.norm(unit)
    axes = [
        AxisMetadata(
            kind=AxisKind.SPACE,
            centering=centering,
            space_direction=list(spacing * unit) if i == 0
            else [spacing if j == i else 0.0 for j in range(3)],
        )
        for i in range(3)
    ]
    meta = DucknMetadata(
        space="right-anterior-superior",
        space_origin=[origin, 0.0, 0.0],
        axes=axes,
    )
    # Value = distance along axis 0 from the origin, projected to world x.
    coords = origin + spacing * unit[0] * np.arange(n)
    return Volume(raw=np.broadcast_to(coords.reshape(n, 1, 1), (n, n, n)).astype(float).copy(),
                  metadata=meta)


class TestCentering:
    @pytest.mark.parametrize("centering", [Centering.CELL, Centering.NODE])
    @pytest.mark.parametrize("factor", [2, 4])
    def test_declared_position_matches_the_data(self, centering, factor):
        """The grid the metadata describes is the grid the samples are on."""
        vol = _ramp_volume(centering=centering)
        result = resample(vol, factor=factor)
        geom = result.geometry
        n = result.raw.shape[0]
        mid = n // 2
        declared = np.array(
            [geom.index_to_world(np.array([i, mid, mid]))[0] for i in range(n)]
        )
        held = np.asarray(result.raw)[:, mid, mid]
        # Interpolation is only defined between the source sample centers.
        # Outside that hull the resampler clamps by design, so those samples
        # are not a claim about the grid — every sample within it is.
        src_first, src_last = 5.0, 5.0 + 2.0 * (10 - 1)
        interior = (declared >= src_first - 1e-9) & (declared <= src_last + 1e-9)
        assert interior.sum() >= n - factor
        assert np.allclose(declared[interior], held[interior], atol=1e-9)

    def test_cell_preserves_the_field_of_view(self):
        vol = _ramp_volume(n=10, spacing=2.0, origin=5.0, centering=Centering.CELL)
        result = resample(vol, factor=2)
        sp = float(np.asarray(result.metadata.axes[0].space_direction)[0])
        first = result.metadata.space_origin[0]
        n = result.raw.shape[0]
        assert np.isclose(first - sp / 2, 5.0 - 2.0 / 2)          # outer edge held
        assert np.isclose(first + sp * (n - 1) + sp / 2, 23.0 + 2.0 / 2)

    def test_node_preserves_the_sample_extent(self):
        vol = _ramp_volume(n=10, spacing=2.0, origin=5.0, centering=Centering.NODE)
        result = resample(vol, factor=2)
        sp = float(np.asarray(result.metadata.axes[0].space_direction)[0])
        n = result.raw.shape[0]
        assert np.isclose(result.metadata.space_origin[0], 5.0)   # end samples held
        assert np.isclose(result.metadata.space_origin[0] + sp * (n - 1), 23.0)

    def test_spacing_is_the_realized_one_not_the_requested_one(self):
        # 10 samples to 7 cannot land on the requested spacing; the array
        # means what it realized, so that is what must be declared.
        vol = _ramp_volume(n=10, spacing=2.0, centering=Centering.CELL)
        result = resample(vol, shape=(7, 7, 7))
        assert np.isclose(result.geometry.voxel_size[0], 10 * 2.0 / 7)

    def test_oblique_origin_shift_follows_the_axis_direction(self):
        # The half-spacing shift is along each axis's own direction, so a
        # rotated frame moves the origin in more than one component.
        vol = _ramp_volume(centering=Centering.CELL, direction=[1.0, 1.0, 0.0])
        result = resample(vol, factor=2)
        before = np.array(vol.metadata.space_origin)
        after = np.array(result.metadata.space_origin)
        moved = after - before
        # Every cell-centered axis contributes half its own spacing change
        # along its own direction. Axis 0 is oblique in x and y; axes 1 and 2
        # are axis-aligned, so y collects a contribution from each.
        d0 = 2.0 / np.sqrt(2)            # axis 0 spacing per component
        assert np.allclose(moved, [-d0 / 4, -d0 / 4 - 0.5, -0.5], atol=1e-9)

    def test_resolved_centering_is_recorded(self):
        vol = _ramp_volume(centering=Centering.CELL)
        for ax in vol.metadata.axes:
            ax.centering = None          # unknown, per spec
        result = resample(vol, factor=2)
        assert all(ax.centering is Centering.CELL for ax in result.metadata.axes)

    def test_override_beats_the_declared_value(self):
        vol = _ramp_volume(centering=Centering.CELL)
        result = resample(vol, factor=2, centering=Centering.NODE)
        assert np.isclose(result.metadata.space_origin[0], vol.metadata.space_origin[0])
        assert all(ax.centering is Centering.NODE for ax in result.metadata.axes)

    def test_disagreeing_axes_raise(self):
        vol = _ramp_volume(centering=Centering.CELL)
        vol.metadata.axes[1].centering = Centering.NODE
        with pytest.raises(ValueError, match="different centerings"):
            resample(vol, factor=2)


# ---- Anti-aliasing ----


class TestAntiAlias:
    def test_downsampling_blurs_by_default(self):
        vol = _make_volume()
        blurred = resample(vol, factor=0.5)
        sharp = resample(vol, factor=0.5, anti_alias=False)
        assert not np.allclose(np.asarray(blurred.raw), np.asarray(sharp.raw))

    def test_disabled_matches_plain_zoom(self):
        """anti_alias=False is a plain ndimage.zoom, for consumers validated on one."""
        ndimage = pytest.importorskip("scipy.ndimage")
        vol = _make_volume()
        result = resample(vol, factor=0.5, anti_alias=False, centering=Centering.NODE)
        expected = ndimage.zoom(
            np.asarray(vol.raw).astype(float), 0.5, order=1,
            mode="nearest", grid_mode=False,
        )
        assert np.allclose(np.asarray(result.raw), expected)

    def test_no_effect_when_upsampling(self):
        vol = _make_volume()
        assert np.allclose(
            np.asarray(resample(vol, factor=2).raw),
            np.asarray(resample(vol, factor=2, anti_alias=False).raw),
        )

    def test_no_effect_for_nearest(self):
        vol = _make_labelmap()
        assert np.array_equal(
            np.asarray(resample(vol, factor=0.5, order=0).raw),
            np.asarray(resample(vol, factor=0.5, order=0, anti_alias=False).raw),
        )
