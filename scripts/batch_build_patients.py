#!/usr/bin/env python3
"""Batch-build patient ZMPs from a JSON manifest.

Reads a batch JSON file (e.g., output/idc/batch-01.json) containing
patient IDs and builds ZMPs for each, placing them in the output directory.

Usage:
    python scripts/batch_build_patients.py output/idc/batch-01.json
    python scripts/batch_build_patients.py output/idc/batch-01.json --workers 4
    python scripts/batch_build_patients.py output/idc/batch-01.json --clinical-dir ~/idc_clinical/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path


async def process_patient(
    patient_id: str,
    output_dir: Path,
    clinical_dir: Path | None,
    semaphore: asyncio.Semaphore,
) -> tuple[str, float, str]:
    """Build one patient ZMP. Returns (patient_id, elapsed, status)."""
    from duckn.build_patient_zmp import build_patient

    output_path = output_dir / f"patient_{patient_id}.zmp"
    if output_path.exists():
        return (patient_id, 0, "skipped")

    async with semaphore:
        t0 = time.time()
        try:
            await build_patient(
                patient_id, output_path,
                overwrite=False, clinical_dir=clinical_dir,
            )
            elapsed = time.time() - t0
            size_kb = output_path.stat().st_size / 1024
            return (patient_id, elapsed, f"ok ({size_kb:.0f} KB, {elapsed:.0f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            return (patient_id, elapsed, f"ERROR: {e}")


async def run_batch(
    batch_file: Path,
    output_dir: Path,
    workers: int,
    clinical_dir: Path | None,
) -> None:
    with open(batch_file) as f:
        batch = json.load(f)

    patient_ids = batch["patient_ids"]
    total = len(patient_ids)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Check how many already exist
    existing = sum(
        1 for pid in patient_ids
        if (output_dir / f"patient_{pid}.zmp").exists()
    )
    remaining = total - existing

    print(f"Batch: {total} patients, {existing} already built, {remaining} to build")
    print(f"Workers: {workers}")
    print(f"Output: {output_dir}")
    if clinical_dir:
        print(f"Clinical data: {clinical_dir}")
    print()

    if remaining == 0:
        print("Nothing to do.")
        return

    semaphore = asyncio.Semaphore(workers)
    t0 = time.time()
    completed = 0
    errors = 0

    tasks = [
        process_patient(pid, output_dir, clinical_dir, semaphore)
        for pid in patient_ids
    ]

    for coro in asyncio.as_completed(tasks):
        patient_id, elapsed, status = await coro
        completed += 1
        if "ERROR" in status:
            errors += 1

        wall = time.time() - t0
        rate = completed / wall if wall > 0 else 0
        eta = (remaining - completed) / rate if rate > 0 else 0

        print(
            f"  [{completed}/{remaining}] {patient_id}: {status}"
            f"  (rate: {rate:.1f}/min, ETA: {eta/60:.0f}min)"
            if completed % 10 == 0 or "ERROR" in status
            else f"  [{completed}/{remaining}] {patient_id}: {status}"
        )

    wall = time.time() - t0
    print()
    print(f"Done: {completed} patients in {wall:.0f}s ({wall/60:.1f}min)")
    print(f"  Success: {completed - errors}, Errors: {errors}")
    print(f"  Rate: {completed / wall * 60:.1f} patients/min")


def main():
    parser = argparse.ArgumentParser(
        description="Batch-build patient ZMPs from a JSON manifest"
    )
    parser.add_argument("batch_file", type=Path, help="JSON file with patient_ids list")
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Output directory (default: same dir as batch file)",
    )
    parser.add_argument(
        "--workers", type=int, default=2,
        help="Number of concurrent patient builds (default: 2)",
    )
    parser.add_argument(
        "--clinical-dir", type=Path, default=None,
        help="Path to IDC clinical data directory",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show HTTP request logging",
    )

    args = parser.parse_args()

    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    output_dir = args.output_dir or args.batch_file.parent
    asyncio.run(run_batch(args.batch_file, output_dir, args.workers, args.clinical_dir))


if __name__ == "__main__":
    main()
