#!/usr/bin/env python3
"""Fetch a DICOM series from IDC via DICOMweb and convert to duckn Zarr.

Usage:
    python scripts/fetch_idc.py <SeriesInstanceUID> <output.zarr>
    python scripts/fetch_idc.py <SeriesInstanceUID> <output.zarr> --keep-dicom
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import requests

IDC_PROXY = (
    "https://proxy.imaging.datacommons.cancer.gov/current/"
    "viewer-only-no-downloads-see-tinyurl-dot-com-slash-3j3d9jyp/dicomWeb"
)


def find_study_uid(series_uid: str) -> str:
    """Look up the StudyInstanceUID for a series via QIDO-RS."""
    url = f"{IDC_PROXY}/series"
    resp = requests.get(
        url,
        params={"SeriesInstanceUID": series_uid},
        headers={"Accept": "application/dicom+json"},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"Series not found: {series_uid}")
    study_uid = data[0].get("0020000D", {}).get("Value", [None])[0]
    if not study_uid:
        raise ValueError("No StudyInstanceUID in QIDO-RS response")

    modality = data[0].get("00080060", {}).get("Value", [""])[0]
    desc = data[0].get("0008103E", {}).get("Value", [""])[0]
    print(f"Found: {modality} series — {desc}")
    return study_uid


def list_instances(study_uid: str, series_uid: str) -> list[str]:
    """List SOPInstanceUIDs in the series via QIDO-RS."""
    url = f"{IDC_PROXY}/studies/{study_uid}/series/{series_uid}/instances"
    resp = requests.get(
        url,
        headers={"Accept": "application/dicom+json"},
    )
    resp.raise_for_status()
    data = resp.json()
    uids = [inst["00080018"]["Value"][0] for inst in data]
    print(f"  {len(uids)} instances")
    return uids


def fetch_series(
    study_uid: str,
    series_uid: str,
    instance_uids: list[str],
    output_dir: Path,
) -> list[Path]:
    """Download DICOM instances via WADO-RS to output_dir."""
    paths = []
    total = len(instance_uids)
    for i, sop_uid in enumerate(instance_uids, 1):
        url = (
            f"{IDC_PROXY}/studies/{study_uid}"
            f"/series/{series_uid}"
            f"/instances/{sop_uid}"
        )
        resp = requests.get(
            url,
            headers={"Accept": "multipart/related; type=application/dicom"},
        )
        resp.raise_for_status()

        # Parse multipart response to extract the DICOM part
        dcm_bytes = _extract_dicom_part(resp)
        out_path = output_dir / f"{i:04d}.dcm"
        out_path.write_bytes(dcm_bytes)
        paths.append(out_path)

        if i % 50 == 0 or i == total:
            print(f"  Downloaded {i}/{total}")

    return paths


def _extract_dicom_part(resp: requests.Response) -> bytes:
    """Extract the DICOM binary from a multipart/related response."""
    content_type = resp.headers.get("Content-Type", "")

    # Find the boundary
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
            break

    if boundary is None:
        # Maybe not multipart — return raw body
        return resp.content

    # Split on boundary and find the DICOM part
    sep = f"--{boundary}".encode()
    parts = resp.content.split(sep)

    for part in parts:
        # Skip empty parts and closing boundary
        if not part.strip() or part.strip() == b"--":
            continue
        # Find the blank line separating headers from body
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            header_end = part.find(b"\n\n")
            if header_end == -1:
                continue
            body = part[header_end + 2:]
        else:
            body = part[header_end + 4:]

        # Check if this looks like DICOM (starts with preamble or DICM magic)
        if len(body) > 132 and body[128:132] == b"DICM":
            return body
        # Could also be raw without preamble
        if len(body) > 8:
            # Check for a DICOM tag pattern (group, element as little-endian uint16)
            group = struct.unpack("<H", body[:2])[0]
            if group in (0x0002, 0x0008):
                return body

    # Fallback: return largest part
    largest = max(parts, key=len)
    header_end = largest.find(b"\r\n\r\n")
    if header_end != -1:
        return largest[header_end + 4:]
    return largest


def main():
    parser = argparse.ArgumentParser(description="Fetch IDC DICOM series → duckn Zarr")
    parser.add_argument("series_uid", help="SeriesInstanceUID")
    parser.add_argument("output", help="Output .zarr path")
    parser.add_argument("--keep-dicom", action="store_true", help="Keep downloaded .dcm files")
    parser.add_argument("--dicom-dir", default=None, help="Directory to save .dcm files")
    parser.add_argument("--compressor", default="zstd", choices=["zstd", "gzip", "none"])
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    from duckn.dicom_convert import dicom_to_zarr

    # Resolve series
    print(f"Looking up series {args.series_uid}...")
    study_uid = find_study_uid(args.series_uid)
    instance_uids = list_instances(study_uid, args.series_uid)

    # Download
    if args.dicom_dir:
        dcm_dir = Path(args.dicom_dir)
        dcm_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        dcm_dir = Path(tempfile.mkdtemp(prefix="idc_dicom_"))
        cleanup = not args.keep_dicom

    try:
        print(f"Downloading {len(instance_uids)} instances to {dcm_dir}...")
        fetch_series(study_uid, args.series_uid, instance_uids, dcm_dir)

        # Convert
        print(f"Converting to {args.output}...")
        dicom_to_zarr(
            dcm_dir,
            args.output,
            compressor=args.compressor,
            overwrite=args.overwrite,
        )
        print("Done!")

    finally:
        if cleanup:
            shutil.rmtree(dcm_dir, ignore_errors=True)
        elif args.keep_dicom:
            print(f"DICOM files kept at: {dcm_dir}")


if __name__ == "__main__":
    main()
