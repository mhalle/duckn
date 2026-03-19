"""Generate BIDS JSON sidecar files from duckn metadata.

Maps duckn convention fields and DICOM extension tags to BIDS-compliant
sidecar JSON for MRI data. Handles unit conversions (DICOM ms → BIDS s)
and field renaming.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import DucknMetadata


# ---------------------------------------------------------------------------
# DICOM tag → BIDS field mapping
# ---------------------------------------------------------------------------

# Direct mappings: DICOM keyword → BIDS field name
# Values are copied as-is (no unit conversion)
_DIRECT_MAPPINGS: dict[str, str] = {
    "Manufacturer": "Manufacturer",
    "ManufacturerModelName": "ManufacturersModelName",
    "DeviceSerialNumber": "DeviceSerialNumber",
    "StationName": "StationName",
    "SoftwareVersions": "SoftwareVersions",
    "MagneticFieldStrength": "MagneticFieldStrength",
    "ReceiveCoilName": "ReceiveCoilName",
    "TransmitCoilName": "TransmitCoilName",
    "ScanningSequence": "ScanningSequence",
    "SequenceVariant": "SequenceVariant",
    "ScanOptions": "ScanOptions",
    "SequenceName": "SequenceName",
    "MRAcquisitionType": "MRAcquisitionType",
    "SeriesDescription": "SeriesDescription",
    "ProtocolName": "ProtocolName",
    "ImageType": "ImageType",
    "BodyPartExamined": "BodyPartExamined",
    "PatientPosition": "PatientPosition",
    "NumberOfAverages": "NumberOfAverages",
    "PixelBandwidth": "PixelBandwidth",
    "NumberOfPhaseEncodingSteps": "NumberOfPhaseEncodingSteps",
    "PercentPhaseFieldOfView": "PercentPhaseFieldOfView",
    "PercentSampling": "PercentSampling",
    "EchoTrainLength": "EchoTrainLength",
    "FlipAngle": "FlipAngle",
    "SAR": "SAR",
    "ImagingFrequency": "ImagingFrequency",
    "ImagedNucleus": "ImagedNucleus",
    "PatientWeight": "PatientWeight",
}

# Mappings that require ms → s conversion
_MS_TO_S_MAPPINGS: dict[str, str] = {
    "RepetitionTime": "RepetitionTime",
    "EchoTime": "EchoTime",
    "InversionTime": "InversionTime",
}

# DICOM InPlanePhaseEncodingDirection → BIDS PhaseEncodingDirection
_PHASE_DIR_MAP: dict[str, str] = {
    "COL": "j",
    "ROW": "i",
}


def _get_spacing_and_thickness(meta: DucknMetadata) -> dict[str, Any]:
    """Extract spatial parameters from duckn axes."""
    result: dict[str, Any] = {}

    if meta.axes:
        # Slice thickness from first spatial axis
        for ax in meta.axes:
            if ax.thickness is not None:
                result["SliceThickness"] = ax.thickness
                break

        # Pixel spacing from space_direction magnitudes
        spatial_dirs = [ax for ax in meta.axes if ax.space_direction is not None]
        if len(spatial_dirs) >= 3:
            import math

            spacings = []
            for ax in spatial_dirs:
                mag = math.sqrt(sum(v * v for v in ax.space_direction))
                spacings.append(mag)
            # In-plane resolution (last two spatial axes in C-order)
            if len(spacings) >= 2:
                result["PixelSpacing"] = [spacings[-1], spacings[-2]]

    return result


def duckn_to_bids_sidecar(
    meta: DucknMetadata,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a BIDS-compliant JSON sidecar from duckn metadata.

    Parameters
    ----------
    meta : DucknMetadata from a duckn store
    output_path : if provided, write the sidecar to this path

    Returns
    -------
    dict : the BIDS sidecar as a Python dict
    """
    sidecar: dict[str, Any] = {}

    # Get DICOM tags if available
    tags: dict[str, Any] = {}
    if meta.extensions and "dicom" in meta.extensions:
        dicom_ext = meta.extensions["dicom"]
        tags = dicom_ext.get("tags", {})

    # Direct mappings
    for dicom_key, bids_key in _DIRECT_MAPPINGS.items():
        val = tags.get(dicom_key)
        if val is not None:
            sidecar[bids_key] = val

    # Time fields: DICOM stores in ms, BIDS wants seconds
    for dicom_key, bids_key in _MS_TO_S_MAPPINGS.items():
        val = tags.get(dicom_key)
        if val is not None:
            if isinstance(val, (int, float)):
                sidecar[bids_key] = val / 1000.0
            else:
                sidecar[bids_key] = val

    # Phase encoding direction
    pe_dir = tags.get("InPlanePhaseEncodingDirection")
    if pe_dir and pe_dir in _PHASE_DIR_MAP:
        sidecar["PhaseEncodingDirection"] = _PHASE_DIR_MAP[pe_dir]

    # Spatial parameters from duckn axes
    spatial = _get_spacing_and_thickness(meta)
    sidecar.update(spatial)

    # Modality (not a BIDS field per se, but useful context)
    modality = tags.get("Modality")
    if modality:
        sidecar["Modality"] = modality

    # Institution
    for key in ("InstitutionName", "InstitutionalDepartmentName"):
        val = tags.get(key)
        if val is not None:
            sidecar[key] = val

    # Convert from duckn DWI extension if present
    if meta.extensions and "dwmri" in meta.extensions:
        dwi = meta.extensions["dwmri"]
        if "b_value" in dwi:
            sidecar["DiffusionBValue"] = dwi["b_value"]

    # Write if path provided
    if output_path is not None:
        output_path = Path(output_path)
        with open(output_path, "w") as f:
            json.dump(sidecar, f, indent=2)

    return sidecar
