"""IDC-specific utilities for building patient-level ZMP hierarchies.

Provides support functions for:
- Querying the IDC index for patient data
- Normalizing CT reconstruction kernel names
- Resolving SEG/SR → CT series references
- Parsing DICOM Structured Reports into DataFrames
- Downloading DICOM files from IDC S3
- Building zarr group entries in ZMP manifests
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Kernel normalization
# ---------------------------------------------------------------------------

# Vendor-specific kernel → normalized name
_KERNEL_MAP: dict[str, str] = {
    # GE
    "STANDARD": "standard",
    "LUNG": "lung",
    "BONE": "bone",
    "BONEPLUS": "bone",
    "SOFT": "soft",
    "DETAIL": "detail",
    "EDGE": "edge",
    "EXPERIMENTAL7": "experimental",
    # Toshiba / Canon
    "FC01": "standard",
    "FC02": "standard",
    "FC03": "standard",
    "FC10": "soft",
    "FC12": "soft",
    "FC13": "soft",
    "FC30": "medium",
    "FC50": "lung",
    "FC51": "lung",
    "FC52": "lung",
    "FC55": "lung",
    "FC56": "lung",
    "FL01": "lung",
    "FL02": "lung",
    "FB30": "bone",
    "FC80": "bone",
    "FC81": "bone",
    # Siemens
    "B10f": "soft",
    "B20f": "soft",
    "B30f": "standard",
    "B31f": "standard",
    "B35f": "standard",
    "B40f": "standard",
    "B41f": "standard",
    "B45f": "medium",
    "B46f": "medium",
    "B50f": "medium",
    "B60f": "sharp",
    "B70f": "lung",
    "B75f": "lung",
    "B80f": "bone",
    "Br36": "standard",
    "Br40": "standard",
    "Br56": "lung",
    "Br69": "bone",
    # Philips
    "A": "soft",
    "B": "standard",
    "C": "sharp",
    "D": "detail",
    "L": "lung",
    "YA": "soft",
    "YB": "standard",
}


def normalize_kernel(series_description: str) -> str:
    """Extract and normalize the CT reconstruction kernel from a series description.

    Parses vendor-specific kernel names from NLST-style series descriptions
    (e.g., "2,OPA,TO,AQUL4,FC01,359.4,2,120,80,na,na") and maps them to
    normalized names (standard, lung, bone, etc.).

    Falls back to the raw kernel string (lowercased) if not in the map,
    or "unknown" if no kernel can be extracted.
    """
    parts = series_description.split(",")

    # NLST format: timepoint,protocol,vendor,scanner,kernel,...
    if len(parts) >= 5:
        kernel = parts[4].strip()
        normalized = _KERNEL_MAP.get(kernel)
        if normalized:
            return normalized
        # Try case-insensitive
        normalized = _KERNEL_MAP.get(kernel.upper())
        if normalized:
            return normalized
        return kernel.lower()

    # Try to find a known kernel anywhere in the description
    for token in parts:
        token = token.strip()
        if token in _KERNEL_MAP:
            return _KERNEL_MAP[token]
        if token.upper() in _KERNEL_MAP:
            return _KERNEL_MAP[token.upper()]

    return "unknown"


# ---------------------------------------------------------------------------
# Series reference resolution
# ---------------------------------------------------------------------------

def resolve_seg_sr_references(
    patient_series: "pd.DataFrame",
) -> dict[str, str]:
    """Map SEG/SR SeriesInstanceUIDs to their referenced CT SeriesInstanceUIDs.

    Uses the series description to match: TotalSegmentator descriptions
    contain "of Series N" which corresponds to the Nth CT series
    (sorted by series number) within the same study.

    Parameters
    ----------
    patient_series : DataFrame with columns:
        StudyInstanceUID, SeriesInstanceUID, Modality, SeriesDescription

    Returns
    -------
    dict mapping SEG/SR SeriesInstanceUID → CT SeriesInstanceUID
    """
    refs: dict[str, str] = {}

    for study_uid in patient_series["StudyInstanceUID"].unique():
        study_data = patient_series[
            patient_series["StudyInstanceUID"] == study_uid
        ]

        # Get CT series sorted by series number or description
        ct_series = study_data[study_data["Modality"] == "CT"].sort_values(
            "SeriesDescription"
        )
        ct_list = ct_series["SeriesInstanceUID"].tolist()

        # Match SEG/SR to CT
        for _, row in study_data.iterrows():
            if row["Modality"] not in ("SEG", "SR"):
                continue
            desc = row["SeriesDescription"]
            # Extract "of Series N" pattern
            match = re.search(r"of [Ss]eries\s+(\d+)", desc)
            if match:
                series_num = int(match.group(1))
                # Series numbers are 1-based in the description,
                # referring to the Nth CT series
                if 0 < series_num <= len(ct_list):
                    refs[row["SeriesInstanceUID"]] = ct_list[series_num - 1]

    return refs


# ---------------------------------------------------------------------------
# DICOM SR parsing
# ---------------------------------------------------------------------------

def sr_to_records(dicom_bytes: bytes) -> list[dict[str, Any]]:
    """Parse a DICOM TID 1500 Measurement Report into a list of dicts.

    Extracts per-organ measurements from TotalSegmentator-style
    structured reports.

    Returns a list of dicts with keys:
        organ, measurement, value, unit
    """
    import pydicom

    ds = pydicom.dcmread(pydicom.filebase.DicomBytesIO(dicom_bytes))

    rows: list[dict[str, Any]] = []
    content_seq = getattr(ds, "ContentSequence", None)
    if content_seq is None:
        return rows

    _walk_sr_content(content_seq, rows, context={})
    return rows


def sr_to_dataframe(
    dicom_bytes: bytes,
    engine: str = "pandas",
) -> Any:
    """Parse a DICOM TID 1500 Measurement Report into a DataFrame.

    Extracts per-organ measurements from TotalSegmentator-style
    structured reports.

    Parameters
    ----------
    dicom_bytes : raw DICOM file bytes
    engine : "pandas" or "polars"

    Returns a DataFrame with columns:
        organ, measurement, value, unit
    """
    rows = sr_to_records(dicom_bytes)

    if engine == "polars":
        import polars as pl
        if not rows:
            return pl.DataFrame(
                schema={"organ": pl.Utf8, "measurement": pl.Utf8,
                        "value": pl.Float64, "unit": pl.Utf8}
            )
        return pl.DataFrame(rows)
    else:
        import pandas as pd
        if not rows:
            return pd.DataFrame(columns=["organ", "measurement", "value", "unit"])
        return pd.DataFrame(rows)


def _walk_sr_content(
    seq: Any,
    rows: list[dict[str, Any]],
    context: dict[str, str],
) -> None:
    """Recursively walk SR content tree extracting measurements.

    Handles TID 1500 Measurement Report structure:
    - Measurement Group containers hold per-organ measurements
    - Finding Site (HAS CONCEPT MOD CODE) names the organ
    - Laterality may be nested under Finding Site
    - NUM items within the group are the measurements
    - Image Library is skipped (contains per-slice geometry, not features)

    Within a container (Measurement Group), all sibling items share
    context — so Finding Site sets the organ for all NUM items in
    the same group.
    """
    # First pass: scan siblings for Finding Site to establish context
    group_context = dict(context)
    for item in seq:
        value_type = str(getattr(item, "ValueType", ""))
        concept = _sr_concept_name(item)

        if concept == "Finding Site" and value_type == "CODE":
            organ = _sr_code_meaning(item)
            if organ:
                laterality = None
                child_seq = getattr(item, "ContentSequence", None)
                if child_seq:
                    for child in child_seq:
                        if _sr_concept_name(child) == "Laterality":
                            laterality = _sr_code_meaning(child)
                if laterality:
                    group_context["organ"] = f"{laterality} {organ}"
                else:
                    group_context["organ"] = organ

    # Second pass: extract measurements and recurse
    for item in seq:
        value_type = str(getattr(item, "ValueType", ""))
        concept = _sr_concept_name(item)

        # Skip Image Library
        if value_type == "CONTAINER" and concept == "Image Library":
            continue

        # Extract numeric measurements
        if value_type == "NUM" and "organ" in group_context:
            measured = getattr(item, "MeasuredValueSequence", None)
            if measured and len(measured) > 0:
                mv = measured[0]
                value = float(getattr(mv, "NumericValue", 0))
                unit_seq = getattr(mv, "MeasurementUnitsCodeSequence", None)
                unit = ""
                if unit_seq and len(unit_seq) > 0:
                    unit = str(getattr(unit_seq[0], "CodeValue", ""))

                rows.append({
                    "organ": group_context["organ"],
                    "measurement": concept or "",
                    "value": value,
                    "unit": unit,
                })

        # Recurse into child containers
        child_seq = getattr(item, "ContentSequence", None)
        if child_seq:
            _walk_sr_content(child_seq, rows, group_context)


def _sr_concept_name(item: Any) -> str | None:
    """Extract the concept name meaning from an SR content item."""
    cn = getattr(item, "ConceptNameCodeSequence", None)
    if cn and len(cn) > 0:
        return str(getattr(cn[0], "CodeMeaning", ""))
    return None


def _sr_code_meaning(item: Any) -> str | None:
    """Extract the concept code meaning from an SR content item."""
    cc = getattr(item, "ConceptCodeSequence", None)
    if cc and len(cc) > 0:
        return str(getattr(cc[0], "CodeMeaning", ""))
    return None


# ---------------------------------------------------------------------------
# S3 download
# ---------------------------------------------------------------------------

def fetch_series_dicom(
    crdc_series_uuid: str,
    bucket: str = "idc-open-data",
) -> list[bytes]:
    """Download all DICOM files for a series from IDC S3.

    Parameters
    ----------
    crdc_series_uuid : CRDC series UUID (S3 prefix)
    bucket : S3 bucket name

    Returns
    -------
    List of DICOM file bytes (one per instance)
    """
    import httpx

    base = f"https://{bucket}.s3.amazonaws.com"

    # List files in series prefix
    client = httpx.Client(timeout=60, follow_redirects=True)
    try:
        resp = client.get(
            base,
            params={"list-type": "2", "prefix": f"{crdc_series_uuid}/"},
        )
        resp.raise_for_status()

        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys = [c.text for c in root.findall(".//s3:Key", ns)]

        # Download each file
        files = []
        for key in keys:
            if not key.endswith(".dcm"):
                continue
            resp = client.get(f"{base}/{key}")
            resp.raise_for_status()
            files.append(resp.content)

        return files
    finally:
        client.close()


# ---------------------------------------------------------------------------
# ZMP group helpers
# ---------------------------------------------------------------------------

def add_group(
    builder: Any,
    path: str,
    attrs: dict[str, Any] | None = None,
) -> None:
    """Add a zarr v3 group entry to a ZMP builder.

    Parameters
    ----------
    builder : zarr_zmp.Builder
    path : group path (e.g., "2001/standard")
    attrs : optional group attributes
    """
    meta: dict[str, Any] = {"zarr_format": 3, "node_type": "group"}
    if attrs:
        meta["attributes"] = attrs
    builder.add(f"{path}/zarr.json", text=json.dumps(meta))


def add_mount(
    builder: Any,
    path: str,
    zmp_path: str,
) -> None:
    """Add a mount entry pointing to another ZMP file.

    Parameters
    ----------
    builder : zarr_zmp.Builder
    path : mount path in the parent (e.g., "2001/standard/ct")
    zmp_path : filesystem path or URL to the child ZMP
    """
    from zmanifest import ContentType

    builder.mount(
        path,
        resolve={"http": {"url": zmp_path}},
        content_type=ContentType.ZMP,
    )


def add_parquet(
    builder: Any,
    path: str,
    df: "pd.DataFrame",
) -> None:
    """Add a parquet file inline in a ZMP.

    Parameters
    ----------
    builder : zarr_zmp.Builder
    path : path in the manifest (e.g., "2001/standard/features.parquet")
    df : pandas DataFrame to serialize
    """
    import io

    from zmanifest import ContentType

    buf = io.BytesIO()
    df.to_parquet(buf, index=False)
    builder.add(path, data=buf.getvalue(), content_type=ContentType.PARQUET)


# ---------------------------------------------------------------------------
# Patient query
# ---------------------------------------------------------------------------

def query_patient_series(
    patient_id: str,
    idc_version: str | None = None,
) -> "pd.DataFrame":
    """Query all series for a patient from the IDC index.

    Returns a DataFrame with columns:
        PatientID, StudyDate, StudyInstanceUID, Modality,
        SeriesDescription, SeriesInstanceUID, crdc_series_uuid,
        series_size_MB, aws_bucket
    """
    from idc_index import index

    idx = index.IDCClient()

    result = idx.sql_query(f"""
        SELECT DISTINCT
            PatientID, StudyDate, StudyDescription, StudyInstanceUID,
            Modality, SeriesDescription, SeriesInstanceUID,
            crdc_series_uuid, series_size_MB, aws_bucket
        FROM index
        WHERE PatientID = '{patient_id}'
        ORDER BY StudyDate, Modality, SeriesDescription
    """)

    return result


def group_patient_data(
    series_df: "pd.DataFrame",
) -> dict[str, dict[str, dict[str, Any]]]:
    """Organize patient series into a year → kernel → {ct, seg, sr} hierarchy.

    Returns a nested dict:
        {year: {kernel: {"ct": [...], "seg": [...], "sr_shape": [...], "sr_firstorder": [...]}}}

    Each list contains series info dicts with keys:
        SeriesInstanceUID, crdc_series_uuid, SeriesDescription, series_size_MB
    """
    refs = resolve_seg_sr_references(series_df)

    # Build CT lookup: SeriesInstanceUID → kernel name
    ct_kernels: dict[str, str] = {}
    for _, row in series_df[series_df["Modality"] == "CT"].iterrows():
        kernel = normalize_kernel(row["SeriesDescription"])
        ct_kernels[row["SeriesInstanceUID"]] = kernel

    hierarchy: dict[str, dict[str, dict[str, list]]] = {}

    for _, row in series_df.iterrows():
        year = str(row["StudyDate"])[:4]
        if year not in hierarchy:
            hierarchy[year] = {}

        modality = row["Modality"]
        series_info = {
            "SeriesInstanceUID": row["SeriesInstanceUID"],
            "crdc_series_uuid": row["crdc_series_uuid"],
            "SeriesDescription": row["SeriesDescription"],
            "series_size_MB": row["series_size_MB"],
        }

        if modality == "CT":
            kernel = ct_kernels[row["SeriesInstanceUID"]]
            if kernel not in hierarchy[year]:
                hierarchy[year][kernel] = {
                    "ct": [], "seg": [], "sr_shape": [], "sr_firstorder": [],
                }
            hierarchy[year][kernel]["ct"].append(series_info)

        elif modality in ("SEG", "SR"):
            # Find the referenced CT and its kernel
            ref_ct = refs.get(row["SeriesInstanceUID"])
            if ref_ct and ref_ct in ct_kernels:
                kernel = ct_kernels[ref_ct]
            else:
                # Can't resolve — skip or put in unknown
                continue

            if kernel not in hierarchy[year]:
                hierarchy[year][kernel] = {
                    "ct": [], "seg": [], "sr_shape": [], "sr_firstorder": [],
                }

            if modality == "SEG":
                hierarchy[year][kernel]["seg"].append(series_info)
            elif "shape" in row["SeriesDescription"].lower():
                hierarchy[year][kernel]["sr_shape"].append(series_info)
            elif "firstorder" in row["SeriesDescription"].lower():
                hierarchy[year][kernel]["sr_firstorder"].append(series_info)

    return hierarchy
