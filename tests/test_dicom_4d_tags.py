"""A time series' tags sit on the axis they vary along (dicom-spec §6.1, 2026-09-26).

0.5.3 took tags from time point 0 alone and put its instance identifiers on the slice axis -
so slice z at every time point was claimed to be one instance - and stated TriggerTime,
AcquisitionTime and TemporalPositionIdentifier (time point 0's) series-wide."""
from __future__ import annotations

import os
import warnings

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")
zarr = pytest.importorskip("zarr")

from duckn.dicom_convert import dicom_to_zarr  # noqa: E402
from duckn.dicom_tags import split_time_and_slice  # noqa: E402


def _series(d, n_t=2, n_z=3):
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid
    os.makedirs(d)
    k = 0
    for t in range(n_t):
        for z in range(n_z):
            ds = Dataset()
            ds.file_meta = FileMetaDataset()
            ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
            ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.4"
            ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
            ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
            ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.Modality = "1.2.3", "1.2.3.4", "MR"
            ds.ImagePositionPatient = [0, 0, z * 2.0]
            ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
            ds.PixelSpacing = [1, 1]
            ds.Rows = ds.Columns = 4
            ds.BitsAllocated = ds.BitsStored = 16
            ds.HighBit, ds.PixelRepresentation, ds.SamplesPerPixel = 15, 0, 1
            ds.PhotometricInterpretation = "MONOCHROME2"
            ds.InstanceNumber = k + 1
            ds.SliceLocation = z * 2.0
            ds.TriggerTime = 100.0 * t
            ds.TemporalPositionIdentifier = t + 1
            ds.PixelData = np.full((4, 4), t, np.uint16).tobytes()
            ds.save_as(os.path.join(d, f"{k}.dcm"), enforce_file_format=True)
            k += 1
    return d


def test_tags_follow_the_axis_they_vary_along(tmp_path):
    warnings.simplefilter("ignore")
    dicom_to_zarr(_series(str(tmp_path / "s")), str(tmp_path / "o.zarr"))
    m = zarr.open_array(str(tmp_path / "o.zarr")).attrs["duckn"]
    kinds = [a["kind"] for a in m["axes"]]
    assert kinds[0] == "time" and kinds[1] == "space"
    series = m["extensions"]["dicom"]["tags"]
    assert not {"TriggerTime", "TemporalPositionIdentifier", "InstanceNumber",
                "SOPInstanceUID", "SliceLocation"} & series.keys()
    per_time = [(s.get("metadata") or {}).get("dicom", {}) for s in m["axes"][0]["samples"]]
    assert [t["TemporalPositionIdentifier"] for t in per_time] == [1, 2]
    per_slice = [(s.get("metadata") or {}).get("dicom", {}) for s in m["axes"][1]["samples"]]
    assert [s["SliceLocation"] for s in per_slice] == [0.0, 2.0, 4.0]
    # one instance per (t, z): no axis may claim an instance's identity
    for d in per_time + per_slice:
        assert "SOPInstanceUID" not in d and "InstanceNumber" not in d


def test_the_split_rule():
    per = [{"A": 1, "Z": z, "T": t, "B": 10 * t + z} for t in range(2) for z in range(3)]
    per_time, per_slice = split_time_and_slice(per, 2, 3)
    assert per_time == [{"T": 0}, {"T": 1}]
    assert per_slice == [{"A": 1, "Z": 0}, {"A": 1, "Z": 1}, {"A": 1, "Z": 2}]
