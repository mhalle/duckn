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
            version="1.1", space="LPS", measurement_frame=_frame_from_nrrd_definition().tolist(),
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

    @pytest.mark.parametrize("convert", [nrrd_to_zarr, nrrd_to_zarr_zerocopy])
    def test_import_declares_the_version_whose_frame_rule_it_follows(self, tmp_path, convert):
        # Rows are 1.1's form; 1.0 wrote columns. 0.5.5 to 0.6.2 declared 1.0 over rows.
        z = tmp_path / "mf.zarr"
        convert(_mf_nrrd(tmp_path), z)
        assert _meta(z).version in ("1.1", "1.2")

    def test_a_1_0_frame_is_read_by_columns(self, tmp_path):
        # A genuine 1.0 file stores NRRD's columns; export writes them back as the vectors.
        z = tmp_path / "mf.zarr"
        m = DucknMetadata(
            version="1.0", space="LPS", measurement_frame=_frame_from_nrrd_definition().T.tolist(),
            axes=[{"kind": "3-vector"}] + [{"kind": "space", "space_direction": list(r)}
                                           for r in np.eye(3)])
        assert m.measurement_frame_rows() == _frame_from_nrrd_definition().tolist()
        from duckn.models import duckn_attrs
        with open_store(z, mode="w") as s:
            zarr.create_array(s, data=np.zeros((3, 2, 2, 2), np.float32), attributes=duckn_attrs(m))
        out = tmp_path / "out.nrrd"
        zarr_to_nrrd(z, out)
        np.testing.assert_array_equal(np.array(nrrd.read_header(str(out))["measurement frame"]),
                                      np.array(_MF_VECTORS))

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


# ---------------------------------------------------------------------------
# 4, 8. NIfTI: codes 0, unknown units, a time unit on a 3D file, pixdim[4]
# ---------------------------------------------------------------------------

nib = pytest.importorskip("nibabel")


def _nifti(tmp_path, data, *, sform_code, qform_code, xyzt_units, pixdim4=None, name="a.nii"):
    """A NIfTI-1 file whose codes and units are set in its bytes (NIfTI-1 header offsets), so
    that nibabel's own save-time choices are not in the expectation."""
    import struct
    aff = np.diag([2.0, 3.0, 4.0, 1.0])
    aff[:3, 3] = [10.0, 20.0, 30.0]
    img = nib.Nifti1Image(data, aff)
    if pixdim4 is not None:
        img.header["pixdim"][4] = pixdim4
    p = tmp_path / name
    nib.save(img, p)
    b = bytearray(p.read_bytes())
    b[123] = xyzt_units
    b[252:254] = struct.pack("<h", qform_code)
    b[254:256] = struct.pack("<h", sform_code)
    p.write_bytes(bytes(b))
    return p


def _nifti_roundtrip(tmp_path, p):
    from duckn.nifti_convert import nifti_to_zarr, zarr_to_nifti
    z = tmp_path / "a.zarr"
    out = tmp_path / "rt.nii"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        nifti_to_zarr(p, z)
        zarr_to_nifti(z, out)
    with open(out, "rb") as fh:
        return _meta(z), nib.Nifti1Header.from_fileobj(fh)


class TestNiftiCodes:
    def test_no_codes_states_no_patient_space(self, tmp_path):
        p = _nifti(tmp_path, np.zeros((4, 5, 6), np.int16), sform_code=0, qform_code=0,
                   xyzt_units=2)
        m, h = _nifti_roundtrip(tmp_path, p)
        # NIfTI method 1: x = pixdim[1] * i, ...: no orientation, no origin, no named space
        assert m.space is None and m.space_dimension == 3
        assert m.space_origin == [0.0, 0.0, 0.0]
        assert [ax.space_direction for ax in m.axes] == [[2, 0, 0], [0, 3, 0], [0, 0, 4]]
        assert (m.extensions["nifti"]["tags"]["sform_code"],
                m.extensions["nifti"]["tags"]["qform_code"]) == (0, 0)
        assert (int(h["sform_code"]), int(h["qform_code"])) == (0, 0)
        np.testing.assert_allclose(h["pixdim"][1:4], [2, 3, 4])

    def test_a_qform_only_file_gains_no_sform(self, tmp_path):
        p = _nifti(tmp_path, np.zeros((4, 5, 6), np.int16), sform_code=0, qform_code=1,
                   xyzt_units=2)
        _, h = _nifti_roundtrip(tmp_path, p)
        assert (int(h["sform_code"]), int(h["qform_code"])) == (0, 1)

    def test_an_sform_only_file_gains_no_qform(self, tmp_path):
        p = _nifti(tmp_path, np.zeros((4, 5, 6), np.int16), sform_code=1, qform_code=0,
                   xyzt_units=2)
        _, h = _nifti_roundtrip(tmp_path, p)
        assert (int(h["sform_code"]), int(h["qform_code"])) == (1, 0)


class TestNiftiUnits:
    def test_unknown_units_are_not_millimeters(self, tmp_path):
        p = _nifti(tmp_path, np.zeros((4, 5, 6), np.int16), sform_code=2, qform_code=0,
                   xyzt_units=0)
        m, h = _nifti_roundtrip(tmp_path, p)
        assert [ax.unit for ax in m.axes] == [None, None, None]
        assert int(h["xyzt_units"]) == 0

    def test_a_3d_file_keeps_its_time_unit(self, tmp_path):
        p = _nifti(tmp_path, np.zeros((4, 5, 6), np.int16), sform_code=2, qform_code=0,
                   xyzt_units=2 | 8)
        _, h = _nifti_roundtrip(tmp_path, p)
        assert int(h["xyzt_units"]) == 2 | 8

    def test_hertz_on_a_time_axis(self, tmp_path):
        p = _nifti(tmp_path, np.zeros((4, 5, 6, 2), np.int16), sform_code=2, qform_code=0,
                   xyzt_units=2 | 32)
        m, h = _nifti_roundtrip(tmp_path, p)
        assert m.axes[3].unit == "Hz"
        assert int(h["xyzt_units"]) == 2 | 32


class TestNiftiTimeInterval:
    def test_pixdim4_is_the_interval_not_a_thickness(self, tmp_path):
        p = _nifti(tmp_path, np.zeros((4, 5, 6, 3), np.int16), sform_code=2, qform_code=0,
                   xyzt_units=2 | 16, pixdim4=2000.0)
        m, h = _nifti_roundtrip(tmp_path, p)
        t = m.axes[3]
        assert t.thickness is None  # §3.2: thickness is the extent measured
        assert [sm.position for sm in t.samples] == [0.0, 2000.0, 4000.0]
        assert float(h["pixdim"][4]) == 2000.0


# ---------------------------------------------------------------------------
# 5. adapters: a derived array keeps no source-format extension
# ---------------------------------------------------------------------------


def _ct_meta() -> DucknMetadata:
    return DucknMetadata(
        version="1.0", space="LPS", space_origin=[0.0, 0.0, 0.0],
        value_transforms=[{"name": "linear", "parameters": {"slope": 1.0, "intercept": -1024.0}}],
        sample_units="HU",
        axes=[{"kind": "space", "space_direction": [0, 0, 2.0], "unit": "mm"},
              {"kind": "space", "space_direction": [0, 1.0, 0], "unit": "mm"},
              {"kind": "space", "space_direction": [1.0, 0, 0], "unit": "mm"}],
        extensions={"dicom": {"version": "1.0", "stored_values": True,
                              "tags": {"BitsAllocated": 16, "BitsStored": 12,
                                       "PixelRepresentation": 0, "Modality": "CT"}},
                    "keyvalues": {"version": "1.0", "entries": {"a": "b"}},
                    "custom": {"version": "0.1", "note": "kept"}})


class TestAdaptersDropSourceExtensions:
    def test_from_sitk_of_a_shrunk_float_image(self):
        sitk = pytest.importorskip("SimpleITK")
        from duckn.sitk_adapter import from_sitk, to_sitk
        from duckn.volume import Volume
        vol = Volume(raw=np.zeros((4, 6, 6), np.uint16), metadata=_ct_meta())
        shrunk = sitk.Shrink(sitk.Cast(to_sitk(vol), sitk.sitkFloat32), [2, 2, 2])
        out = from_sitk(shrunk, metadata=vol.metadata)
        assert "dicom" not in (out.metadata.extensions or {})
        assert "keyvalues" not in (out.metadata.extensions or {})
        assert out.metadata.extensions["custom"] == {"version": "0.1", "note": "kept"}

    def test_from_nifti_of_calibrated_values(self):
        nib = pytest.importorskip("nibabel")
        from duckn.nibabel_adapter import from_nifti, to_nifti
        from duckn.volume import Volume
        vol = Volume(raw=np.zeros((4, 6, 6), np.uint16), metadata=_ct_meta())
        out = from_nifti(to_nifti(vol), metadata=vol.metadata)
        # the image holds vol.data, calibrated: stored_values: true would be false of it
        assert "dicom" not in (out.metadata.extensions or {})

    def test_an_unchanged_image_may_keep_them(self):
        pytest.importorskip("SimpleITK")
        from duckn.sitk_adapter import from_sitk, to_sitk
        from duckn.volume import Volume
        m = _ct_meta()
        m.value_transforms = None
        vol = Volume(raw=np.zeros((4, 6, 6), np.uint16), metadata=m)
        out = from_sitk(to_sitk(vol), metadata=vol.metadata)
        assert out.metadata.extensions["dicom"]["stored_values"] is True
        forced = from_sitk(to_sitk(vol), metadata=vol.metadata, derived=True)
        assert "dicom" not in (forced.metadata.extensions or {})


# ---------------------------------------------------------------------------
# 6. adapters build an axis per dimension; io.write validates
# ---------------------------------------------------------------------------


class TestAdapterAxes:
    def test_a_4d_nifti_gets_four_axes(self):
        nib = pytest.importorskip("nibabel")
        from duckn.models import validate_against_shape
        from duckn.nibabel_adapter import from_nifti
        img = nib.Nifti1Image(np.zeros((4, 5, 6, 3), np.int16), np.diag([2.0, 3.0, 4.0, 1.0]))
        vol = from_nifti(img)
        assert vol.raw.shape == (3, 6, 5, 4)
        validate_against_shape(vol.metadata, vol.raw.shape)
        assert vol.metadata.axes[0].kind == "time"
        assert [ax.space_direction for ax in vol.metadata.axes[1:]] == \
            [[0, 0, 4], [0, 3, 0], [2, 0, 0]]

    def test_a_vector_sitk_image_gets_a_component_axis(self):
        sitk = pytest.importorskip("SimpleITK")
        from duckn.models import validate_against_shape
        from duckn.sitk_adapter import from_sitk
        img = sitk.Image([4, 5, 6], sitk.sitkVectorFloat32, 3)
        vol = from_sitk(img)
        assert vol.raw.shape == (6, 5, 4, 3)
        validate_against_shape(vol.metadata, vol.raw.shape)
        assert vol.metadata.axes[-1].kind == "list"

    def test_a_2d_sitk_image(self):
        sitk = pytest.importorskip("SimpleITK")
        from duckn.sitk_adapter import from_sitk
        img = sitk.Image([4, 5], sitk.sitkUInt8)
        img.SetSpacing([0.5, 2.0])
        vol = from_sitk(img)
        assert vol.metadata.space is None and vol.metadata.space_dimension == 2
        assert [ax.space_direction for ax in vol.metadata.axes] == [[0, 2.0], [0.5, 0]]

    def test_write_refuses_metadata_that_does_not_fit(self, tmp_path):
        from duckn.io import write
        from duckn.volume import Volume
        m = DucknMetadata(version="1.0", axes=[{}, {}, {}])
        with pytest.raises(ValueError, match="axes"):
            write(Volume(raw=np.zeros((2, 2, 2, 2), np.uint8), metadata=m), tmp_path / "x.zarr")
        assert not (tmp_path / "x.zarr").exists()


# ---------------------------------------------------------------------------
# 9. a lut over uint64 stored values
# ---------------------------------------------------------------------------


class TestLutUnsigned64:
    def test_a_large_uint64_clamps_to_the_last_entry(self):
        from duckn.zarr_io import _apply_lut
        data = np.array([0, 1, 2, 2**63, 2**64 - 1], dtype=np.uint64)
        got = _apply_lut(data, {"first_value": 0, "values": [10.0, 20.0, 30.0]},
                         np.dtype(np.float64))
        assert got.tolist() == [10.0, 20.0, 30.0, 30.0, 30.0]

    def test_a_table_above_int64(self):
        from duckn.zarr_io import _apply_lut
        data = np.array([2**63 + 4, 2**63 + 5, 2**63 + 9, 3], dtype=np.uint64)
        got = _apply_lut(data, {"first_value": 2**63 + 5, "values": [1.0, 2.0]},
                         np.dtype(np.float64))
        assert got.tolist() == [1.0, 1.0, 2.0, 1.0]

    def test_signed_and_scalar_still_clamp(self):
        from duckn.zarr_io import _apply_lut
        p = {"first_value": -1, "values": [5.0, 6.0, 7.0]}
        assert _apply_lut(np.array([-5, -1, 0, 1, 9], np.int16), p,
                          np.dtype(np.float32)).tolist() == [5, 5, 6, 7, 7]
        assert float(_apply_lut(np.int8(0), p, np.dtype(np.float32))) == 6.0
        assert _apply_lut(np.array([True, False]), {"values": [1.0, 2.0]},
                          np.dtype(np.float32)).tolist() == [2.0, 1.0]


# ---------------------------------------------------------------------------
# 11. the palette is stated in stored values (dicom-spec §5.10)
# ---------------------------------------------------------------------------


# LITERAL, as in test_dicom_tags_gaps: a list read from dt.STORED_ENCODING shrinks with it
@pytest.mark.parametrize("tag", [0x00281101, 0x00281102, 0x00281103, 0x00281104, 0x00281111,
                                 0x00281112, 0x00281113, 0x00281199, 0x00281201, 0x00281202,
                                 0x00281203, 0x00281204, 0x00281221, 0x00281222, 0x00281223,
                                 0x00281224])
def test_every_palette_attribute_follows_stored_values(tag):
    from pydicom.datadict import dictionary_VR
    from pydicom.dataset import Dataset
    import duckn.dicom_tags as dt
    ds = Dataset()
    ds.Modality = "CT"
    vr = dictionary_VR(tag).split(" or ")[0]
    value = {"US": [256, 0, 16], "UI": "1.2.3", "OW": b"\x00\x01"}[vr]
    ds.add_new(tag, vr, value)
    assert dt.dataset_tags(ds) == {"Modality": "CT"}
    k = f"{tag >> 16:04x}|{tag & 0xFFFF:04x}"
    assert dt.tags_from_sitk([{k: "256\\0\\16", "0008|0060": "CT"}])[0] == {"Modality": "CT"}
    if vr != "OW":   # binary values are left out unless asked for, whatever their units
        assert dt.keyword_of(tag) in dt.dataset_tags(ds, stored_values=True)


# ---------------------------------------------------------------------------
# 10, D29. an Enhanced object's rescale is a functional group
# ---------------------------------------------------------------------------


class TestEnhancedRescale:
    def _series(self, tmp_path, edit):
        from test_dicom_convert_gaps import _series
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return _series(tmp_path, edit)

    def test_a_uniform_rescale_is_the_shared_group(self, tmp_path):
        from duckn.dicom_convert import dicom_to_zarr, zarr_to_dicom

        def ct(i, ds):
            ds.Modality = "CT"
            ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = 1, -1024, "HU"
        z, m = self._series(tmp_path, ct)
        assert m.value_transforms
        out = tmp_path / "e.dcm"
        zarr_to_dicom(z, out)
        ds = pydicom.dcmread(out)
        assert ds.SOPClassUID == "1.2.840.10008.5.1.4.1.1.2.1"   # Enhanced CT
        assert "RescaleSlope" not in ds and "RescaleIntercept" not in ds
        pvt = ds.SharedFunctionalGroupsSequence[0].PixelValueTransformationSequence[0]
        assert (float(pvt.RescaleSlope), float(pvt.RescaleIntercept), pvt.RescaleType) == \
            (1.0, -1024.0, "HU")
        # and duckn reads it back from there
        back = tmp_path / "back.zarr"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            dicom_to_zarr(out, back)
        vt = _meta(back).value_transforms[0]
        assert (vt.parameters["slope"], vt.parameters["intercept"]) == (1.0, -1024.0)

    def test_a_varying_rescale_is_written_per_frame(self, tmp_path):
        from duckn.dicom_convert import zarr_to_dicom

        def vary(i, ds):
            ds.Modality = "CT"
            ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = [1, 2, 1][i], -1024, "HU"
        z, m = self._series(tmp_path, vary)
        assert [t.name for t in m.value_transforms] == ["axis_linear"]  # 0.6.3: stated, in 1.2
        out = tmp_path / "e.dcm"
        zarr_to_dicom(z, out)
        ds = pydicom.dcmread(out)
        pvts = [fg.PixelValueTransformationSequence[0] for fg in ds.PerFrameFunctionalGroupsSequence]
        assert [float(p.RescaleSlope) for p in pvts] == [1.0, 2.0, 1.0]
        assert [float(p.RescaleIntercept) for p in pvts] == [-1024.0] * 3
        assert {str(p.RescaleType) for p in pvts} == {"HU"}
        assert "PixelValueTransformationSequence" not in ds.SharedFunctionalGroupsSequence[0]


# ---------------------------------------------------------------------------
# 0.6.1: `space units` name one unit per WORLD axis, not per spatial array axis
# ---------------------------------------------------------------------------


class TestSpaceUnitsPerWorldAxis:
    def _convert(self, tmp_path, header_lines, data):
        p = tmp_path / "in.nrrd"
        _write_nrrd_text(p, header_lines, data)
        z = tmp_path / "x.zarr"
        out = tmp_path / "out.nrrd"
        nrrd_to_zarr(p, z)
        zarr_to_nrrd(z, out)
        return _meta(z), nrrd.read_header(str(out))

    def test_an_axis_takes_the_unit_of_the_world_axis_it_steps_along(self, tmp_path):
        # NRRD axis 0 steps along world axis 0 (mm), NRRD axis 1 along world axis 1 (um).
        # Array order is slowest first, so duckn's axis 0 is NRRD's axis 1.
        data = np.zeros((5, 4), dtype=np.uint8)
        m, h = self._convert(tmp_path, ["space dimension: 2",
                                        "space directions: (2,0) (0,3)",
                                        'space units: "mm" "um"'], data)
        assert [ax.unit for ax in m.axes] == ["um", "mm"]
        assert list(h["space units"]) == ["mm", "um"]

    def test_a_slice_in_3d_writes_a_unit_for_every_world_axis(self, tmp_path):
        data = np.zeros((6, 5), dtype=np.int16)
        m, h = self._convert(tmp_path, ["space: left-posterior-superior",
                                        "space directions: (0,1,0) (0,0,-1)",
                                        "space origin: (10,-120,90)",
                                        'space units: "mm" "mm" "mm"'], data)
        assert [ax.unit for ax in m.axes] == ["mm", "mm"]
        assert list(h["space units"]) == ["mm", "mm", "mm"]
