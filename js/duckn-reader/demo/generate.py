#!/usr/bin/env python3
"""Generate demo duckn Zarr stores in public/ for the Vite dev server."""

import os
from duckn import nrrd_to_zarr

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(SCRIPT_DIR, "public")
os.makedirs(PUBLIC_DIR, exist_ok=True)

TEST_DIR = os.path.join(SCRIPT_DIR, "..", "..", "..", "tests", "data")

STORES = {
    "demo.zarr": "scalar_3d_ras.nrrd",
    "demo_oblique.zarr": "scalar_3d_oblique.nrrd",
    "demo_lps.zarr": "scalar_3d_lps_aniso.nrrd",
}

for name, nrrd_file in STORES.items():
    nrrd_path = os.path.join(TEST_DIR, nrrd_file)
    dest = os.path.join(PUBLIC_DIR, name)
    if not os.path.exists(nrrd_path):
        print(f"  skip {name} — {nrrd_path} not found")
        continue
    print(f"  {nrrd_file} -> public/{name}")
    nrrd_to_zarr(nrrd_path, dest, overwrite=True)

print("Done. Zarr stores are in public/")
