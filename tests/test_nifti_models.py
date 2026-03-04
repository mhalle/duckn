"""Tests for NIfTI provenance extension Pydantic models.

Validates that each JSON example from §7 of the nifti-spec
can be parsed and round-tripped through model_dump(exclude_none=True).
"""

from __future__ import annotations

import pytest

from duckn.models import NiftiExtension, NiftiTags


# -- §7.1 Structural MRI (simple case) ----------------------------------------

EXAMPLE_7_1 = {
    "version": "1.0",
    "nifti_version": 1,
    "tags": {
        "descrip": "FreeSurfer recon-all",
    },
}

# -- §7.2 Statistical map with intent parameters ------------------------------

EXAMPLE_7_2 = {
    "version": "1.0",
    "nifti_version": 1,
    "tags": {
        "sform_code": 4,
        "intent": {
            "code": 3,
            "name": "ttest",
            "p1": 42.0,
        },
        "cal": {
            "min": -8.0,
            "max": 8.0,
        },
        "descrip": "SPM{T_[42.0]} - contrast 1",
    },
}

# -- §7.3 fMRI time series with full acquisition metadata ----------------------

EXAMPLE_7_3 = {
    "version": "1.0",
    "nifti_version": 1,
    "tags": {
        "sform_code": 1,
        "qform_code": 1,
        "dim_info": {
            "freq_dim": 1,
            "phase_dim": 2,
            "slice_dim": 3,
        },
        "slice_timing": {
            "code": "alternating-increasing",
            "start": 0,
            "end": 35,
            "duration": 0.0556,
        },
        "toffset": 0.0,
        "cal": {
            "min": 0.0,
            "max": 32000.0,
        },
        "descrip": "EPI BOLD 3mm iso TR=2s",
    },
    "legacy": {
        "tags": {
            "sform": [
                [3.0, 0.0, 0.0, -94.5],
                [0.0, 3.0, 0.0, -130.5],
                [0.0, 0.0, 3.0, -72.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "qform": [
                [3.0, 0.0, 0.0, -94.5],
                [0.0, 3.0, 0.0, -130.5],
                [0.0, 0.0, 3.0, -72.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
        },
    },
}

# -- §7.4 Diffusion tensor from NIfTI -----------------------------------------

EXAMPLE_7_4 = {
    "version": "1.0",
    "nifti_version": 1,
    "tags": {
        "intent": {
            "code": 1005,
            "name": "symmatrix",
        },
    },
}

# -- §7.5 Minimal (just version + nifti_version) ------------------------------

EXAMPLE_7_5 = {
    "version": "1.0",
    "nifti_version": 1,
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
    model = NiftiExtension(**data)
    dumped = model.model_dump(exclude_none=True)
    assert dumped == data


def test_extra_field_rejected() -> None:
    """Extra fields should be rejected (extra='forbid')."""
    with pytest.raises(Exception):
        NiftiExtension(version="1.0", bogus="nope")


def test_missing_required_field() -> None:
    """Missing required 'version' field should raise."""
    with pytest.raises(Exception):
        NiftiExtension(nifti_version=1)


def test_tags_extra_field_rejected() -> None:
    """Extra fields inside tags should be rejected."""
    with pytest.raises(Exception):
        NiftiTags(bogus="nope")


def test_qform_code_preserved() -> None:
    """qform_code stored as simple integer in tags."""
    tags = NiftiTags(qform_code=1)
    dumped = tags.model_dump(exclude_none=True)
    assert dumped == {"qform_code": 1}


def test_qform_code_with_sform_code() -> None:
    """Both sform_code and qform_code can coexist."""
    tags = NiftiTags(sform_code=4, qform_code=1)
    dumped = tags.model_dump(exclude_none=True)
    assert dumped == {"sform_code": 4, "qform_code": 1}


def test_minimal_extension() -> None:
    """Minimal extension with just version and nifti_version."""
    ext = NiftiExtension(version="1.0", nifti_version=1)
    dumped = ext.model_dump(exclude_none=True)
    assert dumped == {"version": "1.0", "nifti_version": 1}


def test_tags_all_optional() -> None:
    """Empty tags is valid."""
    tags = NiftiTags()
    dumped = tags.model_dump(exclude_none=True)
    assert dumped == {}


def test_extension_with_url() -> None:
    """Extension with optional url field."""
    ext = NiftiExtension(
        version="1.0",
        url="https://nifti.nimh.nih.gov",
        nifti_version=1,
    )
    dumped = ext.model_dump(exclude_none=True)
    assert dumped["url"] == "https://nifti.nimh.nih.gov"
