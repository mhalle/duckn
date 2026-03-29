#!/usr/bin/env python3
"""Build a patient-level ZMP from IDC data.

Given an IDC patient ID, queries the IDC index for all series,
organizes them into a year → kernel → {ct, seg, features} hierarchy,
and builds a single self-contained ZMP with:
  - CT arrays as inline-mounted ZMPs with virtual S3 byte-range references
  - SEG arrays as inline-mounted ZMPs with decoded pixel data
  - SR measurements as inline parquet tables

All sub-ZMP builds run in parallel for speed.

Usage:
    python scripts/build_patient_zmp.py 119269 /tmp/patient_119269.zmp
    python scripts/build_patient_zmp.py 119269 /tmp/patient_119269.zmp --overwrite
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Sub-ZMP builders (each returns bytes)
# ---------------------------------------------------------------------------


async def build_ct_zmp_bytes(crdc_series_uuid: str, base_url: str) -> bytes:
    """Build a CT ZMP using the async IDC builder, return bytes."""
    import tempfile

    from duckn.idc_zmp import async_build_idc_zmp

    tmp = tempfile.mktemp(suffix=".zmp")
    try:
        await async_build_idc_zmp(
            crdc_series_uuid, tmp, base_url=base_url, overwrite=True,
        )
        return Path(tmp).read_bytes()
    finally:
        Path(tmp).unlink(missing_ok=True)


def _build_seg_zmp_bytes_sync(crdc_series_uuid: str) -> bytes:
    """Download DICOM SEG from S3, decode to labelmap, build ZMP. (sync)"""
    import pydicom
    import zarr

    from zarr_zmp import ZMPWritableStore

    from duckn.dicom_convert import (
        _extract_seg_extension,
        _load_seg,
        build_duckn_metadata,
    )
    from duckn.idc_utils import fetch_series_dicom
    from duckn.seg_convert import seg_binary_to_labelmap

    files = fetch_series_dicom(crdc_series_uuid)
    if not files:
        raise ValueError(f"No DICOM files found for {crdc_series_uuid}")
    ds = pydicom.dcmread(io.BytesIO(files[0]))

    data, geometry, sorted_datasets = _load_seg(ds)
    duckn_meta = build_duckn_metadata(
        geometry, sorted_datasets or [ds], anonymized=None, include_tags=True,
    )

    seg_ext = _extract_seg_extension(ds)
    if seg_ext is not None:
        if duckn_meta.extensions is None:
            duckn_meta.extensions = {}
        duckn_meta.extensions["seg"] = seg_ext.model_dump(exclude_none=True)

    if data.ndim == 4:
        data, duckn_meta = seg_binary_to_labelmap(data, duckn_meta)

    buf = io.BytesIO()
    store = ZMPWritableStore(buf)
    attrs = {"duckn": duckn_meta.model_dump(exclude_none=True)}

    arr = zarr.open_array(
        store, mode="w",
        shape=data.shape, dtype=data.dtype,
        chunks=(data.shape[0], data.shape[1], data.shape[2]),
        attributes=attrs,
    )
    arr[:] = data
    # Can't use asyncio.run() inside a thread spawned by an event loop,
    # but store.close() is the coroutine we need. Use a new event loop
    # since this runs in a separate thread via to_thread().
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(store.close())
    finally:
        loop.close()
    return buf.getvalue()


async def build_seg_zmp_bytes(crdc_series_uuid: str) -> bytes:
    """Async wrapper — runs sync SEG builder in a thread."""
    return await asyncio.to_thread(_build_seg_zmp_bytes_sync, crdc_series_uuid)


def _build_features_sync(sr_infos: dict) -> "pd.DataFrame":
    """Download and parse SR files, return combined DataFrame. (sync)"""
    import pandas as pd

    from duckn.idc_utils import fetch_series_dicom, sr_to_dataframe

    dfs = []
    for label, info in sr_infos.items():
        files = fetch_series_dicom(info["crdc_series_uuid"])
        df = sr_to_dataframe(files[0])
        df["feature_type"] = label
        dfs.append(df)

    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()


async def build_features(sr_infos: dict) -> "pd.DataFrame":
    """Async wrapper — runs sync SR parsing in a thread."""
    return await asyncio.to_thread(_build_features_sync, sr_infos)


# ---------------------------------------------------------------------------
# Task types
# ---------------------------------------------------------------------------


async def build_patient(
    patient_id: str,
    output_path: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Build a single self-contained patient ZMP with parallel fetching."""
    from zarr_zmp import Builder

    from duckn.idc_utils import (
        add_group,
        add_inline_mount,
        add_parquet,
        group_patient_data,
        query_patient_series,
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

    # Build all sub-ZMPs and features in parallel
    print()
    print("Building all series in parallel...")
    t0 = time.time()

    tasks: list[tuple[str, str, str, asyncio.Task]] = []  # (type, year, kernel, task)

    for year in sorted(hierarchy):
        for kernel in sorted(hierarchy[year]):
            data = hierarchy[year][kernel]
            prefix = f"{year}/{kernel}"

            if data["ct"]:
                ct_info = data["ct"][0]
                task = asyncio.create_task(
                    build_ct_zmp_bytes(ct_info["crdc_series_uuid"], base_url="https://idc-open-data.s3.amazonaws.com"),
                    name=f"{prefix}/ct",
                )
                tasks.append(("ct", year, kernel, task))
                print(f"  {prefix}/ct: started")

            if data["seg"]:
                seg_info = data["seg"][0]
                task = asyncio.create_task(
                    build_seg_zmp_bytes(seg_info["crdc_series_uuid"]),
                    name=f"{prefix}/seg",
                )
                tasks.append(("seg", year, kernel, task))
                print(f"  {prefix}/seg: started")

            sr_infos = {}
            if data["sr_shape"]:
                sr_infos["shape"] = data["sr_shape"][0]
            if data["sr_firstorder"]:
                sr_infos["firstorder"] = data["sr_firstorder"][0]
            if sr_infos:
                task = asyncio.create_task(
                    build_features(sr_infos),
                    name=f"{prefix}/features",
                )
                tasks.append(("features", year, kernel, task))
                print(f"  {prefix}/features: started")

    # Wait for all tasks
    all_tasks = [t[3] for t in tasks]
    results = await asyncio.gather(*all_tasks, return_exceptions=True)

    elapsed = time.time() - t0
    print(f"\nAll tasks completed in {elapsed:.1f}s")

    # Assemble patient ZMP
    print("\nAssembling patient ZMP...")
    builder = Builder()
    builder.add("zarr.json", text=json.dumps({
        "zarr_format": 3, "node_type": "group",
        "attributes": {"patient_id": patient_id, "source": "IDC"},
    }))

    # Ensure all groups exist
    for year in sorted(hierarchy):
        add_group(builder, year)
        for kernel in sorted(hierarchy[year]):
            add_group(builder, f"{year}/{kernel}")

    # Add results
    for (task_type, year, kernel, _), result in zip(tasks, results):
        prefix = f"{year}/{kernel}"

        if isinstance(result, Exception):
            print(f"  ERROR {prefix}/{task_type}: {result}", file=sys.stderr)
            continue

        if task_type == "ct":
            add_inline_mount(builder, f"{prefix}/ct", result)
            print(f"  {prefix}/ct: {len(result) / 1024:.0f} KB")
        elif task_type == "seg":
            add_inline_mount(builder, f"{prefix}/seg", result)
            print(f"  {prefix}/seg: {len(result) / 1024:.0f} KB")
        elif task_type == "features":
            if not result.empty:
                add_parquet(builder, f"{prefix}/features.parquet", result)
                print(f"  {prefix}/features: {len(result)} rows")

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
    asyncio.run(build_patient(args.patient_id, args.output, overwrite=args.overwrite))


if __name__ == "__main__":
    main()
