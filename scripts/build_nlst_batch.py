# /// script
# dependencies = [
#   "duckn @ git+https://github.com/mhalle/duckn.git",
#   "idc-index",
#   "httpx[http2]",
#   "pydicom",
#   "pyarrow",
#   "zmanifest @ git+https://github.com/mhalle/zmanifest.git",
#   "zarr-zmp @ git+https://github.com/mhalle/zarr-zmp.git",
# ]
#
# [tool.uv]
# override-dependencies = [
#   "zmanifest @ git+https://github.com/mhalle/zmanifest.git",
# ]
# ///
"""Build ZMP manifests for 100 NLST CT series in parallel.

Usage:
    uv run scripts/build_nlst_batch.py [--count 100] [--output /tmp/nlst_zmps]

Each ZMP is a ~25 KB manifest with virtual byte-range references into
DICOM files on S3. No pixel data is downloaded — only headers (~5 KB each).
"""

import argparse
import asyncio
import os
import sys
import time

def main():
    parser = argparse.ArgumentParser(description="Build ZMP manifests for NLST CT series")
    parser.add_argument("--count", type=int, default=100, help="Number of series to process")
    parser.add_argument("--output", type=str, default="/tmp/nlst_zmps", help="Output directory")
    parser.add_argument("--concurrency", type=int, default=20, help="Max parallel builds")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)

    # Select series from IDC index
    print(f"Selecting {args.count} NLST CT series from IDC index...")
    from idc_index import IDCClient
    client = IDCClient()

    df = client.sql_query(f"""
        SELECT crdc_series_uuid, instanceCount, series_size_MB
        FROM index
        WHERE collection_id = 'nlst'
        AND Modality = 'CT'
        AND instanceCount BETWEEN 50 AND 300
        AND series_aws_url LIKE 's3://idc-open-data/%'
        ORDER BY RANDOM()
        LIMIT {args.count}
    """)
    uuids = df['crdc_series_uuid'].tolist()
    total_slices = int(df['instanceCount'].sum())
    print(f"  {len(uuids)} series, {total_slices} total slices")
    print(f"  Average: {df['instanceCount'].mean():.0f} slices, {df['series_size_MB'].mean():.0f} MB per series")

    # Build all ZMPs in parallel
    from duckn.idc_zmp import async_build_idc_zmp

    completed = 0
    failed = 0
    t_start = time.perf_counter()

    async def build_one(i, uuid, sem):
        nonlocal completed, failed
        out = os.path.join(args.output, f"{i:04d}.zmp")
        async with sem:
            try:
                await async_build_idc_zmp(uuid, out, overwrite=True)
                completed += 1
                if completed % 10 == 0:
                    elapsed = time.perf_counter() - t_start
                    rate = completed / elapsed
                    eta = (len(uuids) - completed) / rate if rate > 0 else 0
                    print(f"  {completed}/{len(uuids)} done  ({rate:.1f}/s, ETA {eta:.0f}s)")
            except Exception as e:
                failed += 1
                print(f"  FAILED {uuid[:12]}: {e}", file=sys.stderr)

    async def build_all():
        sem = asyncio.Semaphore(args.concurrency)
        await asyncio.gather(*[build_one(i, uuid, sem) for i, uuid in enumerate(uuids)])

    print(f"\nBuilding {len(uuids)} ZMPs (concurrency={args.concurrency})...")
    asyncio.run(build_all())

    t_end = time.perf_counter()
    elapsed = t_end - t_start

    # Summary
    sizes = []
    for i in range(len(uuids)):
        p = os.path.join(args.output, f"{i:04d}.zmp")
        if os.path.exists(p):
            sizes.append(os.path.getsize(p))

    total_size = sum(sizes)
    print(f"\n=== Results ===")
    print(f"  Built: {completed}  Failed: {failed}")
    print(f"  Time: {elapsed:.1f}s  ({completed/elapsed:.1f} manifests/s)")
    print(f"  Slices indexed: {total_slices}")
    print(f"  Total ZMP size: {total_size:,} bytes ({total_size/1024:.0f} KB)")
    print(f"  Average: {total_size//max(len(sizes),1):,} bytes per manifest")
    print(f"  Output: {args.output}/")

    # Quick DuckDB verification
    print(f"\n=== DuckDB query test ===")
    import subprocess
    result = subprocess.run(
        ["duckdb", "-c", f"""
SELECT
  json_extract_string(metadata, '$.duckn.extensions.dicom.tags.Manufacturer') as mfr,
  COUNT(*) as n,
  ROUND(AVG(CAST(json_extract(metadata, '$.duckn.axes[0].space_direction[2]') AS DOUBLE)), 2) as avg_z_mm
FROM read_parquet('{args.output}/*.zmp', filename=true)
WHERE path = ''
GROUP BY mfr ORDER BY n DESC;
"""],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"  DuckDB not available: {result.stderr[:100]}")


if __name__ == "__main__":
    main()
