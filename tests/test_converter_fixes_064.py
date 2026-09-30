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
