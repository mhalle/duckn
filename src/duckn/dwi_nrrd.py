"""Parse NRRD DWMRI key/value pairs into DwmriExtension / DwmriAxisExtension."""

from __future__ import annotations

import re
from typing import Any

from .models import DwmriAxisExtension, DwmriExtension

_GRADIENT_RE = re.compile(r"^DWMRI_gradient_(\d+)$")
_BMATRIX_RE = re.compile(r"^DWMRI_B-matrix_(\d+)$")
_NEX_RE = re.compile(r"^DWMRI_NEX_(\d+)$")


def parse_dwi_keyvalues(
    keyvalues: dict[str, str],
) -> tuple[DwmriExtension | None, DwmriAxisExtension | None, dict[str, str]]:
    """Parse DWI key/value pairs into extension models.

    Parameters
    ----------
    keyvalues:
        Non-spec key/value pairs from an NRRD header.

    Returns
    -------
    (top_level_ext, per_axis_ext, remaining)
        The parsed DwmriExtension and DwmriAxisExtension (or None if no DWI
        keys found) and a dict of remaining non-DWI key/value pairs.
    """
    # Detect: modality==DWMRI or any DWMRI_ key
    has_modality = keyvalues.get("modality") == "DWMRI"
    has_dwmri_keys = any(k.startswith("DWMRI_") for k in keyvalues)

    if not has_modality and not has_dwmri_keys:
        return None, None, keyvalues

    # Partition keys into consumed vs remaining
    consumed: dict[str, str] = {}
    remaining: dict[str, str] = {}
    for key, val in keyvalues.items():
        if key == "modality" and val == "DWMRI":
            consumed[key] = val
        elif key.startswith("DWMRI_"):
            consumed[key] = val
        else:
            remaining[key] = val

    # --- Top-level fields ---
    b_value_raw = consumed.get("DWMRI_b-value")
    if b_value_raw is None:
        # No b-value means we can't build a valid extension
        return None, None, keyvalues

    ext_kwargs: dict[str, Any] = {
        "version": "1.0",
        "b_value": float(b_value_raw),
    }

    # --- Per-axis: gradients ---
    gradients: dict[int, list[float]] = {}
    for key, val in consumed.items():
        m = _GRADIENT_RE.match(key)
        if m:
            idx = int(m.group(1))
            gradients[idx] = [float(x) for x in val.split()]

    # --- Per-axis: B-matrices ---
    b_matrices: dict[int, list[float]] = {}
    for key, val in consumed.items():
        m = _BMATRIX_RE.match(key)
        if m:
            idx = int(m.group(1))
            b_matrices[idx] = [float(x) for x in val.split()]

    # --- Per-axis: NEX ---
    nex: dict[str, int] = {}
    for key, val in consumed.items():
        m = _NEX_RE.match(key)
        if m:
            idx_str = m.group(1)
            nex[idx_str] = int(val)

    # Build per-axis extension
    axis_kwargs: dict[str, Any] = {}
    if gradients:
        sorted_indices = sorted(gradients.keys())
        axis_kwargs["gradients"] = [gradients[i] for i in sorted_indices]
    if b_matrices:
        sorted_indices = sorted(b_matrices.keys())
        axis_kwargs["b_matrices"] = [b_matrices[i] for i in sorted_indices]
    if nex:
        axis_kwargs["nex"] = nex

    axis_ext = DwmriAxisExtension(**axis_kwargs) if axis_kwargs else None

    # --- Legacy: stash original key/value strings for lossless back-conversion ---
    ext_kwargs["legacy"] = {"keyvalues": consumed}

    top_ext = DwmriExtension(**ext_kwargs)

    return top_ext, axis_ext, remaining


# ---------------------------------------------------------------------------
# Reverse: DwmriExtension + DwmriAxisExtension → flat NRRD key/value pairs
# ---------------------------------------------------------------------------


def _generate_from_model(
    ext: DwmriExtension,
    axis_ext: DwmriAxisExtension,
) -> dict[str, str]:
    """Generate flat key/value pairs from model data (no legacy)."""
    kv: dict[str, str] = {}

    kv["modality"] = "DWMRI"
    kv["DWMRI_b-value"] = str(ext.b_value)

    if axis_ext.gradients is not None:
        for i, grad in enumerate(axis_ext.gradients):
            key = f"DWMRI_gradient_{i:04d}"
            kv[key] = " ".join(str(x) for x in grad)

    if axis_ext.b_matrices is not None:
        for i, bmat in enumerate(axis_ext.b_matrices):
            key = f"DWMRI_B-matrix_{i:04d}"
            kv[key] = " ".join(str(x) for x in bmat)

    if axis_ext.nex is not None:
        for idx_str, count in axis_ext.nex.items():
            key = f"DWMRI_NEX_{int(idx_str):04d}"
            kv[key] = str(count)

    return kv


def serialize_dwi_extension(
    ext: DwmriExtension,
    axis_ext: DwmriAxisExtension,
) -> dict[str, str]:
    """Convert DWI extensions back to flat NRRD key/value pairs.

    Uses the same legacy replay pattern as seg_nrrd: if the model is
    unchanged from what was parsed, replay original strings; otherwise
    generate fresh.
    """
    generated = _generate_from_model(ext, axis_ext)

    legacy_kv: dict[str, str] = {}
    if ext.legacy and "keyvalues" in ext.legacy:
        legacy_kv = ext.legacy["keyvalues"]

    if not legacy_kv:
        return generated

    # Re-parse legacy to see if it still matches the current model
    try:
        legacy_top, legacy_axis, _ = parse_dwi_keyvalues(legacy_kv)
    except Exception:
        return generated

    if legacy_top is None:
        return generated

    # Compare model data (excluding legacy) to check equivalence
    current_top_dump = ext.model_dump(exclude={"legacy"}, exclude_none=True)
    legacy_top_dump = legacy_top.model_dump(exclude={"legacy"}, exclude_none=True)

    current_axis_dump = axis_ext.model_dump(exclude_none=True)
    legacy_axis_dump = legacy_axis.model_dump(exclude_none=True) if legacy_axis else {}

    if current_top_dump != legacy_top_dump or current_axis_dump != legacy_axis_dump:
        # Model was modified — generate fresh
        return generated

    # Model unchanged — replay original strings
    result: dict[str, str] = {}
    for key, val in generated.items():
        result[key] = legacy_kv.get(key, val)
    return result
