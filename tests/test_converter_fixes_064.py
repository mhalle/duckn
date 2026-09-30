"""0.6.4: two converter defects the convention 2.0 extension review found (2026-09-30)."""

import json

import nrrd
import numpy as np
import zarr

from duckn.convert import nrrd_to_zarr
from duckn.models import DucknMetadata


def _meta(path):
    return DucknMetadata(**zarr.open_array(str(path), mode="r").attrs["duckn"])


def _raw_nrrd(path, header_lines, data: bytes):
    path.write_bytes(("NRRD0004\n" + "\n".join(header_lines) + "\nencoding: raw\n\n").encode() + data)
    return path


def test_centers_is_read_as_well_as_centerings(tmp_path):
    """NRRD's own field is `centers` (teem writes it); `centerings` is its synonym. pynrrd
    parses only the synonym, and 0.6.3 read a node-centered file as cell-centered."""
    src = _raw_nrrd(tmp_path / "a.nrrd", ["type: uint8", "dimension: 2", "sizes: 2 3",
                                          "space dimension: 2", "space directions: (1,0) (0,1)",
                                          "space origin: (0,0)", "centers: node cell",
                                          "kinds: domain domain"], bytes(6))
    nrrd_to_zarr(src, tmp_path / "a.zarr")
    m = _meta(tmp_path / "a.zarr")
    # C order: the NRRD's last axis first
    assert [a.centering.value for a in m.axes] == ["cell", "node"]
    assert "centers" not in json.dumps((m.extensions or {}).get("keyvalues", {}))


def test_a_nifti_symmetric_tensor_is_reordered_to_duckns_components(tmp_path):
    """nifti1.h stores SYMMATRIX as the lower triangle by rows: xx xy yy xz yz zz; duckn's
    3D-symmetric-matrix is xx xy xz yy yz zz. 0.6.3 swapped Dyy and Dxz."""
    import nibabel as nib
    from duckn.nifti_convert import nifti_to_zarr, zarr_to_nifti

    comps = np.arange(6, dtype=np.float32)          # the file's slot numbers, nifti1.h's order
    data = np.broadcast_to(comps, (2, 2, 2, 1, 6)).copy()
    img = nib.Nifti1Image(data, np.eye(4))
    img.header.set_intent(1005)
    nib.save(img, str(tmp_path / "t.nii"))
    nifti_to_zarr(tmp_path / "t.nii", tmp_path / "t.zarr")
    arr = zarr.open_array(str(tmp_path / "t.zarr"), mode="r")
    m = _meta(tmp_path / "t.zarr")
    k = next(i for i, a in enumerate(m.axes) if a.kind and a.kind.value == "3D-symmetric-matrix")
    got = np.take(arr[:], 0, axis=0)[0, 0, 0]
    # xx xy xz yy yz zz from the file's slots xx=0 xy=1 yy=2 xz=3 yz=4 zz=5
    assert k == 4 and list(got) == [0, 1, 3, 2, 4, 5]
    zarr_to_nifti(tmp_path / "t.zarr", tmp_path / "back.nii")
    back = nib.load(str(tmp_path / "back.nii"))
    assert int(back.header["intent_code"]) == 1005
    assert np.array_equal(np.asarray(back.dataobj), data)


def test_non_finite_nifti_scaling_is_read_as_nifti1_io_reads_it(tmp_path):
    """nibabel refused a usable slope beside a non-finite intercept (0.6.3 crashed), and an
    infinite slope failed the linear transform's validation. nifti1_io: a slope of 0 or not
    finite is unscaled; a non-finite intercept beside a usable slope is 0."""
    import struct

    import nibabel as nib
    import pytest
    from duckn.nifti_convert import nifti_to_zarr

    d = np.arange(8, dtype=np.int16).reshape(2, 2, 2)
    for slope, inter, expect in ((2.0, float("nan"), (2.0, 0.0)), (2.0, float("inf"), (2.0, 0.0)),
                                 (float("inf"), 0.0, None), (0.0, 5.0, None)):
        p = tmp_path / f"s{slope}_{inter}.nii"
        nib.save(nib.Nifti1Image(d, np.eye(4)), str(p))
        raw = bytearray(p.read_bytes())
        struct.pack_into("<f", raw, 112, slope)             # scl_slope
        struct.pack_into("<f", raw, 116, inter)             # scl_inter
        p.write_bytes(bytes(raw))
        out = tmp_path / f"{p.stem}.zarr"
        nifti_to_zarr(p, out)
        m = _meta(out)
        got = None if not m.value_transforms else (
            m.value_transforms[0].parameters["slope"], m.value_transforms[0].parameters["intercept"])
        assert got == expect, (slope, inter)
        assert np.array_equal(zarr.open_array(str(out), mode="r")[:], d)


def _nifti_with(tmp_path, name, sform=None, sform_code=0, qform=None, qform_code=0, zooms=(2, 2, 2)):
    import nibabel as nib

    img = nib.Nifti1Image(np.zeros((2, 3, 4), np.int16), None)
    img.header.set_zooms(zooms)
    if qform is not None:
        img.set_qform(qform, code=qform_code)
    if sform is not None:
        img.set_sform(sform, code=sform_code)
    p = tmp_path / f"{name}.nii"
    nib.save(img, str(p))
    return p


def test_the_sform_is_used_as_written_whatever_pixdim_says(tmp_path):
    """nifti1.h's precedence, and nibabel's: the sform when sform_code > 0. 0.6.3 took the qform
    when the sform's column lengths disagreed with pixdim - a scanner qform then read as the
    sform's MNI frame - and otherwise rescaled the sform's columns to pixdim."""
    import warnings

    from duckn.nifti_convert import nifti_to_zarr

    sform = np.diag([2.5, 2.5, 2.5, 1.0])
    sform[:3, 3] = [-10, -20, -30]
    p = _nifti_with(tmp_path, "a", sform=sform, sform_code=4, qform=np.diag([2.0, 2, 2, 1]),
                    qform_code=1)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        nifti_to_zarr(p, tmp_path / "a.zarr")
    m = _meta(tmp_path / "a.zarr")
    assert [a.space_direction for a in m.axes] == [[2.5, 0, 0], [0, 2.5, 0], [0, 0, 2.5]]
    assert m.space_origin == [-10.0, -20.0, -30.0]
    assert any("disagree with pixdim" in str(x.message) for x in w)       # reported, not acted on


def test_a_sheared_sform_is_used_as_written(tmp_path):
    from duckn.nifti_convert import nifti_to_zarr

    sform = np.array([[2.0, 0.3, 0, 1], [0, 2.0, 0, 2], [0, 0, 3.0, 3], [0, 0, 0, 1]])
    p = _nifti_with(tmp_path, "b", sform=sform, sform_code=1, zooms=(2, 2, 3))
    nifti_to_zarr(p, tmp_path / "b.zarr")
    m = _meta(tmp_path / "b.zarr")
    np.testing.assert_allclose(np.array([a.space_direction for a in m.axes]).T, sform[:3, :3])


def test_with_no_sform_the_qform_is_used(tmp_path):
    import nibabel as nib
    from duckn.nifti_convert import nifti_to_zarr

    q = np.diag([-2.0, 2, 2, 1])
    q[:3, 3] = [5, 6, 7]
    p = _nifti_with(tmp_path, "c", qform=q, qform_code=1)
    nifti_to_zarr(p, tmp_path / "c.zarr")
    m = _meta(tmp_path / "c.zarr")
    expect = nib.load(str(p)).get_qform()
    np.testing.assert_allclose(np.array([a.space_direction for a in m.axes]).T, expect[:3, :3])
    assert m.space_origin == [5.0, 6.0, 7.0]


def test_rescale_type_us_is_unspecified_not_a_unit(tmp_path):
    """Rescale Type's "US" is UNSPECIFIED (PS3.3 C.11.1.1.2), not the Modality's ultrasound."""
    import warnings

    from test_dicom_convert_gaps import _series

    def us(i, ds):
        ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = 1, -1024, "US"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, meta = _series(tmp_path, us)
    assert meta.sample_units is None and meta.value_transforms
