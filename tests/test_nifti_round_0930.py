"""NIfTI conversion, from the 2026-09-30 adversarial round: the mutation probes (each killed a
survivor of the NIfTI converter's suite; the code in a comment is the reviewer's) and a test per
fix of that round."""
import gzip
import json
import struct
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
import zarr

from duckn.nifti_convert import nifti_to_zarr, zarr_to_nifti


def _attrs(p):
    return dict(zarr.open_array(str(p), mode="r").attrs)["duckn"]


def _save(tmp, img, name="in.nii"):
    p = Path(tmp) / name
    nib.save(img, str(p))
    return p


def _patch_scl(p, slope, inter):
    with open(p, "r+b") as fh:
        fh.seek(112)
        fh.write(struct.pack("<ff", slope, inter))


def _img(shape=(4, 4, 3), dtype=np.int16, aff=None, s=1, q=0):
    aff = np.diag([2.0, 2, 3, 1]) if aff is None else aff
    img = nib.Nifti1Image(np.arange(np.prod(shape)).reshape(shape).astype(dtype), aff)
    img.header.set_sform(aff, code=s)
    if q:
        img.header.set_qform(aff, code=q)
    else:
        img.header.set_qform(None, code=0)
    return img


# N34: slope 1 with a non-zero intercept (CT stored with -1024 offset)
def test_slope_one_with_intercept_is_a_transform(tmp_path):
    p = _save(tmp_path, _img())
    _patch_scl(p, 1.0, -1024.0)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    vt = _attrs(tmp_path / "o.zarr")["value_transforms"]
    assert vt == [{"name": "linear", "parameters": {"slope": 1.0, "intercept": -1024.0}}]


# N08: msec / usec units on import
@pytest.mark.parametrize("unit,code", [("msec", 16), ("usec", 24)])
def test_time_units(tmp_path, unit, code):
    img = _img(shape=(4, 4, 3, 3))
    img.header.set_xyzt_units("mm", unit)
    img.header["pixdim"][4] = 5.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][3]["unit"] == {"msec": "ms", "usec": "us"}[unit]


# N09 / N11 / E20: spatial units m and um, both ways
@pytest.mark.parametrize("unit,expect,code", [("meter", "m", 1), ("micron", "um", 3)])
def test_spatial_units(tmp_path, unit, expect, code):
    img = _img()
    img.header.set_xyzt_units(unit, "unknown")
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][0]["unit"] == expect
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    assert int(nib.load(str(tmp_path / "rt.nii")).header["xyzt_units"]) & 0x07 == code


# N39 / N42 / E12 / E13 / E14 / E19: header tags round trip byte for byte
def test_tags_round_trip(tmp_path):
    img = _img(shape=(4, 4, 6, 2), dtype=np.float32)
    h = img.header
    h.set_xyzt_units("mm", "sec")
    h["pixdim"][4] = 2.0
    h.set_dim_info(freq=0, phase=1, slice=2)
    h["slice_code"], h["slice_start"], h["slice_end"], h["slice_duration"] = 3, 1, 4, 0.05
    h["toffset"] = 2.5
    h["intent_code"], h["intent_p1"], h["intent_p2"], h["intent_p3"] = 3, 7.0, 9.0, 11.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    r = nib.load(str(tmp_path / "rt.nii")).header
    for f in ("dim_info", "slice_code", "slice_start", "slice_end", "slice_duration", "toffset",
              "intent_code", "intent_p1", "intent_p2", "intent_p3"):
        assert float(r[f]) == pytest.approx(float(h[f])), f


# E16: NIfTI-2 .nii with a linear transform
def test_nifti2_scaling_export(tmp_path):
    aff = np.diag([2.0, 2, 3, 1])
    img = nib.Nifti2Image(np.arange(48, dtype=np.int16).reshape(4, 4, 3), aff)
    img.header.set_sform(aff, code=1)
    p = _save(tmp_path, img)
    with open(p, "r+b") as fh:
        fh.seek(176)
        fh.write(struct.pack("<dd", 0.5, -10.0))
    nifti_to_zarr(p, tmp_path / "o.zarr")
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    back = nib.load(str(tmp_path / "rt.nii"))
    np.testing.assert_allclose(back.get_fdata(), np.arange(48).reshape(4, 4, 3) * 0.5 - 10.0)


# E17: NIfTI-1 .nii.gz with a linear transform
def test_nii_gz_scaling_export(tmp_path):
    p = _save(tmp_path, _img())
    _patch_scl(p, 0.5, -10.0)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii.gz")
    back = nib.load(str(tmp_path / "rt.nii.gz"))
    np.testing.assert_allclose(back.get_fdata(), np.arange(48).reshape(4, 4, 3) * 0.5 - 10.0)


# N23 / E22: a tensor store with no nifti tags (as from NRRD) exports in nifti1.h order, intent 1005
def test_untagged_tensor_exports_as_symmatrix(tmp_path):
    comps = np.array([1, 2, 3, 4, 5, 6], np.float32)  # duckn order: xx xy xz yy yz zz
    data = np.broadcast_to(comps, (2, 2, 2, 1, 6)).copy()
    img = nib.Nifti1Image(data, np.eye(4)); img.header["intent_code"] = 1005
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    a = zarr.open_array(str(tmp_path / "o.zarr"), mode="r+")
    meta = dict(a.attrs)["duckn"]
    meta.pop("extensions", None)
    a.attrs["duckn"] = meta
    a[...] = data  # stored in duckn order
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    back = nib.load(str(tmp_path / "rt.nii"))
    assert int(back.header["intent_code"]) == 1005
    np.testing.assert_array_equal(np.asarray(back.dataobj)[0, 0, 0, 0], [1, 2, 4, 3, 5, 6])


# N30 / N31: convention intents
@pytest.mark.parametrize("code,intent", [(1002, "label-map"), (1006, "displacement-field")])
def test_convention_intent(tmp_path, code, intent):
    img = _img(); img.header["intent_code"] = code
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["intent"] == intent


# N24 / N25 / N27 / N28 / N29: component kinds
@pytest.mark.parametrize("code,size,kind", [
    (1005, 3, "2D-symmetric-matrix"), (1004, 4, "2D-matrix"), (1004, 9, "3D-matrix"),
    (1010, 4, "quaternion"), (2004, 4, "RGBA-color")])
def test_component_kinds(tmp_path, code, size, kind):
    data = np.zeros((2, 2, 2, 1, size), np.float32)
    img = nib.Nifti1Image(data, np.eye(4)); img.header["intent_code"] = code
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][4]["kind"] == kind


# N15: a 5D file with dim4 > 1 and components in dim5: the 4th stays time
def test_5d_vector_with_time(tmp_path):
    data = np.zeros((2, 2, 2, 3, 3), np.float32)
    img = nib.Nifti1Image(data, np.eye(4)); img.header["intent_code"] = 1007
    img.header.set_xyzt_units("mm", "sec"); img.header["pixdim"][4] = 2.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    ax = _attrs(tmp_path / "o.zarr")["axes"]
    assert ax[3]["kind"] == "time" and ax[4]["kind"] == "vector"


# N17: two volumes keep their TR
def test_two_volumes_keep_tr(tmp_path):
    img = _img(shape=(2, 2, 2, 2))
    img.header.set_xyzt_units("mm", "sec"); img.header["pixdim"][4] = 2.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert [s["position"] for s in _attrs(tmp_path / "o.zarr")["axes"][3]["samples"]] == [0.0, 2.0]


# E05 / E25: restore_transforms writes the legacy sform and qform verbatim
def test_restore_transforms_verbatim(tmp_path):
    s = np.diag([2.0, 2, 3, 1]); s[:3, 3] = [1, 2, 3]
    q = np.diag([2.0, 2, 3, 1]); q[:3, 3] = [-7, 0, 0]
    img = nib.Nifti1Image(np.zeros((4, 4, 3), np.int16), s)
    img.set_sform(s, code=4); img.set_qform(q, code=1)
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    # edit the core so that the reconstructed affine differs from the legacy sform
    a = zarr.open_array(str(tmp_path / "o.zarr"), mode="r+")
    meta = dict(a.attrs)["duckn"]; meta["space_origin"] = [100.0, 100.0, 100.0]; a.attrs["duckn"] = meta
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii", restore_transforms=True)
    h = nib.load(str(tmp_path / "rt.nii")).header
    np.testing.assert_allclose(h.get_sform(), s)
    np.testing.assert_allclose(h.get_qform(), q, atol=1e-5)


# E08: non-uniform time positions do not become a TR
def test_nonuniform_times_state_no_tr(tmp_path):
    img = _img(shape=(2, 2, 2, 3))
    img.header.set_xyzt_units("mm", "sec"); img.header["pixdim"][4] = 2.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    a = zarr.open_array(str(tmp_path / "o.zarr"), mode="r+")
    meta = dict(a.attrs)["duckn"]
    meta["axes"][3]["samples"] = [{"position": 0.0}, {"position": 2.0}, {"position": 5.0}]
    a.attrs["duckn"] = meta
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    assert float(nib.load(str(tmp_path / "rt.nii")).header["pixdim"][4]) != 2.0


# N04: a singular sform is passed over
def test_a_singular_sform_is_not_used(tmp_path):
    aff = np.diag([2.0, 2, 0, 1])
    img = nib.Nifti1Image(np.zeros((2, 2, 2), np.int16), None)
    img.header.set_sform(aff, code=1); img.header.set_qform(None, code=0)
    img.header["pixdim"][1:3] = 2.0
    img.header["pixdim"][3] = 4.0
    p = _save(tmp_path, img)
    with pytest.warns(UserWarning, match="singular"):
        nifti_to_zarr(p, tmp_path / "o.zarr")
    # a singular sform places nothing: method 1, pixdim along each axis, no space named
    d = _attrs(tmp_path / "o.zarr")
    assert d["axes"][2]["space_direction"] == [0.0, 0.0, 4.0]
    assert "space" not in d


# ---------------------------------------------------------------------------
# The fixes of the round
# ---------------------------------------------------------------------------

def _store(tmp, data, meta, name="s.zarr"):
    """A store from a DucknMetadata, as a non-NIfTI source would write it."""
    path = Path(tmp) / name
    zarr.create_array(zarr.storage.LocalStore(str(path)), data=data,
                      attributes={"duckn": meta.model_dump(exclude_none=True)}, fill_value=0)
    return path


def _two_forms(tmp):
    s = np.diag([2.0, 2, 3, 1]); s[:3, 3] = [1, 2, 3]
    q = np.diag([2.0, 2, 3, 1]); q[:3, 3] = [-7, 0, 0]
    img = nib.Nifti1Image(np.zeros((4, 4, 3), np.int16), s)
    img.set_sform(s, code=1); img.set_qform(q, code=1)
    return _save(tmp, img)


def test_the_affine_is_the_callers_choice(tmp_path):
    p = _two_forms(tmp_path)
    nifti_to_zarr(p, tmp_path / "s.zarr")
    nifti_to_zarr(p, tmp_path / "q.zarr", affine="qform")
    assert _attrs(tmp_path / "s.zarr")["space_origin"] == [1.0, 2.0, 3.0]
    assert _attrs(tmp_path / "q.zarr")["space_origin"] == [-7.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="sform.*qform"):
        nifti_to_zarr(p, tmp_path / "x.zarr", affine="best")


def test_qform_preferred_falls_back_to_the_sform(tmp_path):
    p = _save(tmp_path, _img(s=1, q=0))
    nifti_to_zarr(p, tmp_path / "o.zarr", affine="qform")
    assert _attrs(tmp_path / "o.zarr")["axes"][2]["space_direction"] == [0.0, 0.0, 3.0]


def test_an_sform_disagreeing_with_pixdim_is_used_as_written(tmp_path):
    img = _img()
    img.header["pixdim"][1:4] = [1.0, 1.0, 1.0]
    p = _save(tmp_path, img)
    with pytest.warns(UserWarning, match="disagree with pixdim"):
        nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][0]["space_direction"] == [2.0, 0.0, 0.0]


@pytest.mark.parametrize("shape", [(5, 4), (7,)])
def test_one_and_two_dimensional_files_convert(tmp_path, shape):
    img = nib.Nifti1Image(np.arange(np.prod(shape), dtype=np.int16).reshape(shape), np.eye(4))
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    a = zarr.open_array(str(tmp_path / "o.zarr"), mode="r")
    assert a.shape == shape and len(_attrs(tmp_path / "o.zarr")["axes"]) == len(shape)


def test_a_pair_is_read_from_its_header_given_the_image(tmp_path):
    img = nib.Nifti1Pair(np.arange(48, dtype=np.int16).reshape(4, 4, 3), np.diag([2.0, 2, 3, 1]))
    img.header.set_slope_inter(0.5, -10.0)
    nib.save(img, str(tmp_path / "pair.img"))
    nifti_to_zarr(tmp_path / "pair.img", tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["value_transforms"] == [
        {"name": "linear", "parameters": {"slope": 0.5, "intercept": -10.0}}]


def test_gzip_is_judged_by_its_bytes_not_its_name(tmp_path):
    p = _save(tmp_path, _img(), "up.NII.GZ")
    raw = gzip.decompress(p.read_bytes())
    raw = raw[:112] + struct.pack("<ff", 2.0, 1.0) + raw[120:]
    p.write_bytes(gzip.compress(raw))
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["value_transforms"][0]["parameters"]["slope"] == 2.0


def test_scaling_is_patched_into_a_pairs_header(tmp_path):
    p = _save(tmp_path, _img())
    _patch_scl(p, 0.5, -10.0)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.img")
    back = nib.load(str(tmp_path / "rt.img"))
    np.testing.assert_allclose(back.get_fdata(), np.arange(48).reshape(4, 4, 3) * 0.5 - 10.0)


def test_a_quaternion_past_unit_norm_is_normalized_as_nifti1_io_does(tmp_path):
    img = _img(s=0, q=1)
    p = _save(tmp_path, img)
    raw = bytearray(p.read_bytes())
    raw[256:268] = struct.pack("<fff", 0.0, 0.0, 1.2)       # quatern_b, c, d
    p.write_bytes(bytes(raw))
    nifti_to_zarr(p, tmp_path / "o.zarr")
    # b, c, d = (0, 0, 1): a rotation of 180 degrees about z
    d = _attrs(tmp_path / "o.zarr")["axes"]
    np.testing.assert_allclose(d[0]["space_direction"], [-2, 0, 0], atol=1e-6)
    np.testing.assert_allclose(d[1]["space_direction"], [0, -2, 0], atol=1e-6)


def test_an_analyze_header_is_refused_by_name(tmp_path):
    img = nib.AnalyzeImage(np.zeros((2, 2, 2), np.int16), np.eye(4))
    nib.save(img, str(tmp_path / "a.hdr"))
    with pytest.raises(ValueError, match="Analyze"):
        nifti_to_zarr(tmp_path / "a.hdr", tmp_path / "o.zarr")


def test_cifti_and_nifti_mrs_are_refused(tmp_path):
    img = _img(); img.header["intent_code"] = 3006
    p = _save(tmp_path, img, "c.nii")
    with pytest.raises(ValueError, match="CIFTI"):
        nifti_to_zarr(p, tmp_path / "c.zarr")
    img = _img()
    img.header.extensions.append(nib.nifti1.Nifti1Extension(44, b'{"SpectrometerFrequency":[123.2]}'))
    p = _save(tmp_path, img, "m.nii")
    with pytest.raises(ValueError, match="NIfTI-MRS"):
        nifti_to_zarr(p, tmp_path / "m.zarr")


def test_header_extensions_are_kept_whole(tmp_path):
    img = _img()
    img.header.extensions.append(nib.nifti1.Nifti1Extension(6, b"a comment\x00\x00\x00\x00\x00\x00\x00"))
    img.header.extensions.append(nib.nifti1.Nifti1Extension(40, b"\x01\x02\x03\xff" * 3))
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    nifti = _attrs(tmp_path / "o.zarr")["extensions"]["nifti"]
    assert nifti["version"] == "1.2"
    assert [e["code"] for e in nifti["tags"]["extensions"]] == [6, 40]
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    # as the source file holds them (nibabel pads each to a multiple of 16 bytes on write)
    src = nib.load(str(p)).header.extensions
    back = nib.load(str(tmp_path / "rt.nii")).header.extensions
    assert [(e.get_code(), e.content) for e in back] == [(e.get_code(), e.content) for e in src]


def test_rgb24_is_a_trailing_color_dimension_both_ways(tmp_path):
    rgb = np.zeros((3, 2, 2), dtype=[("R", "u1"), ("G", "u1"), ("B", "u1")])
    rgb["R"], rgb["G"], rgb["B"] = 10, 20, 30
    img = nib.Nifti1Image(rgb, np.eye(4))
    img.header["scl_slope"] = 2.0                  # nifti1.h: scaling does not apply to RGB
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    a = zarr.open_array(str(tmp_path / "o.zarr"), mode="r")
    d = _attrs(tmp_path / "o.zarr")
    assert a.shape == (3, 2, 2, 3) and a.dtype == np.uint8
    assert d["axes"][-1]["kind"] == "RGB-color" and not d.get("value_transforms")
    assert a[0, 0, 0].tolist() == [10, 20, 30]
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    back = nib.load(str(tmp_path / "rt.nii"))
    assert int(back.header["datatype"]) == 128
    assert np.asarray(back.dataobj)[0, 0, 0].tolist() == (10, 20, 30)


def test_toffset_places_the_first_time_point(tmp_path):
    img = _img(shape=(2, 2, 2, 3))
    img.header.set_xyzt_units("mm", "sec"); img.header["pixdim"][4] = 2.0
    img.header["toffset"] = 5.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert [s["position"] for s in _attrs(tmp_path / "o.zarr")["axes"][3]["samples"]] == [
        5.0, 7.0, 9.0]


def test_one_volume_with_a_toffset_has_its_time(tmp_path):
    img = _img(shape=(2, 2, 2, 1))
    img.header.set_xyzt_units("mm", "sec"); img.header["toffset"] = 5.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][3]["samples"] == [{"position": 5.0}]


def test_toffset_is_written_back_from_the_positions(tmp_path):
    img = _img(shape=(2, 2, 2, 3))
    img.header.set_xyzt_units("mm", "sec"); img.header["pixdim"][4] = 2.0
    img.header["toffset"] = 5.0
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    a = zarr.open_array(str(tmp_path / "o.zarr"), mode="r+")
    meta = dict(a.attrs)["duckn"]; meta["extensions"]["nifti"]["tags"].pop("toffset")
    a.attrs["duckn"] = meta
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    assert float(nib.load(str(tmp_path / "rt.nii")).header["toffset"]) == 5.0


@pytest.mark.parametrize("code,intent", [(2, "statistical-map"), (24, "statistical-map"),
                                         (1003, "label-map"), (2006, "displacement-field")])
def test_intents_named(tmp_path, code, intent):
    img = _img(); img.header["intent_code"] = code
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["intent"] == intent


def test_an_fnirt_field_is_a_vector_not_time(tmp_path):
    img = nib.Nifti1Image(np.zeros((2, 2, 2, 3), np.float32), np.eye(4))
    img.header["intent_code"] = 2006
    img.header.set_xyzt_units("mm", "sec")
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][3]["kind"] == "vector"


@pytest.mark.parametrize("code", [2007, 2008, 2009, 2016, 2017])
def test_fsl_coefficient_fields_are_components(tmp_path, code):
    img = nib.Nifti1Image(np.zeros((2, 2, 2, 3), np.float32), np.eye(4))
    img.header["intent_code"] = code
    img.header.set_xyzt_units("mm", "sec")
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][3]["kind"] == "list"


def test_statistic_parameters_are_a_list(tmp_path):
    img = nib.Nifti1Image(np.zeros((2, 2, 2, 1, 3), np.float32), np.eye(4))
    img.header["intent_code"] = 3                   # TTEST: the statistic, then its parameters
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][4]["kind"] == "list"


def test_genmatrix_shape_comes_from_p1_and_p2(tmp_path):
    img = nib.Nifti1Image(np.zeros((2, 2, 2, 1, 4), np.float32), np.eye(4))
    img.header["intent_code"], img.header["intent_p1"], img.header["intent_p2"] = 1004, 1, 4
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    assert _attrs(tmp_path / "o.zarr")["axes"][4]["kind"] == "list"   # a 1 x 4, not a 2 x 2


def test_int64_exports(tmp_path):
    from duckn.models import AxisKind, AxisMetadata, Centering, DucknMetadata, SpaceName
    meta = DucknMetadata(version="1.2", space=SpaceName.RIGHT_ANTERIOR_SUPERIOR, space_origin=[0, 0, 0],
                         value_transforms=[], axes=[
        AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                     space_direction=[1.0 if j == i else 0.0 for j in range(3)])
        for i in range(3)])
    data = np.arange(8, dtype=np.int64).reshape(2, 2, 2)
    zarr_to_nifti(_store(tmp_path, data, meta), tmp_path / "rt.nii")
    back = nib.load(str(tmp_path / "rt.nii"))
    assert np.asarray(back.dataobj).dtype == np.int64
    np.testing.assert_array_equal(np.asarray(back.dataobj), data)


def _lps_vector_store(tmp_path, *, frame=None, intent=None):
    from duckn.models import AxisKind, AxisMetadata, Centering, DucknMetadata, SpaceName
    axes = [AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                         space_direction=[1.0 if j == i else 0.0 for j in range(3)])
            for i in range(3)] + [AxisMetadata(kind=AxisKind.VECTOR)]
    meta = DucknMetadata(version="1.2", space=SpaceName.LEFT_POSTERIOR_SUPERIOR, space_origin=[0, 0, 0],
                         value_transforms=[], axes=axes, measurement_frame=frame, intent=intent)
    data = np.broadcast_to(np.array([1.0, 2.0, 3.0], np.float32), (2, 2, 2, 3)).copy()
    return _store(tmp_path, data, meta)


def test_an_lps_vector_is_written_in_ras(tmp_path):
    zarr_to_nifti(_lps_vector_store(tmp_path), tmp_path / "rt.nii")
    back = nib.load(str(tmp_path / "rt.nii"))
    np.testing.assert_allclose(np.asarray(back.dataobj)[0, 0, 0], [-1, -2, 3])
    assert int(back.header["intent_code"]) == 1007


def test_a_measurement_frame_is_applied_before_the_reframe(tmp_path):
    swap = [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]   # world x = component y
    zarr_to_nifti(_lps_vector_store(tmp_path, frame=swap), tmp_path / "rt.nii")
    np.testing.assert_allclose(
        np.asarray(nib.load(str(tmp_path / "rt.nii")).dataobj)[0, 0, 0], [-2, -1, 3])


def test_a_displacement_field_exports_as_dispvect(tmp_path):
    zarr_to_nifti(_lps_vector_store(tmp_path, intent="displacement-field"), tmp_path / "rt.nii")
    assert int(nib.load(str(tmp_path / "rt.nii")).header["intent_code"]) == 1006


def test_an_lps_tensor_is_written_in_ras(tmp_path):
    from duckn.models import AxisKind, AxisMetadata, Centering, DucknMetadata, SpaceName
    axes = [AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL,
                         space_direction=[1.0 if j == i else 0.0 for j in range(3)])
            for i in range(3)] + [AxisMetadata(kind=AxisKind.THREE_D_SYMMETRIC_MATRIX)]
    meta = DucknMetadata(version="1.2", space=SpaceName.LEFT_POSTERIOR_SUPERIOR, space_origin=[0, 0, 0],
                         value_transforms=[], axes=axes)
    comps = np.array([1, 2, 3, 4, 5, 6], np.float32)             # xx xy xz yy yz zz
    data = np.broadcast_to(comps, (2, 2, 2, 6)).copy()
    zarr_to_nifti(_store(tmp_path, data, meta), tmp_path / "rt.nii")
    back = nib.load(str(tmp_path / "rt.nii"))
    # diag(-1,-1,1) T diag(-1,-1,1): xz and yz change sign; in nifti1.h order xx xy yy xz yz zz
    np.testing.assert_allclose(np.asarray(back.dataobj).reshape(-1, 6)[0], [1, 2, 4, -3, -5, 6])
    assert int(back.header["intent_code"]) == 1005 and float(back.header["intent_p1"]) == 3


def test_a_sheared_affine_writes_no_qform(tmp_path):
    from duckn.models import AxisKind, AxisMetadata, Centering, DucknMetadata, SpaceName
    dirs = [[1.0, 0, 0], [0, 1.0, 0], [0, 0.2, 1.0]]
    meta = DucknMetadata(version="1.2", space=SpaceName.RIGHT_ANTERIOR_SUPERIOR, space_origin=[0, 0, 0],
                         value_transforms=[], axes=[
        AxisMetadata(kind=AxisKind.SPACE, centering=Centering.CELL, space_direction=d)
        for d in dirs])
    zarr_to_nifti(_store(tmp_path, np.zeros((2, 2, 2), np.int16), meta), tmp_path / "rt.nii")
    h = nib.load(str(tmp_path / "rt.nii")).header
    assert int(h["qform_code"]) == 0 and int(h["sform_code"]) > 0
    np.testing.assert_allclose(h.get_sform()[:3, 2], [0, 0.2, 1.0])


def test_a_non_ascii_description_is_written(tmp_path):
    img = _img(); img.header["descrip"] = b"x"
    p = _save(tmp_path, img)
    nifti_to_zarr(p, tmp_path / "o.zarr")
    a = zarr.open_array(str(tmp_path / "o.zarr"), mode="r+")
    meta = dict(a.attrs)["duckn"]; meta["extensions"]["nifti"]["tags"]["descrip"] = "Ålesund"
    a.attrs["duckn"] = meta
    zarr_to_nifti(tmp_path / "o.zarr", tmp_path / "rt.nii")
    assert bytes(nib.load(str(tmp_path / "rt.nii")).header["descrip"]).rstrip(b"\0") == b"?lesund"


def test_complex_values_stay_complex_under_a_scale():
    from duckn.models import ValueTransform
    from duckn.zarr_io import materialize
    out = materialize(np.array([1 + 2j, 3 - 1j], np.complex64),
                      [ValueTransform(name="linear", parameters={"slope": 2.0, "intercept": 1.0})])
    assert out.dtype == np.complex64
    np.testing.assert_allclose(out, [3 + 4j, 7 - 2j])
