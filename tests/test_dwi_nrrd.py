"""Tests for DWMRI key/value parsing into DwmriExtension / DwmriAxisExtension."""

from __future__ import annotations

from pathlib import Path

import nrrd
import pytest

from nrrdz.models import DwmriAxisExtension, DwmriExtension
from nrrdz.dwi_nrrd import parse_dwi_keyvalues, serialize_dwi_extension

DATA_DIR = Path(__file__).parent / "data" / "real-world"
DWI_NRRD = DATA_DIR / "dwi.nrrd"


# ---------------------------------------------------------------------------
# Unit tests for parse_dwi_keyvalues
# ---------------------------------------------------------------------------


def test_no_dwi_keys():
    """Non-DWI keyvalues should return None and pass through."""
    kv = {"foo": "bar", "baz": "42"}
    top, axis, remaining = parse_dwi_keyvalues(kv)
    assert top is None
    assert axis is None
    assert remaining == kv


def test_minimal_dwi():
    """Just modality + b-value + 1 gradient should parse."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000",
        "DWMRI_gradient_0000": "0 0 0",
    }
    top, axis, remaining = parse_dwi_keyvalues(kv)
    assert top is not None
    assert top.version == "1.0"
    assert top.b_value == 1000.0
    assert axis is not None
    assert axis.gradients == [[0.0, 0.0, 0.0]]
    assert remaining == {}


def test_gradient_ordering():
    """Gradient indices should be parsed and sorted correctly."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000",
        "DWMRI_gradient_0002": "0 0 1",
        "DWMRI_gradient_0000": "0 0 0",
        "DWMRI_gradient_0001": "1 0 0",
    }
    top, axis, remaining = parse_dwi_keyvalues(kv)
    assert axis.gradients == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]


def test_b_matrix_parsing():
    """B-matrix keys should parse correctly."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000",
        "DWMRI_B-matrix_0000": "0 0 0 0 0 0",
        "DWMRI_B-matrix_0001": "0.5 0.0 0.5 0.0 0.0 0.5",
    }
    top, axis, remaining = parse_dwi_keyvalues(kv)
    assert axis.b_matrices is not None
    assert len(axis.b_matrices) == 2
    assert axis.b_matrices[0] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert axis.b_matrices[1] == [0.5, 0.0, 0.5, 0.0, 0.0, 0.5]
    assert axis.gradients is None


def test_nex_parsing():
    """NEX keys should parse correctly."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "800",
        "DWMRI_gradient_0000": "0 0 0",
        "DWMRI_gradient_0001": "0 0 0",
        "DWMRI_gradient_0002": "1 0 0",
        "DWMRI_NEX_0000": "2",
    }
    top, axis, remaining = parse_dwi_keyvalues(kv)
    assert axis.nex == {"0000": 2}


def test_remaining_preserved():
    """Non-DWMRI keys should be in remaining dict."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000",
        "DWMRI_gradient_0000": "0 0 0",
        "CustomKey": "custom_value",
        "AnotherKey": "42",
    }
    top, axis, remaining = parse_dwi_keyvalues(kv)
    assert top is not None
    assert remaining == {"CustomKey": "custom_value", "AnotherKey": "42"}


def test_modality_not_dwmri_passes_through():
    """modality key with non-DWMRI value should pass through."""
    kv = {"modality": "MRI", "foo": "bar"}
    top, axis, remaining = parse_dwi_keyvalues(kv)
    assert top is None
    assert remaining == kv


# ---------------------------------------------------------------------------
# Integration tests against real-world dwi.nrrd
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not DWI_NRRD.exists(), reason="dwi.nrrd not found")
def test_real_world_parses():
    """Parse dwi.nrrd, verify 43 gradients and b_value=1000."""
    header = nrrd.read_header(str(DWI_NRRD))
    keyvalues: dict[str, str] = {}
    for k, v in header.items():
        if k not in {
            "type", "dimension", "space dimension", "space", "sizes",
            "space directions", "kinds", "endian", "encoding",
            "centerings", "labels", "units", "space units",
            "space origin", "measurement frame",
            "thicknesses", "spacings", "content", "sample units",
        }:
            keyvalues[k] = str(v)

    top, axis, remaining = parse_dwi_keyvalues(keyvalues)
    assert top is not None
    assert top.b_value == 1000.0
    assert axis is not None
    assert axis.gradients is not None
    assert len(axis.gradients) == 43
    # First gradient should be zero (baseline)
    assert axis.gradients[0] == [0.0, 0.0, 0.0]
    # No DWMRI keys should remain
    for key in remaining:
        assert not key.startswith("DWMRI_"), f"DWMRI key leaked: {key}"
        assert not (key == "modality"), f"modality key leaked"


@pytest.mark.skipif(not DWI_NRRD.exists(), reason="dwi.nrrd not found")
def test_real_world_gradient_values():
    """Spot-check specific gradient values against known data from dwi.nrrd."""
    header = nrrd.read_header(str(DWI_NRRD))
    keyvalues: dict[str, str] = {}
    for k, v in header.items():
        if k not in {
            "type", "dimension", "space dimension", "space", "sizes",
            "space directions", "kinds", "endian", "encoding",
            "centerings", "labels", "units", "space units",
            "space origin", "measurement frame",
            "thicknesses", "spacings", "content", "sample units",
        }:
            keyvalues[k] = str(v)

    top, axis, _ = parse_dwi_keyvalues(keyvalues)

    # gradient_0001: -0.214810 -0.476289 -0.852644
    assert axis.gradients[1] == pytest.approx([-0.214810, -0.476289, -0.852644])
    # gradient_0010: -0.282122 0.820561 -0.497074
    assert axis.gradients[10] == pytest.approx([-0.282122, 0.820561, -0.497074])
    # gradient_0042 (last): -0.963999 0.251698 0.085713
    assert axis.gradients[42] == pytest.approx([-0.963999, 0.251698, 0.085713])
    # Each gradient should be a 3-component vector
    for i, g in enumerate(axis.gradients):
        assert len(g) == 3, f"gradient[{i}] has {len(g)} components, expected 3"


@pytest.mark.skipif(not DWI_NRRD.exists(), reason="dwi.nrrd not found")
def test_real_world_round_trip():
    """Parse -> serialize -> re-parse should preserve structure."""
    header = nrrd.read_header(str(DWI_NRRD))
    keyvalues: dict[str, str] = {}
    for k, v in header.items():
        if k not in {
            "type", "dimension", "space dimension", "space", "sizes",
            "space directions", "kinds", "endian", "encoding",
            "centerings", "labels", "units", "space units",
            "space origin", "measurement frame",
            "thicknesses", "spacings", "content", "sample units",
        }:
            keyvalues[k] = str(v)

    top1, axis1, _ = parse_dwi_keyvalues(keyvalues)
    flat = serialize_dwi_extension(top1, axis1)
    top2, axis2, remaining2 = parse_dwi_keyvalues(flat)

    assert top2.b_value == top1.b_value
    assert len(axis2.gradients) == len(axis1.gradients)
    assert axis2.gradients == axis1.gradients
    assert not remaining2


@pytest.mark.skipif(not DWI_NRRD.exists(), reason="dwi.nrrd not found")
def test_real_world_byte_exact_with_legacy():
    """Unmodified model should replay original strings byte-exactly."""
    header = nrrd.read_header(str(DWI_NRRD))
    keyvalues: dict[str, str] = {}
    for k, v in header.items():
        if k not in {
            "type", "dimension", "space dimension", "space", "sizes",
            "space directions", "kinds", "endian", "encoding",
            "centerings", "labels", "units", "space units",
            "space origin", "measurement frame",
            "thicknesses", "spacings", "content", "sample units",
        }:
            keyvalues[k] = str(v)

    top, axis, _ = parse_dwi_keyvalues(keyvalues)
    flat = serialize_dwi_extension(top, axis)

    # Every consumed DWI key should come back byte-exact
    for key in keyvalues:
        if key == "modality" or key.startswith("DWMRI_"):
            assert flat.get(key) == keyvalues[key], (
                f"{key} mismatch:\n  orig: {keyvalues[key]!r}\n  got:  {flat.get(key)!r}"
            )


def test_modified_model_generates_fresh():
    """Changed model should ignore legacy and generate fresh strings."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000.000000",
        "DWMRI_gradient_0000": "0.000000 0.000000 0.000000",
        "DWMRI_gradient_0001": "1.000000 0.000000 0.000000",
    }
    top, axis, _ = parse_dwi_keyvalues(kv)

    # Modify the b_value
    top.b_value = 2000.0

    flat = serialize_dwi_extension(top, axis)
    # Should use generated value, not legacy
    assert flat["DWMRI_b-value"] == "2000.0"


def test_modified_axis_generates_fresh():
    """Changed axis model should ignore legacy and generate fresh strings."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000.000000",
        "DWMRI_gradient_0000": "0.000000 0.000000 0.000000",
        "DWMRI_gradient_0001": "1.000000 0.000000 0.000000",
    }
    top, axis, _ = parse_dwi_keyvalues(kv)

    # Modify gradients
    axis.gradients[1] = [0.0, 1.0, 0.0]

    flat = serialize_dwi_extension(top, axis)
    # Should use generated value, not legacy
    assert flat["DWMRI_gradient_0001"] == "0.0 1.0 0.0"


# ---------------------------------------------------------------------------
# Serializer unit tests
# ---------------------------------------------------------------------------


def test_serialize_minimal():
    """Minimal extension should serialize and re-parse."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000",
        "DWMRI_gradient_0000": "0 0 0",
    }
    top, axis, _ = parse_dwi_keyvalues(kv)
    flat = serialize_dwi_extension(top, axis)
    assert flat["modality"] == "DWMRI"
    assert "DWMRI_b-value" in flat
    assert "DWMRI_gradient_0000" in flat

    top2, axis2, _ = parse_dwi_keyvalues(flat)
    assert top2.b_value == 1000.0
    assert axis2.gradients == [[0.0, 0.0, 0.0]]


def test_serialize_multiple_gradients():
    """Multiple gradients should use 4-digit zero-padded indices."""
    top = DwmriExtension(version="1.0", b_value=1000.0)
    axis = DwmriAxisExtension(
        gradients=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    )
    flat = serialize_dwi_extension(top, axis)
    assert "DWMRI_gradient_0000" in flat
    assert "DWMRI_gradient_0001" in flat
    assert "DWMRI_gradient_0002" in flat


def test_serialize_b_matrices():
    """B-matrix serialization should work correctly."""
    top = DwmriExtension(version="1.0", b_value=1000.0)
    axis = DwmriAxisExtension(
        b_matrices=[[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], [0.5, 0.0, 0.5, 0.0, 0.0, 0.5]]
    )
    flat = serialize_dwi_extension(top, axis)
    assert "DWMRI_B-matrix_0000" in flat
    assert "DWMRI_B-matrix_0001" in flat

    top2, axis2, _ = parse_dwi_keyvalues(flat)
    assert axis2.b_matrices == axis.b_matrices


def test_serialize_nex():
    """NEX serialization should round-trip."""
    top = DwmriExtension(version="1.0", b_value=800.0)
    axis = DwmriAxisExtension(
        gradients=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        nex={"0": 2},
    )
    flat = serialize_dwi_extension(top, axis)
    assert "DWMRI_NEX_0000" in flat
    assert flat["DWMRI_NEX_0000"] == "2"


def test_model_validates():
    """Parsed extension should validate against models and round-trip through dump."""
    kv = {
        "modality": "DWMRI",
        "DWMRI_b-value": "1000",
        "DWMRI_gradient_0000": "0 0 0",
        "DWMRI_gradient_0001": "1 0 0",
        "DWMRI_gradient_0002": "0 1 0",
    }
    top, axis, _ = parse_dwi_keyvalues(kv)
    # Round-trip through model_dump -> model construction
    top_dumped = top.model_dump(exclude_none=True)
    axis_dumped = axis.model_dump(exclude_none=True)
    top_reloaded = DwmriExtension(**top_dumped)
    axis_reloaded = DwmriAxisExtension(**axis_dumped)
    assert top_reloaded.b_value == 1000.0
    assert axis_reloaded.gradients == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


# ---------------------------------------------------------------------------
# convert.py integration tests: nrrd_to_zarr -> zarr_to_nrrd round-trip
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not DWI_NRRD.exists(), reason="dwi.nrrd not found")
def test_nrrd_to_zarr_dwi_metadata(tmp_path):
    """nrrd_to_zarr should produce extensions.dwmri and per-axis gradients."""
    from nrrdz.convert import nrrd_to_zarr
    import zarr

    zarr_path = tmp_path / "dwi.zarr"
    nrrd_to_zarr(DWI_NRRD, zarr_path)

    store = zarr.storage.LocalStore(str(zarr_path))
    arr = zarr.open_array(store, mode="r")
    nrrd_attrs = arr.attrs["nrrd"]

    # Top-level extension must exist
    assert "dwmri" in nrrd_attrs["extensions"]
    dwmri_top = nrrd_attrs["extensions"]["dwmri"]
    assert dwmri_top["b_value"] == 1000.0
    assert dwmri_top["version"] == "1.0"

    # No DWMRI keys in keyvalues
    kv = nrrd_attrs["extensions"].get("keyvalues", {})
    for k in kv:
        assert not k.startswith("DWMRI_"), f"DWMRI key leaked to keyvalues: {k}"
        assert k != "modality", "modality key leaked to keyvalues"

    # Per-axis extension must be on the list axis
    axes = nrrd_attrs["axes"]
    list_axes = [a for a in axes if a.get("kind") == "list"]
    assert len(list_axes) == 1, f"Expected 1 list axis, got {len(list_axes)}"
    ax_dwmri = list_axes[0]["extensions"]["dwmri"]
    gradients = ax_dwmri["gradients"]
    assert len(gradients) == 43

    # Spot-check gradient values
    assert gradients[0] == pytest.approx([0.0, 0.0, 0.0])
    assert gradients[1] == pytest.approx([-0.214810, -0.476289, -0.852644])
    assert gradients[42] == pytest.approx([-0.963999, 0.251698, 0.085713])


@pytest.mark.skipif(not DWI_NRRD.exists(), reason="dwi.nrrd not found")
def test_nrrd_zarr_nrrd_round_trip(tmp_path):
    """nrrd -> zarr -> nrrd should preserve DWMRI key/value pairs."""
    from nrrdz.convert import nrrd_to_zarr, zarr_to_nrrd

    zarr_path = tmp_path / "dwi.zarr"
    out_nrrd = tmp_path / "dwi_out.nrrd"

    nrrd_to_zarr(DWI_NRRD, zarr_path)
    zarr_to_nrrd(zarr_path, out_nrrd)

    # Read back the output NRRD and verify DWI keys
    header = nrrd.read_header(str(out_nrrd))

    assert header.get("modality") == "DWMRI"
    assert "DWMRI_b-value" in header

    # b-value should parse to 1000
    assert float(header["DWMRI_b-value"]) == 1000.0

    # Should have 43 gradient keys
    grad_keys = [k for k in header if k.startswith("DWMRI_gradient_")]
    assert len(grad_keys) == 43

    # Spot-check specific gradients from the original
    orig_header = nrrd.read_header(str(DWI_NRRD))
    for idx in [0, 1, 10, 42]:
        key = f"DWMRI_gradient_{idx:04d}"
        orig_vals = [float(x) for x in orig_header[key].split()]
        out_vals = [float(x) for x in header[key].split()]
        assert out_vals == pytest.approx(orig_vals), (
            f"{key} mismatch: orig={orig_vals}, out={out_vals}"
        )
