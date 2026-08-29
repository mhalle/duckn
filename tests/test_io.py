"""Tests for duckn.io — the top-level read/write API.

This module had no coverage, which is how a transposition bug survived in
it: `io.read` and `io.write` were both wrong about NRRD axis order in
mutually canceling ways, so an io-to-io round trip looked perfect. Several
tests here deliberately check against an *independent* reader for that
reason.
"""

from __future__ import annotations

import numpy as np
import pytest

from duckn import io
from duckn.models import AxisMetadata, DucknMetadata
from duckn.volume import Volume


def _anisotropic_volume():
    """A volume whose axes are all distinguishable.

    Distinct extents per axis (2x9, 3x5, 4x1 mm) and a non-symmetric shape,
    so any transposition of data or geometry is detectable.
    """
    raw = np.arange(24, dtype=np.int16).reshape(2, 3, 4)
    meta = DucknMetadata(
        version="1.0",
        space="left-posterior-superior",
        space_origin=[10.0, 20.0, 30.0],
        sample_units="HU",
        axes=[
            AxisMetadata(kind="space", space_direction=[9.0, 0.0, 0.0], unit="mm"),
            AxisMetadata(kind="space", space_direction=[0.0, 5.0, 0.0], unit="mm"),
            AxisMetadata(kind="space", space_direction=[0.0, 0.0, 1.0], unit="mm"),
        ],
    )
    return Volume(raw=raw, metadata=meta)


def _directions(meta):
    return [ax.space_direction for ax in meta.axes]


class TestRoundTrip:
    """Every format must return what it was given."""

    @pytest.mark.parametrize("suffix", [".zarr", ".zarr.zip", ".nrrd"])
    def test_shape_and_values_survive(self, tmp_path, suffix):
        vol = _anisotropic_volume()
        path = tmp_path / f"vol{suffix}"
        io.write(vol, path)
        back = io.read(path)

        assert back.raw.shape == vol.raw.shape
        np.testing.assert_array_equal(back.data, vol.data)

    @pytest.mark.parametrize("suffix", [".zarr", ".zarr.zip", ".nrrd"])
    def test_geometry_survives(self, tmp_path, suffix):
        vol = _anisotropic_volume()
        path = tmp_path / f"vol{suffix}"
        io.write(vol, path)
        back = io.read(path)

        np.testing.assert_allclose(back.metadata.space_origin, [10.0, 20.0, 30.0])
        np.testing.assert_allclose(_directions(back.metadata), _directions(vol.metadata))

    def test_nifti_round_trip(self, tmp_path):
        pytest.importorskip("nibabel")
        vol = _anisotropic_volume()
        path = tmp_path / "vol.nii"
        io.write(vol, path)
        back = io.read(path)

        assert back.raw.shape == vol.raw.shape
        np.testing.assert_array_equal(back.data, vol.data)


class TestAgreesWithAnIndependentReader:
    """In-package round trips cannot catch a self-consistent error."""

    def test_written_nrrd_reads_correctly_in_simpleitk(self, tmp_path):
        sitk = pytest.importorskip("SimpleITK")

        vol = _anisotropic_volume()
        path = tmp_path / "vol.nrrd"
        io.write(vol, path)

        img = sitk.ReadImage(str(path))
        # SimpleITK reports size in xyz; duckn's shape is C-order zyx.
        assert tuple(img.GetSize()) == vol.raw.shape[::-1]
        # Spacing likewise reverses: axis extents are 9, 5, 1 mm in zyx.
        np.testing.assert_allclose(img.GetSpacing(), (1.0, 5.0, 9.0))
        # And the voxels must not be transposed.
        np.testing.assert_array_equal(sitk.GetArrayFromImage(img), vol.data)

    def test_matches_the_canonical_converter(self, tmp_path):
        """io and convert must produce the same file for the same volume."""
        pytest.importorskip("nrrd")
        import nrrd

        from duckn.convert import zarr_to_nrrd

        vol = _anisotropic_volume()
        via_io = tmp_path / "io.nrrd"
        io.write(vol, via_io)

        zarr_path = tmp_path / "vol.zarr"
        io.write(vol, zarr_path)
        via_convert = tmp_path / "convert.nrrd"
        zarr_to_nrrd(zarr_path, via_convert, overwrite=True)

        a_data, a_header = nrrd.read(str(via_io), index_order="C")
        b_data, b_header = nrrd.read(str(via_convert), index_order="C")

        np.testing.assert_array_equal(a_data, b_data)
        assert a_header["sizes"].tolist() == b_header["sizes"].tolist()
        np.testing.assert_allclose(
            a_header["space directions"], b_header["space directions"]
        )

    def test_nrrd_read_agrees_with_the_canonical_converter(self, tmp_path):
        """io.read must interpret a file the same way convert does."""
        pytest.importorskip("nrrd")
        from duckn.convert import nrrd_to_zarr

        vol = _anisotropic_volume()
        src = tmp_path / "src.nrrd"
        io.write(vol, src)

        via_io = io.read(src)
        zarr_path = tmp_path / "via_convert.zarr"
        nrrd_to_zarr(src, zarr_path)
        via_convert = io.read(zarr_path)

        assert via_io.raw.shape == via_convert.raw.shape
        np.testing.assert_array_equal(via_io.data, via_convert.data)
        np.testing.assert_allclose(
            _directions(via_io.metadata), _directions(via_convert.metadata)
        )


class TestCalibration:
    """duckn-spec §4.3: what is written must match what the metadata says."""

    def _ct_volume(self):
        meta = DucknMetadata(
            version="1.0",
            space="left-posterior-superior",
            space_origin=[0.0, 0.0, 0.0],
            sample_units="HU",
            value_transforms=[
                {"name": "linear", "parameters": {"slope": 1.0, "intercept": -1024.0}}
            ],
            axes=[
                AxisMetadata(kind="space", space_direction=v, unit="mm")
                for v in ([1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0])
            ],
        )
        raw = np.full((2, 2, 2), 1000, dtype=np.uint16)
        return Volume(raw=raw, metadata=meta)

    def test_zarr_preserves_stored_values_and_transforms(self, tmp_path):
        """Zarr can carry the transform, so the encoding is preserved."""
        vol = self._ct_volume()
        path = tmp_path / "ct.zarr"
        io.write(vol, path)
        back = io.read(path)

        np.testing.assert_array_equal(back.raw, vol.raw)
        assert back.metadata.value_transforms is not None
        np.testing.assert_allclose(back.data, -24.0)

    def test_nrrd_materializes(self, tmp_path):
        """NRRD cannot, so calibrated values are written under `sample units`."""
        nrrd = pytest.importorskip("nrrd")
        vol = self._ct_volume()
        path = tmp_path / "ct.nrrd"
        io.write(vol, path)

        values, header = nrrd.read(str(path), index_order="C")
        np.testing.assert_allclose(values, -24.0)
        assert header.get("sample units") == "HU"

    def test_nrrd_round_trip_preserves_the_quantity(self, tmp_path):
        pytest.importorskip("nrrd")
        vol = self._ct_volume()
        path = tmp_path / "ct.nrrd"
        io.write(vol, path)
        np.testing.assert_allclose(io.read(path).data, vol.data)


class TestFormatDispatch:
    def test_detects_by_suffix(self, tmp_path):
        vol = _anisotropic_volume()
        for suffix in (".zarr", ".nrrd"):
            path = tmp_path / f"v{suffix}"
            io.write(vol, path)
            assert path.exists()

    def test_refuses_to_overwrite_by_default(self, tmp_path):
        vol = _anisotropic_volume()
        path = tmp_path / "v.zarr"
        io.write(vol, path)
        with pytest.raises(FileExistsError):
            io.write(vol, path)

    def test_overwrite_when_asked(self, tmp_path):
        vol = _anisotropic_volume()
        path = tmp_path / "v.zarr"
        io.write(vol, path)
        io.write(vol, path, overwrite=True)
        assert io.read(path).raw.shape == vol.raw.shape

    def test_unknown_suffix_raises(self, tmp_path):
        vol = _anisotropic_volume()
        with pytest.raises(Exception):
            io.write(vol, tmp_path / "v.bogus")
