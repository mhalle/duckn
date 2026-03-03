"""Tests for ZipStore (.zarr.zip) support."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import zarr

from nrrdz.convert import nrrd_to_zarr, zarr_to_nrrd
from nrrdz.models import NrrdMetadata
from nrrdz.zarr_io import get_zarr_attrs, open_store, read_nrrdz, read_nrrdz_metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_simple_nrrd(path: Path) -> np.ndarray:
    """Write a minimal 3D NRRD file and return the data array."""
    nrrd = pytest.importorskip("nrrd")
    data = np.arange(24, dtype=np.int16).reshape(2, 3, 4)
    header = {
        "space": "right-anterior-superior",
        "space directions": np.array([[1, 0, 0], [0, 2, 0], [0, 0, 3]], dtype=float),
        "space origin": np.array([10.0, 20.0, 30.0]),
        "kinds": ["domain", "domain", "domain"],
        "encoding": "gzip",
    }
    nrrd.write(str(path), data, header, index_order="C")
    return data


# ---------------------------------------------------------------------------
# NRRD -> .zarr.zip -> read back
# ---------------------------------------------------------------------------


class TestNrrdZipRoundTrip:
    def test_nrrd_to_zip_and_read(self, tmp_path):
        """NRRD -> .zarr.zip, then read back with read_nrrdz()."""
        nrrd_path = tmp_path / "test.nrrd"
        data = _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)

        assert zip_path.exists()
        assert zip_path.is_file()

        # Read back
        out_data, meta = read_nrrdz(zip_path)
        np.testing.assert_array_equal(out_data, data)
        assert meta.space == "right-anterior-superior"
        np.testing.assert_allclose(meta.space_origin, [10.0, 20.0, 30.0])

    def test_read_nrrdz_metadata_from_zip(self, tmp_path):
        """read_nrrdz_metadata works with .zarr.zip."""
        nrrd_path = tmp_path / "test.nrrd"
        _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)

        meta = read_nrrdz_metadata(zip_path)
        assert meta.space == "right-anterior-superior"
        assert meta.axes is not None
        assert len(meta.axes) == 3

    def test_get_zarr_attrs_from_zip(self, tmp_path):
        """get_zarr_attrs works with .zarr.zip."""
        nrrd_path = tmp_path / "test.nrrd"
        _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)

        attrs = get_zarr_attrs(zip_path)
        assert "nrrd" in attrs

    def test_zip_to_nrrd_round_trip(self, tmp_path):
        """NRRD -> .zarr.zip -> NRRD round-trip preserves data."""
        nrrd = pytest.importorskip("nrrd")

        nrrd_path = tmp_path / "input.nrrd"
        data = _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)

        output_nrrd = tmp_path / "output.nrrd"
        zarr_to_nrrd(zip_path, output_nrrd)

        out_data, _ = nrrd.read(str(output_nrrd), index_order="C")
        np.testing.assert_array_equal(out_data, data)


# ---------------------------------------------------------------------------
# Overwrite behaviour
# ---------------------------------------------------------------------------


class TestZipOverwrite:
    def test_zip_overwrite_protection(self, tmp_path):
        """Writing to existing .zarr.zip without overwrite raises."""
        nrrd_path = tmp_path / "test.nrrd"
        _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)

        with pytest.raises(FileExistsError):
            nrrd_to_zarr(nrrd_path, zip_path, overwrite=False)

    def test_zip_overwrite_succeeds(self, tmp_path):
        """Writing to existing .zarr.zip with overwrite=True works."""
        nrrd_path = tmp_path / "test.nrrd"
        data = _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)
        nrrd_to_zarr(nrrd_path, zip_path, overwrite=True)

        out_data, _ = read_nrrdz(zip_path)
        np.testing.assert_array_equal(out_data, data)


# ---------------------------------------------------------------------------
# NIfTI -> .zarr.zip round-trip
# ---------------------------------------------------------------------------


class TestNiftiZipRoundTrip:
    def test_nifti_zip_round_trip(self, tmp_path):
        """NIfTI -> .zarr.zip -> NIfTI round-trip."""
        nibabel = pytest.importorskip("nibabel")
        import nibabel as nib

        from nrrdz.nifti_convert import nifti_to_zarr, zarr_to_nifti

        # Create a synthetic NIfTI
        data = np.arange(64, dtype=np.int16).reshape(4, 4, 4)
        affine = np.diag([2.0, 2.0, 2.0, 1.0])
        img = nib.Nifti1Image(data, affine)
        hdr = img.header
        hdr.set_sform(affine, code=1)

        nii_path = tmp_path / "test.nii"
        nib.save(img, str(nii_path))

        # NIfTI -> zip
        zip_path = tmp_path / "test.zarr.zip"
        nifti_to_zarr(nii_path, zip_path)
        assert zip_path.exists()

        # Read back metadata
        meta = read_nrrdz_metadata(zip_path)
        assert meta.space is not None

        # zip -> NIfTI
        nii_out = tmp_path / "output.nii"
        zarr_to_nifti(zip_path, nii_out)

        out_img = nib.load(str(nii_out))
        np.testing.assert_array_equal(out_img.dataobj.get_unscaled(), data)


# ---------------------------------------------------------------------------
# open_store context manager
# ---------------------------------------------------------------------------


class TestOpenStore:
    def test_local_store_for_directory(self, tmp_path):
        """Non-.zarr.zip path yields a LocalStore."""
        path = tmp_path / "test.zarr"
        with open_store(path, mode="w") as store:
            assert isinstance(store, zarr.storage.LocalStore)

    def test_zip_store_for_zip_path(self, tmp_path):
        """Path ending in .zarr.zip yields a ZipStore."""
        path = tmp_path / "test.zarr.zip"
        with open_store(path, mode="w") as store:
            assert isinstance(store, zarr.storage.ZipStore)

    def test_zip_store_closed_after_context(self, tmp_path):
        """ZipStore is closed after exiting context manager."""
        nrrd_path = tmp_path / "test.nrrd"
        _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)

        # If the store was properly closed, we can open and read it
        out_data, meta = read_nrrdz(zip_path)
        assert out_data.shape == (2, 3, 4)


# ---------------------------------------------------------------------------
# CLI info command
# ---------------------------------------------------------------------------


class TestInfoOnZip:
    def test_info_command_on_zip(self, tmp_path):
        """nrrdz info works on .zarr.zip files."""
        from click.testing import CliRunner

        from nrrdz.cli import cli

        nrrd_path = tmp_path / "test.nrrd"
        _write_simple_nrrd(nrrd_path)

        zip_path = tmp_path / "test.zarr.zip"
        nrrd_to_zarr(nrrd_path, zip_path)

        runner = CliRunner()
        result = runner.invoke(cli, ["info", str(zip_path)])
        assert result.exit_code == 0
        assert '"nrrd"' in result.output
