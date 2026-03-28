#!/usr/bin/env python3
"""Build a patient-level ZMP hierarchy from IDC data.

Given an IDC patient ID, queries the IDC index for all series,
organizes them into a year → kernel → {ct, seg, features} hierarchy,
and builds a patient ZMP with:
  - CT arrays as mounted ZMPs with virtual S3 byte-range references
  - SEG arrays as mounted ZMPs with inline decoded pixel data
  - SR measurements as inline parquet tables

Usage:
    python scripts/build_patient_zmp.py 119269 /tmp/patient_119269/
    python scripts/build_patient_zmp.py 119269 /tmp/patient_119269/ --single-zmp
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def build_patient(
    patient_id: str,
    output_dir: Path,
    *,
    single_zmp: bool = False,
    overwrite: bool = False,
) -> Path:
    """Build the full patient hierarchy.

    Parameters
    ----------
    patient_id : IDC patient ID
    output_dir : directory for output files
    single_zmp : if True, build one ZMP with all data inline and mounted
    overwrite : overwrite existing files

    Returns
    -------
    Path to the patient ZMP
    """
    from zarr_zmp import Builder
    from zmanifest import ContentType

    from duckn.idc_utils import (
        add_group,
        add_mount,
        add_parquet,
        fetch_series_dicom,
        group_patient_data,
        query_patient_series,
        sr_to_dataframe,
    )
    from duckn.idc_zmp import build_idc_zmp

    output_dir.mkdir(parents=True, exist_ok=True)

    # Query IDC index
    print(f"Querying IDC index for patient {patient_id}...")
    series_df = query_patient_series(patient_id)
    if series_df.empty:
        print(f"No series found for patient {patient_id}", file=sys.stderr)
        sys.exit(1)

    hierarchy = group_patient_data(series_df)

    print(f"Found {len(series_df)} series across {len(hierarchy)} time points:")
    for year in sorted(hierarchy):
        kernels = hierarchy[year]
        print(f"  {year}: {', '.join(sorted(kernels.keys()))}")

    # Build individual ZMPs
    zmp_paths: dict[str, dict[str, dict[str, Path]]] = {}

    for year in sorted(hierarchy):
        for kernel in sorted(hierarchy[year]):
            data = hierarchy[year][kernel]
            prefix = f"{year}/{kernel}"
            zmp_paths.setdefault(year, {}).setdefault(kernel, {})

            # --- CT ---
            if data["ct"]:
                ct_info = data["ct"][0]  # use first CT series
                ct_zmp = output_dir / f"{year}_{kernel}_ct.zmp"
                if ct_zmp.exists() and not overwrite:
                    print(f"  {prefix}/ct: exists, skipping")
                else:
                    print(f"  {prefix}/ct: building from {ct_info['crdc_series_uuid']}...")
                    try:
                        build_idc_zmp(
                            ct_info["crdc_series_uuid"],
                            str(ct_zmp),
                            overwrite=overwrite,
                        )
                        print(f"    → {ct_zmp.stat().st_size / 1024:.0f} KB")
                    except Exception as e:
                        print(f"    ERROR: {e}", file=sys.stderr)
                        ct_zmp = None
                if ct_zmp and ct_zmp.exists():
                    zmp_paths[year][kernel]["ct"] = ct_zmp

            # --- SEG ---
            if data["seg"]:
                seg_info = data["seg"][0]  # use first SEG
                seg_zmp = output_dir / f"{year}_{kernel}_seg.zmp"
                if seg_zmp.exists() and not overwrite:
                    print(f"  {prefix}/seg: exists, skipping")
                    zmp_paths[year][kernel]["seg"] = seg_zmp
                else:
                    print(f"  {prefix}/seg: downloading and decoding...")
                    try:
                        seg_zmp_path = _build_seg_zmp(
                            seg_info["crdc_series_uuid"],
                            seg_zmp,
                            overwrite=overwrite,
                        )
                        print(f"    → {seg_zmp.stat().st_size / 1024:.0f} KB")
                        zmp_paths[year][kernel]["seg"] = seg_zmp
                    except Exception as e:
                        print(f"    ERROR: {e}", file=sys.stderr)

            # --- SR (features) ---
            sr_dfs = []
            for sr_type in ("sr_shape", "sr_firstorder"):
                if data[sr_type]:
                    sr_info = data[sr_type][0]
                    label = sr_type.replace("sr_", "")
                    print(f"  {prefix}/{label}: parsing SR...")
                    try:
                        files = fetch_series_dicom(sr_info["crdc_series_uuid"])
                        df = sr_to_dataframe(files[0])
                        df["feature_type"] = label
                        sr_dfs.append(df)
                        print(f"    → {len(df)} measurements")
                    except Exception as e:
                        print(f"    ERROR: {e}", file=sys.stderr)

            if sr_dfs:
                import pandas as pd
                combined = pd.concat(sr_dfs, ignore_index=True)
                zmp_paths[year][kernel]["features"] = combined

    # --- Assemble patient ZMP ---
    print()
    print("Assembling patient ZMP...")
    patient_builder = Builder()
    patient_builder.add("zarr.json", text=json.dumps({
        "zarr_format": 3,
        "node_type": "group",
        "attributes": {
            "patient_id": patient_id,
            "source": "IDC",
        },
    }))

    for year in sorted(zmp_paths):
        add_group(patient_builder, year)

        for kernel in sorted(zmp_paths[year]):
            add_group(patient_builder, f"{year}/{kernel}")
            kdata = zmp_paths[year][kernel]

            # Mount CT
            if "ct" in kdata:
                add_mount(patient_builder, f"{year}/{kernel}/ct", str(kdata["ct"]))

            # Mount SEG
            if "seg" in kdata:
                add_mount(patient_builder, f"{year}/{kernel}/seg", str(kdata["seg"]))

            # Inline features parquet
            if "features" in kdata:
                add_parquet(
                    patient_builder,
                    f"{year}/{kernel}/features.parquet",
                    kdata["features"],
                )

    patient_zmp = output_dir / f"patient_{patient_id}.zmp"
    if patient_zmp.exists() and overwrite:
        patient_zmp.unlink()
    patient_builder.write(str(patient_zmp))
    print(f"Wrote {patient_zmp} ({patient_zmp.stat().st_size / 1024:.0f} KB)")

    # Summary
    print()
    print("Summary:")
    for year in sorted(zmp_paths):
        for kernel in sorted(zmp_paths[year]):
            kdata = zmp_paths[year][kernel]
            parts = []
            if "ct" in kdata:
                parts.append("ct")
            if "seg" in kdata:
                parts.append("seg")
            if "features" in kdata:
                parts.append(f"features({len(kdata['features'])} rows)")
            print(f"  {year}/{kernel}: {', '.join(parts)}")

    return patient_zmp


def _build_seg_zmp(
    crdc_series_uuid: str,
    output_path: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Download a DICOM SEG from S3, decode, and write as inline ZMP."""
    import io
    import zstandard

    import pydicom
    from zarr_zmp import Builder
    from zmanifest import ContentType

    from duckn.dicom_convert import (
        _extract_seg_extension,
        _load_seg,
        build_duckn_metadata,
    )
    from duckn.idc_utils import fetch_series_dicom

    # Download
    files = fetch_series_dicom(crdc_series_uuid)
    if not files:
        raise ValueError(f"No DICOM files found for {crdc_series_uuid}")

    # Parse — SEG is typically one multiframe instance
    ds = pydicom.dcmread(io.BytesIO(files[0]))

    # Decode pixel data
    data, geometry, sorted_datasets = _load_seg(ds)

    # Build metadata
    duckn_meta = build_duckn_metadata(
        geometry,
        sorted_datasets or [ds],
        anonymized=None,
        include_tags=True,
    )

    # Extract seg extension
    seg_ext = _extract_seg_extension(ds)
    if seg_ext is not None:
        if duckn_meta.extensions is None:
            duckn_meta.extensions = {}
        duckn_meta.extensions["seg"] = seg_ext.model_dump(exclude_none=True)

    # Build zarr metadata
    shape = list(data.shape)
    ndim = len(shape)
    dtype_str = str(data.dtype)

    if ndim == 4:
        chunk_shape = [1, 1, shape[2], shape[3]]
        dim_names = ["segment", "k", "j", "i"]
    else:
        chunk_shape = [1, shape[1], shape[2]]
        dim_names = ["k", "j", "i"]

    zarr_meta = {
        "zarr_format": 3,
        "node_type": "array",
        "shape": shape,
        "data_type": dtype_str,
        "chunk_grid": {
            "name": "regular",
            "configuration": {"chunk_shape": chunk_shape},
        },
        "chunk_key_encoding": {
            "name": "default",
            "configuration": {"separator": "/"},
        },
        "fill_value": 0,
        "codecs": [
            {"name": "bytes", "configuration": {"endian": "little"}},
            {"name": "zstd", "configuration": {"level": 3, "checksum": False}},
        ],
        "attributes": {"duckn": duckn_meta.model_dump(exclude_none=True)},
        "dimension_names": dim_names,
    }

    # Write ZMP with inline compressed chunks
    cctx = zstandard.ZstdCompressor(level=3)
    builder = Builder()
    builder.add("zarr.json", text=json.dumps(zarr_meta))

    if ndim == 4:
        n_seg, n_z = shape[0], shape[1]
        for s in range(n_seg):
            for z in range(n_z):
                chunk_bytes = data[s, z, :, :].tobytes()
                builder.add(f"c/{s}/{z}/0/0", data=cctx.compress(chunk_bytes))
    else:
        for z in range(shape[0]):
            chunk_bytes = data[z, :, :].tobytes()
            builder.add(f"c/{z}/0/0", data=cctx.compress(chunk_bytes))

    if output_path.exists() and overwrite:
        output_path.unlink()

    builder.write(str(output_path))
    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Build patient-level ZMP hierarchy from IDC data"
    )
    parser.add_argument("patient_id", help="IDC patient ID")
    parser.add_argument("output_dir", type=Path, help="Output directory")
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite existing files"
    )
    parser.add_argument(
        "--single-zmp",
        action="store_true",
        help="Build a single ZMP (future, not yet implemented)",
    )

    args = parser.parse_args()
    build_patient(
        args.patient_id,
        args.output_dir,
        single_zmp=args.single_zmp,
        overwrite=args.overwrite,
    )


if __name__ == "__main__":
    main()
