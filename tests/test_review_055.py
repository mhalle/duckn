"""Defects a review of 0.5.4 reproduced (2026-09-26), one class per defect.

Each test fails on v0.5.4 and passes after the fix. Expected values are computed from the
formats' own definitions (NRRD's column vectors, NIfTI's codes), never from a duckn round trip,
which hides a defect that is its own inverse.
"""

from __future__ import annotations

import warnings

import nrrd
import numpy as np
import pytest
import zarr

from duckn.convert import (
    nrrd_to_zarr, nrrd_to_zarr_zerocopy, zarr_to_nrrd, zarr_to_nrrd_zerocopy,
)
from duckn.models import DucknMetadata
from duckn.zarr_io import open_store


def _meta(path) -> DucknMetadata:
    with open_store(path, mode="r") as s:
        return DucknMetadata(**zarr.open_array(s, mode="r").attrs["duckn"])


def _data(path) -> np.ndarray:
    with open_store(path, mode="r") as s:
        return np.asarray(zarr.open_array(s, mode="r")[:])


def _write_nrrd_text(path, header_lines: list[str], data: np.ndarray) -> None:
    """A NRRD written by hand, so that nothing of pynrrd's parsing is in the expectation."""
    nrrd_type = {"float32": "float", "float64": "double"}.get(data.dtype.name, data.dtype.name)
    head = ["NRRD0005", f"type: {nrrd_type}", f"dimension: {data.ndim}",
            "sizes: " + " ".join(str(n) for n in reversed(data.shape)),
            "encoding: raw", "endian: little", *header_lines]
    path.write_bytes(("\n".join(head) + "\n\n").encode() + data.astype(data.dtype.newbyteorder("<")).tobytes())


# ---------------------------------------------------------------------------
# 1. NRRD's measurement frame is column vectors; duckn's is rows
# ---------------------------------------------------------------------------

# An ASYMMETRIC frame: a symmetric one is its own transpose and proves nothing.
_MF_VECTORS = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (7.0, 8.0, 10.0)]
_MF_TEXT = "measurement frame: " + " ".join("(" + ",".join(f"{x:g}" for x in v) + ")"
                                            for v in _MF_VECTORS)


def _mf_nrrd(tmp_path):
    p = tmp_path / "mf.nrrd"
    _write_nrrd_text(p, [
        "space: left-posterior-superior",
        "space directions: none (1,0,0) (0,1,0) (0,0,1)",
        "kinds: vector space space space",
        _MF_TEXT,
    ], np.zeros((2, 2, 2, 3), np.float32))
    return p


def _frame_from_nrrd_definition() -> np.ndarray:
    # teem's format: "the vectors are the columns of the matrix which transforms coordinates in
    # the measurement frame to world space". So measurement axis j maps to world vector j.
    return np.column_stack([np.array(v) for v in _MF_VECTORS])


class TestMeasurementFrame:
    @pytest.mark.parametrize("convert", [nrrd_to_zarr, nrrd_to_zarr_zerocopy])
    def test_import_stores_the_frame_by_rows(self, tmp_path, convert):
        z = tmp_path / "mf.zarr"
        convert(_mf_nrrd(tmp_path), z)
        mf = np.array(_meta(z).measurement_frame)
        np.testing.assert_array_equal(mf, _frame_from_nrrd_definition())
        # world = measurement_frame @ measurement_coords (duckn-spec §3.1): the first
        # measurement axis lands on the file's first vector
        np.testing.assert_array_equal(mf @ [1.0, 0.0, 0.0], _MF_VECTORS[0])

    @pytest.mark.parametrize("zip_", [False, True])
    def test_export_writes_the_columns_as_nrrd_vectors(self, tmp_path, zip_):
        z = tmp_path / ("mf.zarr.zip" if zip_ else "mf.zarr")
        m = DucknMetadata(
            version="1.0", space="LPS", measurement_frame=_frame_from_nrrd_definition().tolist(),
            axes=[{"kind": "3-vector"}] + [{"kind": "space", "space_direction": list(r)}
                                           for r in np.eye(3)])
        from duckn.models import duckn_attrs
        with open_store(z, mode="w") as s:
            zarr.create_array(s, data=np.zeros((3, 2, 2, 2), np.float32), attributes=duckn_attrs(m))
        out = tmp_path / "out.nrrd"
        zarr_to_nrrd(z, out)
        h = nrrd.read_header(str(out))
        # pynrrd returns the header's vectors as rows, in file order
        np.testing.assert_array_equal(np.array(h["measurement frame"]), np.array(_MF_VECTORS))

    def test_zero_copy_export_writes_the_file_order_back(self, tmp_path):
        z = tmp_path / "mf.zarr"
        nrrd_to_zarr_zerocopy(_mf_nrrd(tmp_path), z)
        out = tmp_path / "out.nrrd"
        zarr_to_nrrd_zerocopy(z, out)
        assert _MF_TEXT in out.read_text(errors="replace")


# ---------------------------------------------------------------------------
# 2. byte-copy converters into a .zarr.zip; a failed conversion leaves nothing
# ---------------------------------------------------------------------------


class TestZeroCopyIntoZip:
    @pytest.mark.parametrize("encoding", ["raw", "gzip"])
    def test_zero_copy_writes_a_zip_that_holds_the_voxels(self, tmp_path, encoding):
        data = np.arange(4 * 5 * 6, dtype=np.int16).reshape(6, 5, 4)
        p = tmp_path / "a.nrrd"
        nrrd.write(str(p), data, {"encoding": encoding, "space": "LPS",
                                  "space directions": np.eye(3)}, index_order="C")
        z = tmp_path / "a.zarr.zip"
        nrrd_to_zarr_zerocopy(p, z)
        np.testing.assert_array_equal(_data(z), data)
        out = tmp_path / "rt.nrrd"
        zarr_to_nrrd_zerocopy(z, out)
        np.testing.assert_array_equal(nrrd.read(str(out), index_order="C")[0], data)

    def test_the_cli_zero_copy_into_a_zip(self, tmp_path):
        from click.testing import CliRunner
        from duckn.cli import cli
        data = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
        p = tmp_path / "a.nrrd"
        nrrd.write(str(p), data, {"encoding": "gzip"}, index_order="C")
        z = tmp_path / "a.zarr.zip"
        r = CliRunner().invoke(cli, ["from-nrrd", str(p), str(z), "--zerocopy"])
        assert r.exit_code == 0, r.output
        np.testing.assert_array_equal(_data(z), data)

    def test_a_failed_zero_copy_leaves_no_store(self, tmp_path, monkeypatch):
        data = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
        p = tmp_path / "a.nrrd"
        nrrd.write(str(p), data, {"encoding": "raw"}, index_order="C")
        import duckn.convert as conv

        def boom(*a, **k):
            raise OSError("disk full")
        # raising=False: 0.5.4 has no set_raw, and fails on its own (bytes into a ZipStore)
        monkeypatch.setattr(conv, "set_raw", boom, raising=False)
        z = tmp_path / "a.zarr.zip"
        with pytest.raises(Exception):
            nrrd_to_zarr_zerocopy(p, z)
        assert not z.exists()
        assert [f.name for f in tmp_path.iterdir()] == ["a.nrrd"]

    def test_a_failed_overwrite_keeps_the_old_store(self, tmp_path, monkeypatch):
        data = np.arange(24, dtype=np.uint8).reshape(2, 3, 4)
        p = tmp_path / "a.nrrd"
        nrrd.write(str(p), data, {"encoding": "raw"}, index_order="C")
        z = tmp_path / "a.zarr.zip"
        nrrd_to_zarr_zerocopy(p, z)
        import duckn.convert as conv
        monkeypatch.setattr(conv, "set_raw", lambda *a, **k: (_ for _ in ()).throw(OSError()),
                            raising=False)
        with pytest.raises(Exception):
            nrrd_to_zarr_zerocopy(p, z, overwrite=True)
        np.testing.assert_array_equal(_data(z), data)


pydicom = pytest.importorskip("pydicom")


def _dicom_series(tmp_path, n=3):
    from test_dicom_convert import _make_dataset, _make_file_dataset
    d = tmp_path / "dicom"
    d.mkdir()
    for i in range(n):
        ds = _make_dataset(rows=8, cols=8, position=(0.0, 0.0, float(i * 5)),
                           pixel_spacing=(0.5, 0.5),
                           pixel_data=np.full((8, 8), i * 100 + 7, dtype=np.uint16))
        fds = _make_file_dataset(ds, str(d / f"s{i}.dcm"))
        fds.save_as(str(d / f"s{i}.dcm"))
    return d


class TestStreamingDicomIntoZip:
    @pytest.mark.parametrize("compressor", ["none", "zstd"])
    def test_streaming_into_a_zip_holds_every_slice(self, tmp_path, compressor):
        from duckn.dicom_convert import dicom_to_zarr_streaming
        d = _dicom_series(tmp_path)
        z = tmp_path / "s.zarr.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dicom_to_zarr_streaming(d, z, compressor=compressor)
        got = _data(z)
        assert [int(got[k, 0, 0]) for k in range(3)] == [7, 107, 207]

    def test_a_failed_stream_leaves_no_store(self, tmp_path, monkeypatch):
        from duckn import dicom_convert
        d = _dicom_series(tmp_path)
        z = tmp_path / "s.zarr.zip"
        calls = []

        def fail_on_second(path):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("unreadable slice")
            return real(path)
        real = dicom_convert.get_pixel_data_range
        monkeypatch.setattr(dicom_convert, "get_pixel_data_range", fail_on_second)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with pytest.raises(OSError):
                dicom_convert.dicom_to_zarr_streaming(d, z)
        assert not z.exists()
        assert sorted(f.name for f in tmp_path.iterdir()) == ["dicom"]


# ---------------------------------------------------------------------------
# 3. NRRD fields the convention does not model are kept, and per-axis units exported
# ---------------------------------------------------------------------------


def _roundtrip_header(tmp_path, header, data=None, zerocopy=False):
    data = np.arange(4 * 5 * 6, dtype=np.uint8).reshape(6, 5, 4) if data is None else data
    p = tmp_path / "in.nrrd"
    nrrd.write(str(p), data, {"encoding": "gzip", **header}, index_order="C")
    z = tmp_path / "x.zarr"
    out = tmp_path / "out.nrrd"
    if zerocopy:
        nrrd_to_zarr_zerocopy(p, z)
        zarr_to_nrrd_zerocopy(z, out)
    else:
        nrrd_to_zarr(p, z)
        zarr_to_nrrd(z, out)
    return _meta(z), nrrd.read_header(str(out))


class TestNrrdFieldsKept:
    @pytest.mark.parametrize("zerocopy", [False, True])
    def test_spacings_and_axis_mins_of_a_file_with_no_space(self, tmp_path, zerocopy):
        m, h = _roundtrip_header(tmp_path, {"spacings": [0.5, 1.0, 2.5],
                                            "axis mins": [0.0, 10.0, 20.0],
                                            "axis maxs": [2.0, 15.0, 35.0]}, zerocopy=zerocopy)
        # array order is slowest first: the NRRD's last axis is duckn's first
        assert m.axes[0].extensions["nrrd"] == {"spacing": 2.5, "axis_min": 20.0,
                                                "axis_max": 35.0}
        assert m.extensions["nrrd"]["version"]
        np.testing.assert_array_equal(h["spacings"], [0.5, 1.0, 2.5])
        np.testing.assert_array_equal(h["axis mins"], [0.0, 10.0, 20.0])
        np.testing.assert_array_equal(h["axis maxs"], [2.0, 15.0, 35.0])

    @pytest.mark.parametrize("zerocopy", [False, True])
    def test_old_min_max_content_min_max(self, tmp_path, zerocopy):
        m, h = _roundtrip_header(tmp_path, {"old min": -100.0, "old max": 100.5,
                                            "content": "hello there", "min": 0.0, "max": 119.0},
                                 zerocopy=zerocopy)
        assert m.extensions["nrrd"] == {"version": m.extensions["nrrd"]["version"],
                                        "content": "hello there", "min": 0.0, "max": 119.0,
                                        "old_min": -100.0, "old_max": 100.5}
        assert not m.value_transforms  # no value mapping is invented from old min/max
        assert (h["old min"], h["old max"], h["content"], h["min"], h["max"]) == \
            (-100.0, 100.5, "hello there", 0.0, 119.0)

    @pytest.mark.parametrize("zerocopy", [False, True])
    def test_per_axis_units_are_exported(self, tmp_path, zerocopy):
        _, h = _roundtrip_header(tmp_path, {"units": ["s", "m", "mm"], "spacings": [1, 1, 1]},
                                 zerocopy=zerocopy)
        assert list(h["units"]) == ["s", "m", "mm"]

    def test_a_time_axis_unit_beside_spatial_axes(self, tmp_path):
        data = np.zeros((2, 3, 4, 5), np.uint8)
        _, h = _roundtrip_header(tmp_path, {
            "space": "left-posterior-superior",
            "space directions": [[1, 0, 0], [0, 1, 0], [0, 0, 1], [np.nan] * 3],
            "kinds": ["space", "space", "space", "time"],
            "space units": ["mm", "mm", "mm"], "units": ["", "", "", "s"]}, data=data)
        assert list(h["units"]) == ["", "", "", "s"]
        assert list(h["space units"]) == ["mm", "mm", "mm"]

    def test_a_field_with_no_place_is_reported(self, tmp_path):
        from duckn.convert import _header_to_metadata
        found = []
        _header_to_metadata({"type": "uint8", "dimension": 1, "sizes": [2], "number": "2"}, 1,
                            diagnostics=found)
        assert [d.code for d in found] == ["nrrd-field-dropped"]


# ---------------------------------------------------------------------------
# 7. the zero-copy writer's `legacy` extension: no version, no owner
# ---------------------------------------------------------------------------


class TestZeroCopyExtension:
    def test_every_extension_it_writes_has_a_version(self, tmp_path):
        data = np.arange(24, dtype=np.int16).reshape(2, 3, 4)
        p = tmp_path / "a.nrrd"
        nrrd.write(str(p), data, {"encoding": "gzip", "content": "x"}, index_order="C")
        z = tmp_path / "a.zarr"
        nrrd_to_zarr_zerocopy(p, z)
        exts = _meta(z).extensions
        assert "legacy" not in exts
        assert all("version" in e for e in exts.values())

    def test_zero_copy_export_refuses_what_it_cannot_copy(self, tmp_path):
        z = tmp_path / "multi.zarr"
        with open_store(z, mode="w") as s:
            zarr.create_array(s, data=np.arange(64, dtype=np.uint8).reshape(4, 4, 4),
                              chunks=(2, 4, 4), attributes={"duckn": {"version": "1.0"}})
        with pytest.raises(ValueError, match="single chunk"):
            zarr_to_nrrd_zerocopy(z, tmp_path / "x.nrrd")
        z2 = tmp_path / "zstd.zarr"
        with open_store(z2, mode="w") as s:
            zarr.create_array(s, data=np.arange(64, dtype=np.uint8).reshape(4, 4, 4),
                              chunks=(4, 4, 4), attributes={"duckn": {"version": "1.0"}})
        with pytest.raises(ValueError, match="raw or gzip"):
            zarr_to_nrrd_zerocopy(z2, tmp_path / "y.nrrd")

    def test_zero_copy_export_refuses_value_transforms(self, tmp_path):
        z = tmp_path / "ct.zarr"
        with open_store(z, mode="w") as s:
            zarr.create_array(s, data=np.zeros((2, 2, 2), np.int16), chunks=(2, 2, 2),
                              compressors=None, attributes={"duckn": {
                                  "version": "1.0", "value_transforms": [
                                      {"name": "linear",
                                       "parameters": {"slope": 1, "intercept": -1024}}]}})
        with pytest.raises(ValueError, match="value_transforms"):
            zarr_to_nrrd_zerocopy(z, tmp_path / "x.nrrd")
