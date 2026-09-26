"""build_dicomweb_zmp's tags come from the one conversion (2026-09-26).

It was a third converter that followed none of dicom-spec: geometry and rescale kept in tags,
instance 0's InstanceNumber / SliceLocation / AcquisitionTime stated series-wide, sequences as
raw PS3.18 JSON. Driven here with the HTTP client and the ZMP builder mocked: only the metadata
it would write is under test."""
from __future__ import annotations

import sys
import types
from unittest import mock

import numpy as np
import pytest

pydicom = pytest.importorskip("pydicom")


def _instances(n=3):
    from pydicom.dataset import Dataset
    from pydicom.sequence import Sequence
    from pydicom.uid import generate_uid
    out = []
    for i in range(n):
        ds = Dataset()
        ds.SOPClassUID, ds.SOPInstanceUID = "1.2.840.10008.5.1.4.1.1.2", generate_uid()
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.Modality = "1.2.3", "1.2.3.4", "CT"
        ds.ImagePositionPatient = [0.0, 0.0, 2.0 * i]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing, ds.SliceThickness = [0.5, 0.5], 2.0
        ds.Rows = ds.Columns = 4
        ds.BitsAllocated = ds.BitsStored = 16
        ds.HighBit, ds.PixelRepresentation, ds.SamplesPerPixel = 15, 1, 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.RescaleSlope, ds.RescaleIntercept, ds.RescaleType = 1, -1024, "HU"
        ds.InstanceNumber, ds.SliceLocation = i + 1, 2.0 * i
        ds.OperatorsName = ["A^B", "C^D"]
        code = Dataset()
        code.CodeValue, code.CodingSchemeDesignator = "T-D3000", "SRT"
        ds.AnatomicRegionSequence = Sequence([code])
        out.append(ds.to_json_dict())
    return out


def test_the_dicomweb_builder_follows_the_spec(tmp_path, monkeypatch):
    import httpx

    from duckn import idc_zmp
    import duckn.models as models
    inst = _instances()

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
    except Exception:                          # the mocked builder may not complete a store
        pass
    ext = seen["extensions"]["dicom"]
    tags = ext["tags"]
    assert ext["stored_values"] is True
    assert not {"SliceThickness", "PixelSpacing", "RescaleSlope", "RescaleIntercept",
                "InstanceNumber", "SliceLocation", "SOPInstanceUID"} & tags.keys()
    assert tags["OperatorsName"] == ["A^B", "C^D"]
    assert tags["AnatomicRegionSequence"] == [{"CodeValue": "T-D3000",
                                               "CodingSchemeDesignator": "SRT"}]
    per = [(s.metadata or {}).get("dicom", {}) for s in seen["axes"][0].samples]
    assert [p["InstanceNumber"] for p in per] == [1, 2, 3]
    assert np.allclose([p["SliceLocation"] for p in per], [0, 2, 4])
