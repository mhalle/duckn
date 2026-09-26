"""SimpleITK's DICOM tags in the ``dicom`` extension's encoding (``duckn.dicom_tags``).

What these hold, against the ways a converter goes wrong quietly:

- the encoding rules of dicom-spec §4: keywords (hex for private tags), JSON numbers by VR,
  arrays by VM, an empty number left out rather than written ``null``, a malformed number kept
  as its text;
- what §2 and §9 exclude stays out - the convention-captured attributes, binary VRs, group
  lengths, SimpleITK's own ``ITK_`` keys;
- the series / per-slice split of §6.1, and that the per-slice dicts are what
  ``samples[i].metadata["dicom"]`` holds (§6.3) - a sample has no ``extensions``;
- on a real series read by SimpleITK, the tags are the files' own;
- ``to_sitk_strings`` inverts the series tags, and skips what SimpleITK cannot hold.
"""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")

from duckn import dicom_tags as dt  # noqa: E402
from duckn.models import SampleMetadata  # noqa: E402

CT_SOP = "1.2.840.10008.5.1.4.1.1.2"


def _pair(i, tube):
    return {"0008|0060": "CT", "0018|0050": "2.0", "0028|1050": "40\\400", "0011|1001": "vendor ",
            "0020|0013": str(i), "0008|0000": "1234", "ITK_original_spacing": "x",
            "0018|1151": str(tube)}


def test_the_encoding_rules():
    series, slices = dt.tags_from_sitk([_pair(1, 100), _pair(2, 110)])
    assert series == {"Modality": "CT", "WindowCenter": [40, 400], "00111001": "vendor"}
    assert slices == [{"InstanceNumber": 1, "XRayTubeCurrent": 100},
                      {"InstanceNumber": 2, "XRayTubeCurrent": 110}]


def test_numbers_by_vr_and_arrays_by_vm():
    assert dt.encode(0x00180060, "120.000 ") == 120.0            # DS, VM 1: a bare float
    assert dt.encode(0x00200013, " 7") == 7                      # IS: an int
    assert dt.encode(0x00280030, "0.5\\0.75") == [0.5, 0.75]     # DS, VM 2
    assert dt.encode(0x00080008, "ORIGINAL\\PRIMARY\\AXIAL") == ["ORIGINAL", "PRIMARY", "AXIAL"]
    assert dt.encode(0x00081090, "LightSpeed16 ") == "LightSpeed16"
    assert dt.encode(0x00181020, "06MW03.5") == ["06MW03.5"]     # VM 1-n: an array of one
    assert dt.encode(0x00180060, "") is None                    # empty number: nothing
    assert dt.encode(0x00180060, "n/a") == "n/a"                 # malformed: the text, not a guess


def test_an_empty_number_is_absent_never_null():
    series, _ = dt.tags_from_sitk([{"0018|0060": "", "0008|0060": "CT"}] * 2)
    assert series == {"Modality": "CT"}


def test_what_the_convention_captures_stays_out():
    captured = {"0020|0032": "0\\0\\0", "0020|0037": "1\\0\\0\\0\\1\\0", "0028|0030": "0.5\\0.5",
                "0018|0088": "2", "0018|0050": "2", "0028|0010": "512", "0028|0011": "512",
                "0028|0100": "16", "0028|0101": "12", "0028|0102": "11", "0028|0103": "1",
                "0028|1052": "-1024", "0028|1053": "1", "0028|1054": "HU", "0028|3004": "HU",
                "7fe0|0010": "x", "0010|0000": "12"}
    series, slices = dt.tags_from_sitk([dict(captured, **{"0008|0060": "CT"})] * 2)
    assert series == {"Modality": "CT"} and slices == [{}, {}]


def test_binary_vrs_are_skipped():
    # SimpleITK's string of an OB/OW value is no base64 of its bytes (§4.5)
    series, _ = dt.tags_from_sitk([{"0029|0010": "SIEMENS CSA", "0008|0060": "CT",
                                    "0028|1201": "\x00\x01\x02"}] * 2)   # Red Palette LUT Data, OW
    assert "RedPaletteColorLookupTableData" not in series and series["Modality"] == "CT"
    series, _ = dt.tags_from_sitk([{"6000|3000": "x", "6002|3000": "x", "0008|0060": "CT"}] * 2)
    assert series == {"Modality": "CT"}                 # Overlay Data, OB/OW, in any group


def test_a_tag_some_slices_lack_is_per_slice():
    series, slices = dt.tags_from_sitk([{"0008|0060": "CT", "0018|1151": "100"}, {"0008|0060": "CT"}])
    assert series == {"Modality": "CT"} and slices == [{"XRayTubeCurrent": 100}, {}]


def test_the_per_slice_dicts_are_sample_metadata():
    _, slices = dt.tags_from_sitk([_pair(1, 100), _pair(2, 110)])
    samples = [SampleMetadata(metadata={"dicom": s}) for s in slices]
    assert samples[1].metadata["dicom"]["XRayTubeCurrent"] == 110
    with pytest.raises(Exception):                      # §6.3: a sample has no extensions
        SampleMetadata(extensions={"dicom": slices[0]})


def test_to_sitk_strings_inverts_the_series_tags():
    assert dt.to_sitk_strings({"Modality": "CT", "KVP": 120.0, "WindowCenter": [40, 400],
                               "00111001": "vendor", "PatientName": None,
                               "ReferencedImageSequence": [{"ReferencedSOPInstanceUID": "1.2"}]}) == {
        "0008|0060": "CT", "0018|0060": "120", "0028|1050": "40\\400", "0011|1001": "vendor"}


def _series(folder, n=4):
    from pydicom.dataset import FileDataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    folder.mkdir()
    series, study, frame = generate_uid(), generate_uid(), generate_uid()
    for i in range(n):
        sop = generate_uid()
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID = CT_SOP, sop
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID, ds.SOPInstanceUID = CT_SOP, sop
        ds.Modality, ds.KVP, ds.PatientName = "CT", 120, "Fixture^Patient"
        ds.SeriesInstanceUID, ds.StudyInstanceUID, ds.FrameOfReferenceUID = series, study, frame
        ds.InstanceNumber, ds.XRayTubeCurrent = i + 1, 100 + 10 * i
        ds.ImagePositionPatient = [-10.0, -12.0, 31.0 + 2 * i]
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.PixelSpacing, ds.SliceThickness = [0.75, 0.5], 2.0
        ds.WindowCenter, ds.WindowWidth = [40, 400], [400, 1500]
        ds.RescaleIntercept, ds.RescaleSlope, ds.RescaleType = -1024, 1, "HU"
        ds.Rows, ds.Columns = 3, 2
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit, ds.PixelRepresentation, ds.SamplesPerPixel = 15, 1, 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.PixelData = np.zeros((3, 2), np.int16).tobytes()
        ds.save_as(folder / f"IM{i}.dcm", enforce_file_format=True)
    return folder


def test_a_series_simpleitk_read(tmp_path):
    sitk = pytest.importorskip("SimpleITK")
    folder = _series(tmp_path / "s")
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames(sitk.ImageSeriesReader.GetGDCMSeriesFileNames(str(folder)))
    reader.MetaDataDictionaryArrayUpdateOn()
    reader.LoadPrivateTagsOn()
    reader.Execute()
    per_slice = [{k: reader.GetMetaData(i, k) for k in reader.GetMetaDataKeys(i)}
                 for i in range(len(reader.GetFileNames()))]
    series, slices = dt.tags_from_sitk(per_slice)
    assert series["Modality"] == "CT" and series["KVP"] == 120
    assert series["PatientName"] == "Fixture^Patient" and series["WindowCenter"] == [40, 400]
    assert not {"PixelSpacing", "SliceThickness", "RescaleSlope", "RescaleType",
                "ImagePositionPatient", "Rows"} & series.keys()
    assert [s["XRayTubeCurrent"] for s in slices] == [100, 110, 120, 130]
    assert [s["InstanceNumber"] for s in slices] == [1, 2, 3, 4]
    assert len({s["SOPInstanceUID"] for s in slices}) == 4       # §6.3: kept, per slice
    assert float(per_slice[0][dt.SLICE_THICKNESS]) == 2.0  # what a caller moves to fields
    assert per_slice[0][dt.RESCALE_TYPE].strip() == "HU"


def _rich(folder, n=4):
    """The fixture series, plus what SimpleITK's dictionaries cannot hold: a sequence, a binary
    value, a private binary value, an empty value, overlay data and waveform data."""
    import pydicom
    from pydicom.dataset import Dataset
    from pydicom.sequence import Sequence
    _series(folder, n)
    for i, f in enumerate(sorted(folder.iterdir())):
        ds = pydicom.dcmread(f)
        item = Dataset()
        item.ReferencedSOPClassUID, item.ReferencedSOPInstanceUID = CT_SOP, f"1.2.3.{i}"
        item.ReferencedFrameNumber = ""                    # empty inside a sequence: left out
        ds.ReferencedImageSequence = Sequence([item])
        ds.add_new(0x00282000, "OB", b"\x01\x02\x03\x04")  # ICC Profile: binary, kept
        ds.add_new(0x00091010, "OB", bytes([i, 7]))         # private, binary, varies
        ds.add_new(0x00091000, "LO", "ACME 1.0")             # its creator
        ds.StudyDescription = ""                             # empty text: ""
        ds.add_new(0x00181150, "IS", None)                   # empty number: left out, never null
        ds.add_new(0x60003000, "OW", b"\x00" * 6)             # overlay data: bulk
        ds.add_new(0x54001004, "US", 16)                     # (GDCM reads waveform data only
        ds.add_new(0x54001010, "OW", b"\x00" * 4)             #  with its bits): bulk
        ds.save_as(f, enforce_file_format=True)
    return sorted(folder.iterdir())


def test_datasets_keep_sequences_binary_and_private_tags(tmp_path):
    files = _rich(tmp_path / "s")
    series, slices, ext = dt.tags_from_files(files)
    assert series["ICCProfile"] == "AQIDBA=="
    assert series["00091000"] == "ACME 1.0"
    assert [s["00091010"] for s in slices] == ["AAc=", "AQc=", "Agc=", "Awc="]
    assert [s["ReferencedImageSequence"] for s in slices][2] == [
        {"ReferencedSOPClassUID": CT_SOP, "ReferencedSOPInstanceUID": "1.2.3.2"}]
    assert series["StudyDescription"] == "" and "ExposureTime" not in series
    assert not {"OverlayData", "WaveformData", "PixelData"} & (series.keys() | slices[0].keys())
    assert not any(k.startswith("6000") or k.startswith("5400") for k in series)
    assert ext == {"source_transfer_syntax": "1.2.840.10008.1.2.1", "lossy_compressed": False}


def test_datasets_exclude_what_the_convention_captures_and_the_file_meta(tmp_path):
    series, slices, _ = dt.tags_from_files(_rich(tmp_path / "s"))
    everything = series.keys() | {k for s in slices for k in s}
    assert not {"PixelSpacing", "SliceThickness", "RescaleSlope", "RescaleIntercept",
                "RescaleType", "ImagePositionPatient", "ImageOrientationPatient", "Rows",
                "Columns", "BitsAllocated"} & everything
    assert not {"TransferSyntaxUID", "MediaStorageSOPInstanceUID"} & everything


def test_datasets_agree_with_simpleitk_on_every_tag_both_see(tmp_path):
    """The two sources must encode one value one way: a copy made through either reads the same."""
    sitk = pytest.importorskip("SimpleITK")
    files = _rich(tmp_path / "s")
    reader = sitk.ImageSeriesReader()
    reader.SetFileNames([str(f) for f in files])
    reader.MetaDataDictionaryArrayUpdateOn()
    reader.LoadPrivateTagsOn()
    reader.Execute()
    per_slice = [{k: reader.GetMetaData(i, k) for k in reader.GetMetaDataKeys(i)}
                 for i in range(len(files))]
    s_series, s_slices = dt.tags_from_sitk(per_slice)
    p_series, p_slices, _ = dt.tags_from_files(files)
    for k, v in s_series.items():
        assert p_series[k] == v, k
    for s, p in zip(s_slices, p_slices):
        for k, v in s.items():
            assert p[k] == v, k
    assert len(p_series) > len(s_series)                   # and says more


def test_datasets_a_tag_some_slices_lack_is_per_slice_and_an_iterable_is_enough():
    from pydicom.dataset import Dataset

    def gen():
        for i in range(3):
            ds = Dataset()
            ds.Modality, ds.InstanceNumber = "MR", i
            if i != 1:
                ds.EchoTime = 5.0
            yield ds
    series, slices, ext = dt.tags_from_datasets(gen())
    assert series == {"Modality": "MR"}
    assert slices == [{"EchoTime": 5.0, "InstanceNumber": 0}, {"InstanceNumber": 1},
                      {"EchoTime": 5.0, "InstanceNumber": 2}]
    assert ext == {}
    assert dt.tags_from_datasets([]) == ({}, [], {})


def test_duckn_io_imports_in_a_fresh_process():
    # `from duckn import io` recursed without end: __getattr__("io") ran `from . import io`
    r = subprocess.run([sys.executable, "-c", "from duckn import io; print(io.__name__)"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == "duckn.io", r.stderr[-500:]
