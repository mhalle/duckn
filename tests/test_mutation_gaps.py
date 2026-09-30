"""Tests that close gaps a mutation run of 0.5.4 found (2026-09-26): each kills a mutant the
suite let through. They pass on 0.5.4 - they guard behavior that was right and untested.

D27 checked: the streaming converter judges the byte copy over EVERY header's Bits Stored, not
the first's (``fills`` in ``dicom_to_zarr_streaming``); the test pins it."""
import sys, types, warnings
from unittest import mock

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")
from duckn import dicom_tags as dt  # noqa: E402

DIMENSION_INDEX_POINTER = 0x00209165          # AT, VM 1
WINDOW_CENTER = 0x00281050                    # DS, VM 1-n


# D02: an attribute tag with hex letters is written uppercase (§4.2)
def test_an_attribute_tag_with_letters_is_uppercase():
    assert dt.encode(DIMENSION_INDEX_POINTER, "(7fe0,0010)") == "7FE00010"
    assert dt.encode(DIMENSION_INDEX_POINTER, "0028a001") == "0028A001"


# D01: what is not eight hex digits is kept as it came, never padded out (a guess)
def test_a_short_attribute_tag_is_kept_as_it_came():
    assert dt.encode(DIMENSION_INDEX_POINTER, "1063") == "1063"


# D04: a malformed numeric multi-value keeps its text, and its empty parts are still dropped
def test_a_malformed_multi_value_drops_its_empty_parts():
    assert dt.encode(WINDOW_CENTER, "abc\\\\40") == ["abc", "40"]


# D13, D14: a value pydicom REFUSES to convert (strict validation) is kept as its text, with its
# padding cut - never dropped
def test_a_value_pydicom_refuses_is_kept_as_its_text(monkeypatch):
    from pydicom import config
    from pydicom.dataelem import RawDataElement
    from pydicom.dataset import Dataset
    from pydicom.tag import Tag
    monkeypatch.setattr(config.settings, "reading_validation_mode", config.RAISE)
    ds = Dataset()
    ds.Modality = "CT"
    ds[0x00180060] = RawDataElement(Tag(0x00180060), "DS", 4, b"abc\x00", 0, True, True)
    got = dt.dataset_tags(ds)
    assert got["KVP"] == "abc" and got["Modality"] == "CT"


# D18: a top-level Pixel Value Transformation Sequence is the value mapping (value_transforms)
def test_a_top_level_pixel_value_transformation_is_left_out():
    from pydicom.dataset import Dataset
    from pydicom.sequence import Sequence
    ds = Dataset()
    ds.Modality = "CT"
    pv = Dataset()
    pv.RescaleIntercept, pv.RescaleSlope, pv.RescaleType = -1024, 1, "HU"
    ds.PixelValueTransformationSequence = Sequence([pv])
    got = dt.dataset_tags(ds)
    assert "PixelValueTransformationSequence" not in got and got["Modality"] == "CT"


# D20: a tag some slices lack is stated only where present
def test_the_split_leaves_a_missing_tag_missing():
    per_time, per_slice = dt.split_time_and_slice([{"A": 1}, {}, {"A": 1}, {}], 2, 2)
    assert per_slice == [{"A": 1}, {}] and per_time == [{}, {}]


# D24: a varying rescale keeps each slice's Rescale Type beside its slope and intercept
def test_a_varying_rescale_states_the_rescale_type_every_slice_shares(tmp_path):
    from test_dicom_convert_gaps import _series

    def vary(i, ds):
        ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = [1, 2, 1][i], -1024, "HU"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _, meta = _series(tmp_path, vary)
    # 0.6.3: a rescale that varies by slice is an axis_linear; the RescaleType every slice
    # shares is the quantity's unit, stated once.
    assert meta.sample_units == "HU" and meta.value_transforms[0].name == "axis_linear"


# D27: the byte copy needs EVERY slice's stored bits to fill the container, not the first's
def test_the_streaming_converter_asks_every_slice_whether_its_bits_fill(tmp_path):
    from duckn.dicom_convert import dicom_to_zarr_streaming
    import zarr
    from test_dicom_convert_gaps import _series

    def later_signed12(i, ds):
        if i == 0:
            ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 15, 1
            ds.PixelData = np.array([5] * 64, np.int16).tobytes()
        else:
            ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 12, 11, 1
            ds.PixelData = np.array([0x0FFF, 0x0800, 5, 0x07FF] * 16, np.uint16).tobytes()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _series(tmp_path, later_signed12)
        s = tmp_path / "s.zarr"
        dicom_to_zarr_streaming(tmp_path / "dicom", s)
    got = np.asarray(zarr.open_array(zarr.storage.LocalStore(str(s)), mode="r")[1]).ravel()[:4]
    assert list(got) == [-1, -2048, 5, 2047]


# D31: the DICOMweb builder writes no binary values (base64) into the tags
def test_the_dicomweb_builder_keeps_no_binary_value(tmp_path, monkeypatch):
    import httpx
    from duckn import idc_zmp
    import duckn.models as models
    from test_dicomweb_tags import _instances
    inst = _instances()
    for j in inst:
        j["00281201"] = {"vr": "OW", "InlineBinary": "AAABAAIA"}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return inst

    class _Client:
        def __init__(self, *a, **k):
            pass

        def get(self, *a, **k):
            return _Resp()

        def close(self):
            pass
    monkeypatch.setattr(httpx, "Client", _Client)
    zm = types.ModuleType("zarr_zmp")
    zm.Builder = mock.MagicMock()
    monkeypatch.setitem(sys.modules, "zarr_zmp", zm)
    seen = {}
    real = models.DucknMetadata

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)
    monkeypatch.setattr(models, "DucknMetadata", spy)
    try:
        idc_zmp.build_dicomweb_zmp("http://srv", "1.2.3.4", str(tmp_path / "x.zmp"),
                                   study_uid="1.2.3")
    except Exception:
        pass
    tags = seen["extensions"]["dicom"]["tags"]
    assert tags["Modality"] == "CT"
    assert "RedPaletteColorLookupTableData" not in tags


# D33: an empty multi-valued text ([""]) is no value for a BIDS field either
def test_the_bids_sidecar_carries_no_empty_array():
    from duckn.bids import duckn_to_bids_sidecar
    from duckn.models import AxisMetadata, DucknMetadata
    meta = DucknMetadata(version="1.0", space="LPS", space_origin=[0, 0, 0],
                         axes=[AxisMetadata(kind="space", space_direction=[1, 0, 0])] * 3,
                         extensions={"dicom": {"version": "1.0",
                                               "tags": {"ImageType": [""], "Manufacturer": "X"}}})
    sidecar = duckn_to_bids_sidecar(meta)
    assert "ImageType" not in sidecar and sidecar.get("Manufacturer") == "X"


# D36: the nibabel adapter states the convention version too
def test_the_nibabel_adapter_states_the_convention_version():
    nib = pytest.importorskip("nibabel")
    from duckn.nibabel_adapter import from_nifti
    img = nib.Nifti1Image(np.zeros((3, 3, 3), np.int16), np.eye(4))
    assert from_nifti(img).metadata.version == "1.0"
