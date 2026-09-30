"""The 2.0 NIfTI writer after the 2026-09-30 adversarial round on NIfTI: the decisions (no
template references, volumes with no centering, the caller's affine recorded) and the fixes,
plus the round's mutation probes that exercise ``convention="2.0"``, adapted to the decisions."""

import struct
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest
import zarr

from duckn.convention2 import read
from duckn.nifti_convert import nifti_to_zarr


def _convert(tmp, img, name="in.nii", **kw):
    p = Path(tmp) / name
    nib.save(img, str(p))
    out = Path(tmp) / (name + ".zarr")
    nifti_to_zarr(p, out, convention="2.0", overwrite=True, **kw)
    arr = zarr.open_array(str(out), mode="r")
    return dict(arr.attrs)["duckn"], arr


def _img(shape=(4, 4, 3), aff=None, s=1, q=0, dtype=np.int16):
    aff = np.diag([2.0, 2, 3, 1]) if aff is None else aff
    img = nib.Nifti1Image(np.zeros(shape, dtype), aff)
    img.set_sform(aff, code=s)
    if q:
        img.set_qform(aff, code=q)
    else:
        img.set_qform(None, code=0)
    return img


def _oblique():
    """An affine as a scanner converter writes it: rotated, offsets of ~100 mm."""
    th = np.deg2rad(13.7)
    r = np.array([[1, 0, 0], [0, np.cos(th), -np.sin(th)], [0, np.sin(th), np.cos(th)]])
    aff = np.eye(4)
    aff[:3, :3] = r @ np.diag([0.9375, 0.9375, 3.3])
    aff[:3, 3] = [-118.4, -97.61, 63.25]
    return aff


def _step(d):
    return [s for s in d.get("processing", [])] or d["extensions"]["provenance"]["processing"]


# ---- the round's probes (W-numbers are the reviewer's) --------------------------------------

def test_identical_sform_and_qform_add_no_transform(tmp_path):         # W24
    d, _ = _convert(tmp_path, _img(s=1, q=1))
    assert "transforms" not in d["world"]


def test_a_scanner_style_oblique_pair_adds_no_transform(tmp_path):
    # the qform is the sform's float32 quaternion: equal only to float32 precision
    d, _ = _convert(tmp_path, _img(aff=_oblique(), s=1, q=1))
    assert "transforms" not in d["world"]


def test_an_mni_code_names_no_frame_and_stays_in_the_record(tmp_path):  # W19, as decided
    d, _ = _convert(tmp_path, _img(s=4, q=1))
    assert "reference" not in d["world"] and "transforms" not in d["world"]
    assert d["extensions"]["nifti"]["tags"]["sform_code"] == 4


def test_a_differing_qform_is_a_transform_to_bare_qform(tmp_path):      # W22, W23, as decided
    s = np.diag([2.0, 2, 3, 1]); q = s.copy(); q[:3, 3] = [5.0, 0, 0]
    img = nib.Nifti1Image(np.zeros((4, 4, 3), np.int16), s)
    img.set_sform(s, code=1); img.set_qform(q, code=4)
    d, _ = _convert(tmp_path, img)
    (t,) = d["world"]["transforms"]
    assert t["to"]["reference"] == "qform"
    assert [a["positive"] for a in t["to"]["axes"]] == ["right", "anterior", "superior"]
    np.testing.assert_allclose(np.array(t["forward"]["affine"])[:, 3], [5.0, 0, 0])


def test_rad_per_s_is_frequency(tmp_path):                             # N12 / L01
    img = _img(shape=(4, 4, 3, 3), dtype=np.float32)
    img.header.set_xyzt_units("mm", "rads"); img.header["pixdim"][4] = 1.0
    d, _ = _convert(tmp_path, img)
    assert d["world"]["axes"][3]["type"] == "frequency"


def _fmri(shape, code, duration, start, end, toffset=0.0, version=1):
    klass = nib.Nifti1Image if version == 1 else nib.Nifti2Image
    img = klass(np.zeros(shape, np.float32), np.eye(4))
    h = img.header
    h.set_sform(np.eye(4), code=1); h.set_qform(None, code=0)
    h.set_xyzt_units("mm", "sec"); h["pixdim"][4] = 2.0; h["toffset"] = toffset
    h.set_dim_info(slice=2)
    h["slice_code"], h["slice_duration"], h["slice_start"], h["slice_end"] = code, duration, start, end
    return img


def test_interleaved_slice_times_add_to_toffset(tmp_path):              # W11
    d, arr = _convert(tmp_path, _fmri((2, 2, 4, 2), 3, 0.5, 0, 3, toffset=10.0))
    assert [s["origin"][3] for s in d["dimensions"][2]["samples"]] == [10.0, 11.0, 10.5, 11.5]
    assert d["origin"][3] == 10.0
    assert read(d, arr.shape).position([0, 0, 1, 1])[3] == 13.0      # volume 1, slice 1


def test_alternating_decreasing_2(tmp_path):                            # W03
    d, _ = _convert(tmp_path, _fmri((2, 2, 6, 2), 6, 0.1, 0, 5))
    # order 4, 2, 0, 5, 3, 1
    assert [s["origin"][3] for s in d["dimensions"][2]["samples"]] == [0.2, 0.5, 0.1, 0.4, 0.0, 0.3]


def test_nonzero_slice_start_is_partial(tmp_path):                      # W07
    d, _ = _convert(tmp_path, _fmri((2, 2, 6, 2), 1, 0.1, 1, 5))
    assert "slice_timing" in d["extensions"]["nifti"]["tags"]


@pytest.mark.parametrize("start,end", [(0, 0), (2, 1), (3, 3)])
def test_an_empty_slice_range_is_every_slice(tmp_path, start, end):
    # slice_end at or below slice_start (0, unset, as many writers leave it) states no range
    d, _ = _convert(tmp_path, _fmri((2, 2, 4, 2), 1, 0.5, start, end))
    assert "slice_timing" not in d["extensions"]["nifti"].get("tags", {})
    assert d["dimensions"][2]["step"][3] == 0.5


def test_single_volume_time_dim_has_no_centering(tmp_path):             # W14
    img = _img(shape=(2, 2, 2, 1))
    img.header.set_xyzt_units("mm", "sec"); img.header["pixdim"][4] = 2.0
    d, _ = _convert(tmp_path, img)
    assert "centering" not in d["dimensions"][3]


# ---- the decisions and fixes -----------------------------------------------------------------

def test_volumes_state_no_centering(tmp_path):
    img = _img(shape=(2, 2, 2, 3))
    img.header.set_xyzt_units("mm", "sec"); img.header["pixdim"][4] = 2.0
    d, _ = _convert(tmp_path, img)
    assert "centering" not in d["dimensions"][3]


def test_one_volume_with_a_toffset_is_placed_by_the_origin(tmp_path):
    img = _img(shape=(2, 2, 2, 1))
    img.header.set_xyzt_units("mm", "sec"); img.header["toffset"] = 5.0
    d, arr = _convert(tmp_path, img)
    assert d["origin"][3] == 5.0 and "samples" not in d["dimensions"][3]
    assert "toffset" not in d["extensions"]["nifti"].get("tags", {})


def test_the_preferred_affine_is_recorded_when_both_are_set(tmp_path):
    s = np.diag([2.0, 2, 3, 1]); q = s.copy(); q[:3, 3] = [5.0, 0, 0]
    img = nib.Nifti1Image(np.zeros((4, 4, 3), np.int16), s)
    img.set_sform(s, code=1); img.set_qform(q, code=1)
    d, _ = _convert(tmp_path, img, affine="qform")
    assert d["origin"][:3] == [5.0, 0.0, 0.0]
    (t,) = d["world"]["transforms"]
    assert t["to"]["reference"] == "sform"
    (step,) = d["extensions"]["provenance"]["processing"]
    assert step["parameters"]["affine"] == "qform"
    d, _ = _convert(tmp_path, img, name="default.nii")
    (step,) = d["extensions"]["provenance"]["processing"]
    assert step["parameters"]["affine"] == "sform"


def test_the_choice_is_not_recorded_with_one_transform(tmp_path):
    d, _ = _convert(tmp_path, _img(s=1, q=0), affine="qform")
    (step,) = d["extensions"]["provenance"]["processing"]
    assert "affine" not in (step.get("parameters") or {})


def test_the_other_frame_states_no_unit_it_does_not_know(tmp_path):
    s = np.diag([2.0, 2, 3, 1]); q = s.copy(); q[:3, 3] = [5.0, 0, 0]
    img = nib.Nifti1Image(np.zeros((4, 4, 3), np.int16), s)
    img.set_sform(s, code=1); img.set_qform(q, code=1)
    img.header.set_xyzt_units("unknown", "unknown")
    d, _ = _convert(tmp_path, img)
    (t,) = d["world"]["transforms"]
    assert all("unit" not in a for a in t["to"]["axes"])


def test_nifti2_slice_times_are_its_float64(tmp_path):
    d, _ = _convert(tmp_path, _fmri((2, 2, 4, 2), 1, 0.1234567891, 0, 3, version=2), name="n2.nii")
    assert d["dimensions"][2]["step"][3] == 0.1234567891


def test_nifti1_slice_times_are_the_float32s_shortest_decimal(tmp_path):
    d, _ = _convert(tmp_path, _fmri((2, 2, 4, 2), 1, 0.1234567891, 0, 3))
    assert d["dimensions"][2]["step"][3] == 0.12345679


def test_a_length_one_spatial_dimension_states_its_thickness(tmp_path):
    d, _ = _convert(tmp_path, _img(shape=(4, 4, 1)))
    assert d["dimensions"][2]["thickness"] == 3.0
    assert "thickness" not in d["dimensions"][0]


def test_a_complex_quantity_keeps_its_imaginary_part():
    h = read({"version": "2.0", "world": {"axes": [{"id": "x", "type": "space", "unit": "mm"}]},
              "origin": [0], "dimensions": [{"step": [1], "centering": "cell"}],
              "values": {"transforms": [{"name": "linear",
                                          "parameters": {"slope": 2, "intercept": 1}}]}},
             (2,), "complex64")
    np.testing.assert_allclose(h.quantity(np.array([1 + 2j, 3 - 1j], np.complex64)),
                               [3 + 4j, 7 - 2j])


def test_a_sample_origin_moves_with_the_origin(tmp_path):
    # the time dimension's first position (toffset) moves the origin, and the interleaved
    # slices' origins with it: samples[0].origin equals origin (§5.4)
    d, _ = _convert(tmp_path, _fmri((2, 2, 4, 2), 3, 0.5, 0, 3, toffset=10.0))
    assert d["dimensions"][2]["samples"][0]["origin"] == d["origin"]
