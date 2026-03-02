"""Smoke tests for segmentation extension Pydantic models.

Validates that each JSON example from §7 of the segmentation-ext-spec
can be parsed and round-tripped through model_dump(exclude_none=True).
"""

from __future__ import annotations

import pytest

from nrrdz.models import SegmentationExtension


# -- §7.1 Non-Overlapping Labelmap with Multi-Ontology Designations ----------

EXAMPLE_7_1 = {
    "version": "1.0",
    "source_representation": "binary-labelmap",
    "contained_representations": ["binary-labelmap", "closed-surface"],
    "terminologies": {
        "SCT": {
            "name": "SNOMED Clinical Terms",
            "version": "2024-09-01",
            "url": "https://browser.ihtsdotools.org",
        },
        "FMA": {
            "name": "Foundational Model of Anatomy",
            "url": "http://purl.org/sig/ont/fma/",
        },
        "TA2": {
            "name": "Terminologia Anatomica 2nd Edition",
            "url": "https://ta2viewer.openanatomy.org",
        },
    },
    "segments": [
        {
            "id": "Segment_1",
            "name": "Right kidney",
            "label_value": 1,
            "color": [0.89, 0.85, 0.78],
            "extent": [90, 170, 100, 180, 40, 100],
            "designations": [
                {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                    "url": "https://browser.ihtsdotools.org/?perspective=full&conceptId1=64033007",
                    "modifier": {
                        "scheme": "SCT",
                        "code": "24028007",
                        "meaning": "Right",
                    },
                },
                {
                    "scheme": "FMA",
                    "code": "7205",
                    "meaning": "Right kidney",
                    "url": "http://purl.org/sig/ont/fma/fma7205",
                },
                {
                    "scheme": "TA2",
                    "code": "5767",
                    "meaning": "Right kidney",
                    "display": {"la": "Ren dexter", "en": "Right kidney"},
                },
            ],
            "dicom": {
                "category": {
                    "scheme": "SCT",
                    "code": "123037004",
                    "meaning": "Body structure",
                },
                "type": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region_modifier": {
                    "scheme": "SCT",
                    "code": "24028007",
                    "meaning": "Right",
                },
            },
        },
        {
            "id": "Segment_2",
            "name": "Left kidney",
            "label_value": 2,
            "color": [0.90, 0.82, 0.72],
            "extent": [85, 165, 60, 140, 38, 98],
            "designations": [
                {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                    "modifier": {
                        "scheme": "SCT",
                        "code": "7771000",
                        "meaning": "Left",
                    },
                },
                {
                    "scheme": "FMA",
                    "code": "7204",
                    "meaning": "Left kidney",
                    "url": "http://purl.org/sig/ont/fma/fma7204",
                },
                {
                    "scheme": "TA2",
                    "code": "5766",
                    "meaning": "Left kidney",
                    "display": {"la": "Ren sinister", "en": "Left kidney"},
                },
            ],
            "dicom": {
                "category": {
                    "scheme": "SCT",
                    "code": "123037004",
                    "meaning": "Body structure",
                },
                "type": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region": {
                    "scheme": "SCT",
                    "code": "64033007",
                    "meaning": "Kidney",
                },
                "anatomic_region_modifier": {
                    "scheme": "SCT",
                    "code": "7771000",
                    "meaning": "Left",
                },
            },
        },
    ],
}

# -- §7.2 Overlapping Segments with Layers -----------------------------------

EXAMPLE_7_2 = {
    "version": "1.0",
    "source_representation": "binary-labelmap",
    "segments": [
        {
            "id": "Segment_1",
            "name": "Tumor",
            "label_value": 1,
            "layer": 0,
            "color": [0.8, 0.2, 0.2],
            "designations": [
                {"scheme": "SCT", "code": "108369006", "meaning": "Neoplasm"}
            ],
        },
        {
            "id": "Segment_2",
            "name": "Liver",
            "label_value": 1,
            "layer": 1,
            "color": [0.2, 0.6, 0.8],
            "designations": [
                {"scheme": "SCT", "code": "10200004", "meaning": "Liver"}
            ],
        },
    ],
}

# -- §7.3 Overlapping Segments with Label Unions ------------------------------

EXAMPLE_7_3 = {
    "version": "1.0",
    "source_representation": "binary-labelmap",
    "segments": [
        {
            "id": "Segment_1",
            "name": "Tumor",
            "label_value": [2, 3],
            "color": [0.8, 0.2, 0.2],
            "designations": [
                {"scheme": "SCT", "code": "108369006", "meaning": "Neoplasm"}
            ],
        },
        {
            "id": "Segment_2",
            "name": "Liver",
            "label_value": [1, 3],
            "color": [0.2, 0.6, 0.8],
            "designations": [
                {"scheme": "SCT", "code": "10200004", "meaning": "Liver"}
            ],
        },
    ],
}

# -- §7.4 Research Segmentation Without DICOM ---------------------------------

EXAMPLE_7_4 = {
    "version": "1.0",
    "source_representation": "binary-labelmap",
    "terminologies": {
        "FMA": {
            "name": "Foundational Model of Anatomy",
            "url": "http://purl.org/sig/ont/fma/",
        }
    },
    "segments": [
        {
            "id": "S1",
            "name": "Left ventricle",
            "label_value": 1,
            "designations": [
                {
                    "scheme": "FMA",
                    "code": "7101",
                    "meaning": "Left ventricle",
                    "url": "http://purl.org/sig/ont/fma/fma7101",
                }
            ],
        },
        {
            "id": "S2",
            "name": "Right ventricle",
            "label_value": 2,
            "designations": [
                {
                    "scheme": "FMA",
                    "code": "7098",
                    "meaning": "Right ventricle",
                    "url": "http://purl.org/sig/ont/fma/fma7098",
                }
            ],
        },
    ],
}

# -- §7.5 Minimal ------------------------------------------------------------

EXAMPLE_7_5 = {
    "version": "1.0",
    "segments": [
        {"id": "S1", "label_value": 1, "name": "Liver"},
        {"id": "S2", "label_value": 2, "name": "Spleen"},
    ],
}


# -- Tests --------------------------------------------------------------------

EXAMPLES = [
    ("7.1", EXAMPLE_7_1),
    ("7.2", EXAMPLE_7_2),
    ("7.3", EXAMPLE_7_3),
    ("7.4", EXAMPLE_7_4),
    ("7.5", EXAMPLE_7_5),
]


@pytest.mark.parametrize("name,data", EXAMPLES, ids=[e[0] for e in EXAMPLES])
def test_spec_example_round_trip(name: str, data: dict) -> None:
    """Parse a spec example and verify model_dump round-trips cleanly."""
    model = SegmentationExtension(**data)
    dumped = model.model_dump(exclude_none=True)
    assert dumped == data


def test_extra_field_rejected() -> None:
    """Extra fields should be rejected (extra='forbid')."""
    with pytest.raises(Exception):
        SegmentationExtension(
            version="1.0",
            segments=[{"id": "S1", "label_value": 1}],
            bogus="nope",
        )


def test_missing_required_field() -> None:
    """Missing required fields should raise."""
    with pytest.raises(Exception):
        SegmentationExtension(version="1.0")  # missing segments
