"""Gaps a mutation run of 0.5.3 found in dicom_tags' tests (2026-09-26). The stored-unit list is
LITERAL on purpose: a test parametrized over ``dt.STORED_ENCODING`` shrinks with the set it
guards, and let three removals live."""
import base64
import pytest
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
import duckn.dicom_tags as dt


@pytest.mark.parametrize("tag", [0x00280101, 0x00280102, 0x00280106, 0x00280107, 0x00280108,
                                 0x00280109, 0x00280110, 0x00280111, 0x00280120, 0x00280121])
def test_every_stored_unit_attribute_follows_stored_values(tag):
    ds = Dataset(); ds.Modality = "CT"
    from pydicom.datadict import dictionary_VR
    vr = dictionary_VR(tag).split(" or ")[-1]
    ds.add_new(tag, vr, 7)
    assert dt.keyword_of(tag) not in dt.dataset_tags(ds)
    assert dt.keyword_of(tag) in dt.dataset_tags(ds, stored_values=True)
    k = f"{tag >> 16:04x}|{tag & 0xFFFF:04x}"
    assert dt.tags_from_sitk([{k: "7", "0008|0060": "CT"}])[0] == {"Modality": "CT"}
    assert dt.keyword_of(tag) in dt.tags_from_sitk([{k: "7"}], stored_values=True)[0]


def test_an_icon_image_keeps_no_pixel_data():
    icon = Dataset(); icon.Rows = 2; icon.PixelData = b"\x00" * 8
    ds = Dataset(); ds.IconImageSequence = Sequence([icon])
    assert dt.dataset_tags(ds)["IconImageSequence"] == [{"Rows": 2}]


@pytest.mark.parametrize("group", [0x6002, 0x601E])
def test_every_overlay_group_stays_out(group):
    ds = Dataset(); ds.Modality = "CT"
    ds.add_new((group << 16) | 0x0010, "US", 3)
    ds.add_new((group << 16) | 0x3000, "OW", b"\x00" * 4)
    assert dt.dataset_tags(ds) == {"Modality": "CT"}
    assert "OverlayData" not in dt.dataset_tags(ds, exclude=False)


def test_curve_groups_stay_out():
    ds = Dataset(); ds.Modality = "CT"
    ds.add_new(0x50000005, "US", 1); ds.add_new(0x50003000, "OW", b"\x00" * 4)
    assert dt.dataset_tags(ds) == {"Modality": "CT"}
    assert not {"CurveData", "50003000"} & dt.dataset_tags(ds, exclude=False).keys()


def test_file_meta_and_group_lengths_from_simpleitk_stay_out():
    s, _ = dt.tags_from_sitk([{"0002|0010": "1.2.840.10008.1.2.1", "0008|0000": "40", "0008|0060": "CT"}])
    assert s == {"Modality": "CT"}


def _ds(i, lossy=None, ts="1.2.840.10008.1.2.1"):
    ds = Dataset(); ds.Modality = "CT"; ds.InstanceNumber = i
    ds.file_meta = FileMetaDataset(); ds.file_meta.TransferSyntaxUID = ts
    if lossy: ds.LossyImageCompression = lossy
    return ds


def test_any_lossy_slice_makes_the_series_lossy():
    _, _, ext = dt.tags_from_datasets([_ds(0), _ds(1, "01"), _ds(2)])
    assert ext.get("lossy_compressed") is True


def test_mixed_transfer_syntaxes_state_none():
    _, _, ext = dt.tags_from_datasets([_ds(0), _ds(1, ts="1.2.840.10008.1.2.4.50")])
    assert "source_transfer_syntax" not in ext


def test_an_empty_multivalued_text_is_an_array():
    ds = Dataset(); ds.add_new(0x00080008, "CS", "")        # ImageType, VM 2-n
    assert dt.dataset_tags(ds)["ImageType"] == [""]


def test_a_file_without_the_preamble_is_read(tmp_path):
    ds = Dataset(); ds.Modality = "CT"; ds.SOPInstanceUID = "1.2.3"
    ds.file_meta = FileMetaDataset(); ds.file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"
    p = tmp_path / "raw"
    pydicom.dcmwrite(p, ds, enforce_file_format=False, implicit_vr=True, little_endian=True)
    assert p.read_bytes()[128:132] != b"DICM"
    s, _, _ = dt.tags_from_files([p])
    assert s["Modality"] == "CT"


def test_an_adapters_new_metadata_states_the_convention_version():
    """duckn-spec §3.1: `version` should always be present; from_sitk wrote none (found
    checking haversack's input copies against the spec, 2026-09-26)."""
    SimpleITK = pytest.importorskip("SimpleITK")
    from duckn.sitk_adapter import from_sitk
    vol = from_sitk(SimpleITK.Image([3, 3, 3], SimpleITK.sitkInt16))
    assert vol.metadata.version == "1.0"
