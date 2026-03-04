"""Tests for DICOM provenance extension Pydantic model.

Validates that each JSON example from §8 of the dicom-spec
can be parsed and round-tripped through model_dump(exclude_none=True, by_alias=True).
"""

from __future__ import annotations

import pytest

from duckn.models import DicomExtension


# -- §8.1 CT Volume (anonymized, full acquisition metadata) -------------------

EXAMPLE_8_1 = {
    "version": "1.0",
    "anonymized": True,
    "tags": {
        "Modality": "CT",
        "SOPClassUID": "1.2.840.10008.5.1.4.1.1.2",
        "SeriesInstanceUID": "1.2.840.113619.2.55.3.604688119.969.1069843699.84",
        "StudyDescription": "CHEST W/O CONTRAST",
        "SeriesDescription": "AXIAL 5mm",
        "BodyPartExamined": "CHEST",
        "Manufacturer": "GE MEDICAL SYSTEMS",
        "ManufacturerModelName": "LightSpeed16",
        "SoftwareVersions": ["06MW03.5"],
        "InstitutionName": None,
        "KVP": 120,
        "XRayTubeCurrent": 200,
        "ExposureTime": 570,
        "ConvolutionKernel": ["STANDARD"],
        "SliceThickness": 5.0,
        "PixelSpacing": [0.703, 0.703],
        "ReconstructionDiameter": 360.0,
        "GantryDetectorTilt": 0.0,
        "RescaleSlope": 1.0,
        "RescaleIntercept": -1024.0,
        "RescaleType": "HU",
        "PatientName": None,
        "PatientID": None,
        "PatientBirthDate": None,
        "PatientSex": "M",
        "PatientAge": "065Y",
        "StudyDate": "20040126",
        "AcquisitionDate": None,
        "WindowCenter": [40],
        "WindowWidth": [400],
    },
}

# -- §8.2 MR Brain (non-anonymized, MR-specific tags) ------------------------

EXAMPLE_8_2 = {
    "version": "1.0",
    "tags": {
        "Modality": "MR",
        "SOPClassUID": "1.2.840.10008.5.1.4.1.1.4",
        "SeriesInstanceUID": "1.2.840.113654.2.70.1.18342...",
        "StudyDescription": "BRAIN MRI",
        "SeriesDescription": "SAG T1 MPRAGE",
        "ProtocolName": "SAG T1 MPRAGE",
        "BodyPartExamined": "BRAIN",
        "Manufacturer": "SIEMENS",
        "ManufacturerModelName": "Prisma",
        "MagneticFieldStrength": 3.0,
        "InstitutionName": "Example Medical Center",
        "MRAcquisitionType": "3D",
        "ScanningSequence": ["GR", "IR"],
        "SequenceVariant": ["SK", "SP", "MP"],
        "RepetitionTime": 2300.0,
        "EchoTime": 2.98,
        "InversionTime": 900.0,
        "FlipAngle": 9.0,
        "NumberOfAverages": 1.0,
        "EchoTrainLength": 1,
        "PixelBandwidth": 240.0,
        "ReceiveCoilName": "HeadNeck_64",
        "InPlanePhaseEncodingDirection": "ROW",
        "SliceThickness": 1.0,
        "PixelSpacing": [1.0, 1.0],
        "SpacingBetweenSlices": 1.0,
        "PatientName": "Doe^John",
        "PatientID": "MRN12345",
        "PatientSex": "M",
        "PatientAge": "042Y",
        "StudyDate": "20240115",
        "FrameOfReferenceUID": "1.2.840.113654.2.70.1.18342...",
    },
}

# -- §8.3 PET/CT (nested sequences) ------------------------------------------

EXAMPLE_8_3 = {
    "version": "1.0",
    "tags": {
        "Modality": "PT",
        "SOPClassUID": "1.2.840.10008.5.1.4.1.1.128",
        "SeriesDescription": "PET WB (AC)",
        "Units": "BQML",
        "Manufacturer": "Siemens",
        "ManufacturerModelName": "Biograph Vision 600",
        "RadiopharmaceuticalInformationSequence": [
            {
                "Radiopharmaceutical": "Fluorodeoxyglucose F^18^",
                "RadionuclideTotalDose": 370000000.0,
                "RadiopharmaceuticalStartTime": "091500.000",
                "RadionuclideHalfLife": 6586.2,
                "RadionuclideCodeSequence": [
                    {
                        "CodeValue": "C-111A1",
                        "CodingSchemeDesignator": "SRT",
                        "CodeMeaning": "^18^Fluorine",
                    }
                ],
            }
        ],
        "DecayCorrection": "START",
        "AttenuationCorrectionMethod": "CT-based",
        "ReconstructionMethod": "PSF+TOF 3i21s",
        "SliceThickness": 2.0,
        "PixelSpacing": [2.0, 2.0],
        "PatientWeight": 75.0,
        "PatientSex": "F",
        "StudyDate": "20240220",
        "FrameOfReferenceUID": "1.2.840.113619.2.55.3...",
    },
}

# -- §8.4 Minimal (just Modality) --------------------------------------------

EXAMPLE_8_4 = {
    "version": "1.0",
    "tags": {
        "Modality": "CT",
    },
}

# -- §8.5 Coded Sequence Attributes ------------------------------------------

EXAMPLE_8_5 = {
    "version": "1.0",
    "tags": {
        "Modality": "CT",
        "AnatomicRegionSequence": [
            {
                "CodeValue": "T-28000",
                "CodingSchemeDesignator": "SRT",
                "CodeMeaning": "Lung",
            }
        ],
        "ProcedureCodeSequence": [
            {
                "CodeValue": "RPID5740",
                "CodingSchemeDesignator": "99ORBIS",
                "CodeMeaning": "CT Chest without contrast",
            }
        ],
    },
}


# -- Tests --------------------------------------------------------------------

EXAMPLES = [
    ("8.1", EXAMPLE_8_1),
    ("8.2", EXAMPLE_8_2),
    ("8.3", EXAMPLE_8_3),
    ("8.4", EXAMPLE_8_4),
    ("8.5", EXAMPLE_8_5),
]


@pytest.mark.parametrize("name,data", EXAMPLES, ids=[e[0] for e in EXAMPLES])
def test_spec_example_round_trip(name: str, data: dict) -> None:
    """Parse a spec example and verify model_dump round-trips cleanly."""
    model = DicomExtension(**data)
    dumped = model.model_dump(exclude_none=True, by_alias=True)
    assert dumped == data


def test_extra_field_rejected() -> None:
    """Extra fields should be rejected (extra='forbid')."""
    with pytest.raises(Exception):
        DicomExtension(version="1.0", bogus="nope")


def test_missing_required_field() -> None:
    """Missing required fields should raise."""
    with pytest.raises(Exception):
        DicomExtension(anonymized=True)  # missing version


def test_null_tags_preserved() -> None:
    """Null values inside tags survive round-trip (anonymization sentinel)."""
    data = {
        "version": "1.0",
        "anonymized": True,
        "tags": {
            "PatientName": None,
            "PatientID": None,
            "Modality": "CT",
        },
    }
    model = DicomExtension(**data)
    dumped = model.model_dump(exclude_none=True, by_alias=True)
    assert dumped["tags"]["PatientName"] is None
    assert dumped["tags"]["PatientID"] is None
    assert dumped == data


def test_nested_sequences_preserved() -> None:
    """Deeply nested DICOM sequences round-trip correctly."""
    data = {
        "version": "1.0",
        "tags": {
            "RadiopharmaceuticalInformationSequence": [
                {
                    "Radiopharmaceutical": "FDG",
                    "RadionuclideCodeSequence": [
                        {
                            "CodeValue": "C-111A1",
                            "CodingSchemeDesignator": "SRT",
                            "CodeMeaning": "^18^Fluorine",
                        }
                    ],
                }
            ],
        },
    }
    model = DicomExtension(**data)
    dumped = model.model_dump(exclude_none=True, by_alias=True)
    seq = dumped["tags"]["RadiopharmaceuticalInformationSequence"]
    assert len(seq) == 1
    assert len(seq[0]["RadionuclideCodeSequence"]) == 1
    assert seq[0]["RadionuclideCodeSequence"][0]["CodeValue"] == "C-111A1"
    assert dumped == data
