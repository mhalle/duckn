#!/usr/bin/env python3
"""Build a patient-level ZMP from IDC data.

Given an IDC patient ID, queries the IDC index for all series,
organizes them into a year → kernel → {ct, seg, features} hierarchy,
and builds a single self-contained ZMP with:
  - CT arrays as inline-mounted ZMPs with virtual S3 byte-range references
  - SEG arrays as inline-mounted ZMPs with decoded pixel data
  - SR measurements as inline parquet tables

Usage:
    python scripts/build_patient_zmp.py 119269 /tmp/patient_119269.zmp
    python scripts/build_patient_zmp.py 119269 /tmp/patient_119269.zmp --overwrite
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path


def build_ct_zmp_bytes(crdc_series_uuid: str) -> bytes:
    """Build a CT ZMP in memory, return bytes."""
    from duckn.idc_zmp import build_idc_zmp

    with tempfile.NamedTemporaryFile(suffix=".zmp", delete=False) as f:
        tmp = f.name
    try:
        build_idc_zmp(crdc_series_uuid, tmp, overwrite=True)
        return Path(tmp).read_bytes()
    finally:
        Path(tmp).unlink(missing_ok=True)


def build_seg_zmp_bytes(crdc_series_uuid: str) -> bytes:
    """Download DICOM SEG from S3, decode, build ZMP in memory, return bytes."""
    import numpy as np
    import pydicom
    import zstandard

    from zarr_zmp import Builder

    from duckn.dicom_convert import (
        _extract_seg_extension,
        _load_seg,
        build_duckn_metadata,
    )
    from duckn.idc_utils import fetch_series_dicom

    # Download and parse
    files = fetch_series_dicom(crdc_series_uuid)
    if not files:
        raise ValueError(f"No DICOM files found for {crdc_series_uuid}")
    ds = pydicom.dcmread(io.BytesIO(files[0]))

    # Decode
    data, geometry, sorted_datasets = _load_seg(ds)
    duckn_meta = build_duckn_metadata(
        geometry, sorted_datasets or [ds], anonymized=None, include_tags=True,
    )

    # Seg extension
    seg_ext = _extract_seg_extension(ds)
    if seg_ext is not None:
        if duckn_meta.extensions is None:
            duckn_meta.extensions = {}
        duckn_meta.extensions["seg"] = seg_ext.model_dump(exclude_none=True)

    # Build ZMP
    shape = list(data.shape)
    ndim = len(shape)

    if ndim == 4:
        chunk_shape = [1, 1, shape[2], shape[3]]
        dim_names = ["segment", "k", "j", "i"]
    else:
        chunk_shape = [1, shape[1], shape[2]]
        dim_names = ["k", "j", "i"]

    zarr_meta = {
        "zarr_format": 3, "node_type": "array",
        "shape": shape, "data_type": str(data.dtype),
        "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": chunk_shape}},
        "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
        "fill_value": 0,
        "codecs": [
            {"name": "bytes", "configuration": {"endian": "little"}},
            {"name": "zstd", "configuration": {"level": 3, "checksum": False}},
        ],
        "attributes": {"duckn": duckn_meta.model_dump(exclude_none=True)},
        "dimension_names": dim_names,
    }

    cctx = zstandard.ZstdCompressor(level=3)
    builder = Builder()
    builder.add("zarr.json", text=json.dumps(zarr_meta))

    if ndim == 4:
        for s in range(shape[0]):
            for z in range(shape[1]):
                builder.add(f"c/{s}/{z}/0/0", data=cctx.compress(data[s, z].tobytes()))
    else:
        for z in range(shape[0]):
            builder.add(f"c/{z}/0/0", data=cctx.compress(data[z].tobytes()))

    buf = io.BytesIO()
    builder.write(buf)
    return buf.getvalue()


def build_patient(
    patient_id: str,
    output_path: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Build a single self-contained patient ZMP.

    All sub-ZMPs (CT, SEG) are stored inline as mounted entries.
    Features parquet tables are stored inline.
    """
    from zarr_zmp import Builder

    from duckn.idc_utils import (
        add_group,
        add_inline_mount,
        add_parquet,
        fetch_series_dicom,
        group_patient_data,
        query_patient_series,
        sr_to_dataframe,
    )

    if output_path.exists() and not overwrite:
        print(f"{output_path} exists, use --overwrite", file=sys.stderr)
        sys.exit(1)

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

    # Build everything into one ZMP
    print()
    builder = Builder()
    builder.add("zarr.json", text=json.dumps({
        "zarr_format": 3, "node_type": "group",
        "attributes": {"patient_id": patient_id, "source": "IDC"},
    }))

    for year in sorted(hierarchy):
        add_group(builder, year)

        for kernel in sorted(hierarchy[year]):
            data = hierarchy[year][kernel]
            prefix = f"{year}/{kernel}"
            add_group(builder, prefix)

            # --- CT ---
            if data["ct"]:
                ct_info = data["ct"][0]
                print(f"  {prefix}/ct: building from {ct_info['crdc_series_uuid']}...")
                try:
                    ct_bytes = build_ct_zmp_bytes(ct_info["crdc_series_uuid"])
                    add_inline_mount(builder, f"{prefix}/ct", ct_bytes)
                    print(f"    → {len(ct_bytes) / 1024:.0f} KB")
                except Exception as e:
                    print(f"    ERROR: {e}", file=sys.stderr)

            # --- SEG ---
            if data["seg"]:
                seg_info = data["seg"][0]
                print(f"  {prefix}/seg: downloading and decoding...")
                try:
                    seg_bytes = build_seg_zmp_bytes(seg_info["crdc_series_uuid"])
                    add_inline_mount(builder, f"{prefix}/seg", seg_bytes)
                    print(f"    → {len(seg_bytes) / 1024:.0f} KB")
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
                add_parquet(builder, f"{prefix}/features.parquet", combined)

    # Write
    if output_path.exists() and overwrite:
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    builder.write(str(output_path))
    print()
    print(f"Wrote {output_path} ({output_path.stat().st_size / 1024:.0f} KB)")

    return output_path


def main():
    parser = argparse.ArgumentParser(
        description="Build a single patient ZMP from IDC data"
    )
    parser.add_argument("patient_id", help="IDC patient ID")
    parser.add_argument("output", type=Path, help="Output .zmp file")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing")

    args = parser.parse_args()
    build_patient(args.patient_id, args.output, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
