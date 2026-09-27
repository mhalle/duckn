"""A color axis states its color space (convention 1.2, duckn-spec §3.2 `color_space`).

An RGB-color axis said which component is red; nothing said what the numbers mean. DICOM's
Color Space (0028,2002) says it and was dropped. The vocabulary is CSS Color 4's predefined
spaces - the same answer the seg extension's color strings give.
"""
from __future__ import annotations

import warnings

import numpy as np
import pytest

from duckn.models import AxisMetadata, DucknMetadata

pydicom = pytest.importorskip("pydicom")
zarr = pytest.importorskip("zarr")

from pydicom.dataset import Dataset, FileDataset, FileMetaDataset  # noqa: E402
from pydicom.sequence import Sequence  # noqa: E402
from pydicom.uid import ExplicitVRLittleEndian, generate_uid  # noqa: E402

from duckn.dicom_convert import dicom_to_zarr, zarr_to_dicom  # noqa: E402


# --- the model ------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,space", [
    ("RGB-color", "srgb"), ("RGB-color", "display-p3"), ("RGB-color", "a98-rgb"),
    ("RGB-color", "prophoto-rgb"), ("RGB-color", "rec2020"), ("RGB-color", "srgb-linear"),
    ("RGB-color", "display-p3-linear"), ("RGBA-color", "srgb"),
    ("XYZ-color", "xyz-d50"), ("XYZ-color", "xyz-d65"),
])
def test_a_color_axis_takes_a_space_of_its_own_kind(kind, space):
    assert AxisMetadata(kind=kind, color_space=space).color_space == space


@pytest.mark.parametrize("kind,space,match", [
    ("space", "srgb", "applies to RGB-color"),
    ("3-color", "srgb", "applies to RGB-color"),        # generic: no component order stated
    ("HSV-color", "srgb", "applies to RGB-color"),
    (None, "srgb", "no kind"),
    ("RGB-color", "xyz-d65", "use one of srgb"),         # an XYZ space on an RGB axis
    ("XYZ-color", "srgb", "use one of xyz-d50"),
    ("XYZ-color", "xyz", "use one of xyz-d50"),          # CSS's alias: a file names its white
    ("RGB-color", "sRGB", "use one of srgb"),            # the canonical spelling only
    ("RGB-color", "adobe-rgb", "use one of srgb"),
])
def test_a_space_that_does_not_fit_the_axis_is_refused(kind, space, match):
    with pytest.raises(ValueError, match=match):
        AxisMetadata(kind=kind, color_space=space)


# --- DICOM import ---------------------------------------------------------------------------

RGB = np.stack([np.full((4, 4, 3), (10 * i, 100, 200), dtype=np.uint8) for i in range(3)])


def _series(tmp_path, *, bits=8, stored=None, **tags):
    d = tmp_path / "dicom"
    d.mkdir()
    dtype = np.uint8 if bits == 8 else np.uint16
    for i in range(3):
        ds = Dataset()
        ds.Rows = ds.Columns = 4
        ds.ImagePositionPatient = [0.0, 0.0, float(i)]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [1.0, 1.0]
        ds.BitsAllocated = bits
        ds.BitsStored = stored or bits
        ds.HighBit = (stored or bits) - 1
        ds.PixelRepresentation = 0
        ds.SamplesPerPixel = 3
        ds.PhotometricInterpretation = "RGB"
        ds.PlanarConfiguration = 0
        ds.SeriesInstanceUID = "1.2.3.4.77"
        ds.Modality = "OT"
        for k, v in tags.items():
            setattr(ds, k, v)
        ds.PixelData = RGB[i].astype(dtype).tobytes()
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.7"
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        path = d / f"s{i}.dcm"
        FileDataset(str(path), ds, file_meta=meta, preamble=b"\0" * 128).save_as(str(path))
    return d


def _import(tmp_path, **kw):
    binary = kw.pop("binary_tags", False)
    out = tmp_path / "rgb.zarr"
    dicom_to_zarr(_series(tmp_path, **kw), out, binary_tags=binary)
    arr = zarr.open_array(zarr.storage.LocalStore(str(out)), mode="r")
    return out, DucknMetadata(**arr.attrs["duckn"])


@pytest.mark.parametrize("term,space", [
    ("SRGB", "srgb"), ("ADOBERGB", "a98-rgb"), ("ROMMRGB", "prophoto-rgb"),
    ("DISPLAYP3", "display-p3"),
])
def test_dicom_color_space_becomes_the_axis_color_space(tmp_path, term, space):
    _, meta = _import(tmp_path, ColorSpace=term)
    assert meta.axes[-1].color_space == space
    assert meta.version == "1.2"                   # color_space is a 1.2 field
    assert meta.value_transforms == []             # the values ARE the components, stated


def test_a_color_space_on_the_one_optical_path_is_read(tmp_path):
    """A whole-slide image keeps its ICC Profile and Color Space in the Optical Path item."""
    path = Dataset()
    path.ColorSpace = "SRGB"
    _, meta = _import(tmp_path, OpticalPathSequence=Sequence([path]))
    assert meta.axes[-1].color_space == "srgb"


@pytest.mark.parametrize("kw", [
    {},                                                       # the file names nothing
    {"ICCProfile": b"\0" * 128},                              # a profile names no space
    {"ColorSpace": "CIELAB"},                                 # not a defined term
    {"bits": 16, "stored": 12, "ColorSpace": "SRGB"},         # 12 bits in 16: not 0-1 as read
])
def test_nothing_is_stated_that_the_file_does_not_state(tmp_path, kw):
    _, meta = _import(tmp_path, **kw)
    assert meta.axes[-1].color_space is None
    assert meta.version == "1.0"                  # and nothing about the values changes


def test_a_rescaled_rgb_series_states_no_color_space(tmp_path):
    """Its calibrated values are not the full-range components the axis would claim."""
    _, meta = _import(tmp_path, ColorSpace="SRGB", RescaleSlope=2.0, RescaleIntercept=0.0)
    assert meta.axes[-1].color_space is None
    assert meta.value_transforms[0].name == "linear"


# --- DICOM export ---------------------------------------------------------------------------

def _restate(path, space):
    arr = zarr.open_array(zarr.storage.LocalStore(str(path)), mode="r+")
    attrs = dict(arr.attrs["duckn"])
    attrs["axes"][-1]["color_space"] = space
    if space is None:
        del attrs["axes"][-1]["color_space"]
    arr.attrs["duckn"] = attrs


def _export(path, tmp_path):
    out = tmp_path / "out.dcm"
    zarr_to_dicom(path, out)
    return pydicom.dcmread(str(out))


def test_export_writes_the_axis_color_space_as_its_term(tmp_path):
    path, _ = _import(tmp_path, ColorSpace="ADOBERGB")
    assert _export(path, tmp_path).ColorSpace == "ADOBERGB"


def test_export_keeps_a_carried_profile_that_agrees(tmp_path):
    path, _ = _import(tmp_path, ColorSpace="SRGB", ICCProfile=b"\1" * 64, binary_tags=True)
    ds = _export(path, tmp_path)
    assert ds.ColorSpace == "SRGB" and ds.ICCProfile == b"\1" * 64


def test_the_axis_wins_over_carried_tags_and_takes_the_profile_with_them(tmp_path):
    """The carried ICC Profile describes the source's space; the standard requires Color Space
    to agree with it, so it cannot ride along under another term."""
    path, _ = _import(tmp_path, ColorSpace="SRGB", ICCProfile=b"\1" * 64, binary_tags=True)
    _restate(path, "display-p3")
    ds = _export(path, tmp_path)
    assert ds.ColorSpace == "DISPLAYP3"
    assert "ICCProfile" not in ds


def test_a_space_dicom_cannot_name_is_left_out_with_a_warning(tmp_path):
    path, _ = _import(tmp_path, ColorSpace="SRGB", ICCProfile=b"\1" * 64, binary_tags=True)
    _restate(path, "rec2020")
    with pytest.warns(UserWarning, match="no DICOM Color Space term"):
        ds = _export(path, tmp_path)
    assert "ColorSpace" not in ds and "ICCProfile" not in ds


def test_an_axis_that_states_nothing_leaves_the_carried_tags_alone(tmp_path):
    path, _ = _import(tmp_path, ColorSpace="SRGB", ICCProfile=b"\1" * 64, binary_tags=True)
    _restate(path, None)
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        ds = _export(path, tmp_path)
    assert ds.ColorSpace == "SRGB" and ds.ICCProfile == b"\1" * 64


# --- cast keeps a color space only while the colors are unchanged -----------------------------

def _srgb(dtype=np.uint8):
    from duckn.volume import Volume
    return Volume(raw=RGB[0].astype(dtype), metadata=DucknMetadata(
        version="1.2", value_transforms=[],
        axes=[{"kind": "space"}, {"kind": "space"}, {"kind": "RGB-color", "color_space": "srgb"}]))


@pytest.mark.parametrize("target,kw,kept", [
    ("uint8", {}, True),                                          # nothing moved
    ("float32", {}, False),                                       # 255 -> 255.0, but 1.0 is white
    ("uint16", {}, False),                                        # 255 is no longer full scale
    ("float32", {"normalize": True, "range": (0, 255)}, True),    # exactly the components
    ("uint16", {"normalize": True, "range": (0, 255)}, True),     # full range to full range
    ("float32", {"normalize": True}, False),                      # the data's own min/max: new colors
    ("float32", {"normalize": True, "range": (0, 100)}, False),   # a stretch: other colors
    ("float32", {"normalize": True, "range": (10, 255)}, False),
    ("int16", {}, False),                                         # a signed type needs a mapping
])
def test_cast_keeps_a_color_space_only_while_the_colors_are_the_same(target, kw, kept):
    from duckn.cast import cast
    out = cast(_srgb(), target, **kw)
    assert (out.metadata.axes[-1].color_space == "srgb") is kept
    if kept:
        assert out.metadata.value_transforms == []    # the reading the color_space relies on


def test_an_interpolating_resample_states_the_mapping_its_float_values_need():
    """uint8 interpolates to float 0-255; read through `[]` a float's 1.0 is white, so the
    result states slope 1/255 and its colors are the source's."""
    pytest.importorskip("scipy")
    from duckn.resample import resample
    from duckn.volume import Volume
    src = Volume(raw=np.stack([RGB[1]] * 2), metadata=DucknMetadata(
        version="1.2", value_transforms=[], space="left-posterior-superior",
        space_origin=[0.0, 0.0, 0.0],
        axes=[{"kind": "space", "centering": "cell", "space_direction": d, "unit": "mm"}
              for d in ([0, 0, 1.0], [0, 1.0, 0], [1.0, 0, 0])]
        + [{"kind": "RGB-color", "color_space": "srgb"}]))
    out = resample(src, factor=[1.0, 2.0, 2.0], order=1)
    assert out.raw.dtype.kind == "f"
    assert out.metadata.axes[-1].color_space == "srgb"
    (t,) = out.metadata.value_transforms
    assert t.name == "linear" and t.parameters["slope"] == pytest.approx(1 / 255)
    np.testing.assert_allclose(out.data[0, 0, 0], np.array([10, 100, 200]) / 255, atol=1e-9)
