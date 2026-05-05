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
    return Volume(data=data, metadata=meta)


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
    return Volume(data=data, metadata=meta)


# ---- Default: isotropic ----


class TestIsotropicDefault:
    def test_makes_isotropic(self):
        vol = _make_volume()
        result = resample(vol)
        sp = result.geometry.voxel_size
        assert np.allclose(sp, sp[0], rtol=1e-3)

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

    def test_origin_preserved(self):
        vol = _make_volume()
        result = resample(vol, spacing=1.0)
        assert result.metadata.space_origin == vol.metadata.space_origin

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
