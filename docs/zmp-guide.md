# Building ZMP Manifests

[ZMP](https://github.com/mhalle/zarr-zmp) (Zarr Manifest Parquet) is a general-purpose index format for Zarr stores. A ZMP maps chunk paths to byte ranges in external files — zip archives, DICOM files on S3, NIfTI files, DICOMweb servers — giving Zarr-compatible random access without copying or converting data. ZMP is an independent project with no dependency on duckn.

This guide covers the tools in the duckn library that build ZMP manifests from imaging sources. These tools optionally inject duckn metadata (spatial calibration, axis semantics, domain extensions) into the ZMP's `zarr.json` entry, but duckn metadata is not required — you can build ZMPs without it.

ZMPs come in two flavors:

- **Virtual**: chunk entries are byte-range references to external files. The ZMP is tiny (typically 10-100 KB) and data is fetched on demand.
- **Hydrated**: chunk data is embedded inline in the ZMP's Parquet data column. The ZMP is self-contained but larger.

The `zarr.json` entry in every ZMP is queryable via DuckDB or any Parquet reader — whether or not it contains duckn metadata.

---

## Sources

### Zarr zip stores (local or remote)

Convert a `.zarr.zip` file — Zarr v2 or v3, including OME-NGFF multi-resolution pyramids — to a ZMP. Chunks are referenced by byte range within the zip file.

```bash
# Local file
duckn from-zarr-zip data.zarr.zip data.zmp

# Remote file on S3 (scans central directory via range requests)
duckn from-zarr-zip https://s3.amazonaws.com/bucket/data.zarr.zip data.zmp

# With a path prefix inside the zip
duckn from-zarr-zip archive.zip data.zmp --prefix="nested/data.zarr/"

# Hydrated (embed all chunk data inline)
duckn from-zarr-zip data.zarr.zip data.zmp --hydrate
```

```python
from duckn.zarr_zip_convert import zarr_zip_to_zmp

# Virtual (byte-range references)
zarr_zip_to_zmp("data.zarr.zip", "data.zmp")

# Remote
zarr_zip_to_zmp("https://s3.../data.zarr.zip", "data.zmp",
                prefix="sam-SR042-CL1.zarr/")

# Hydrated
zarr_zip_to_zmp("data.zarr.zip", "data.zmp", hydrate=True)

# Without duckn metadata injection
zarr_zip_to_zmp("data.zarr.zip", "data.zmp", duckn=False)
```

For Zarr v2 stores, the converter automatically translates `.zarray` metadata to Zarr v3 `zarr.json` format, including codec conversion (blosc, zstd, gzip). For OME-NGFF stores, duckn spatial metadata is optionally auto-generated from `multiscales` coordinate transformations (use `--no-duckn` to skip).

**Requirements**: zip must use `ZIP_STORED` (no zip-level compression) for virtual references. Chunk-level compression (blosc, zstd) is preserved as-is.

### NIfTI files

Build a ZMP from a `.nii` file where each axial slice is a contiguous byte range. No nibabel required — the NIfTI-1 header is parsed directly from the first 348 bytes.

```python
from duckn.nifti_convert import build_nifti_zmp

build_nifti_zmp("brain.nii", "brain.zmp")
```

The Zarr array shape is `(z, y, x)` — C-order with Z slowest — matching the NIfTI file's on-disk byte layout where X varies fastest. Each chunk `(1, y, x)` maps to one axial slice.

This works with NIfTI files inside uncompressed zip archives too. Compute the NIfTI data offset within the zip (zip local header offset + header size + NIfTI vox_offset), and each slice is at a known absolute byte position in the zip.

**Requirements**: uncompressed `.nii` (not `.nii.gz`), NIfTI-1 format, 3D volume, sform present.

### DICOM series (local files)

Build a ZMP from a directory of single-frame DICOM files. Each chunk references the pixel data byte range within the original `.dcm` file.

```python
from duckn.dicom_convert import build_local_zmp

build_local_zmp("dicom_series/", "series.zmp")
```

The converter scans headers with pydicom, sorts by slice position, computes spatial geometry, and builds per-slice byte-range entries using `get_pixel_data_range()`.

**Requirements**: uncompressed transfer syntax, single-frame DICOM files, one series per directory.

### DICOM series on IDC (S3 byte ranges)

Build a ZMP from an IDC DICOM series using the CRDC series UUID. Headers are fetched in parallel via HTTP range requests; no full files are downloaded.

```python
from duckn.idc_zmp import build_idc_zmp

# Virtual (byte-range references to S3)
build_idc_zmp("bfa2aab6-85de-4f92-b311-e6c8a52b9299", "series.zmp")

# With content hashes
build_idc_zmp("bfa2aab6-...", "series.zmp", content_hash=True)

# Hydrated (download pixel data into the ZMP)
build_idc_zmp("bfa2aab6-...", "series.zmp", inline_data=True)
```

Supports both uncompressed and JPEG-compressed DICOM. For compressed series, encapsulated frame offsets are scanned in parallel. Build time is typically 2-4 seconds for a 100-200 slice series.

### DICOM series via DICOMweb

Build a ZMP from any DICOMweb-compliant server using a single WADO-RS metadata request. No pixel data is fetched — chunk entries are WADO-RS frame retrieval URLs.

```python
from duckn.idc_zmp import build_dicomweb_zmp

build_dicomweb_zmp(
    "https://server/dicomWeb",
    study_uid="1.2.840...",
    series_uid="1.2.840...",
    output_path="series.zmp",
)
```

Build time is ~1 second regardless of series size (one HTTP request for all instance metadata). Chunk URIs are relative WADO-RS frame URLs (`instances/{sop_uid}/frames/1`) resolved against the series base URL.

Works with any DICOMweb server: Google Healthcare API, Orthanc, dcm4chee, IDC public proxy.

**Note**: WADO-RS returns pixel data wrapped in multipart/related MIME. The zarr-zmp HTTP resolver handles this automatically.

---

## Reading ZMPs

```python
from zarr_zmp import Manifest, ZMPStore
import zarr

# Open any ZMP
m = Manifest("data.zmp")
store = ZMPStore(m)

# Single array
arr = zarr.open_array(store, mode="r")
slice_data = arr[100, :, :]

# Multi-array group (e.g., OME-NGFF pyramid)
group = zarr.open_group(store, mode="r")
level0 = group["0"]  # full resolution
level2 = group["2"]  # 16x downsampled

# Access duckn metadata
meta = dict(arr.attrs).get("duckn", {})
print(meta["space"])        # "right-anterior-superior"
print(meta["space_origin"]) # [-98.0, -134.0, -72.0]
```

### Remote ZMPs

```python
# Open a ZMP hosted on a server
store = ZMPStore.from_url("https://cdn.example.com/brain.zmp")
arr = zarr.open_array(store, mode="r")
```

### Composing ZMPs

ZMPs can reference or embed other ZMPs:

```python
from zarr_zmp import Builder

builder = Builder()
builder.add("zarr.json", text='{"zarr_format": 3, "node_type": "group", "attributes": {}}')

# Mount by reference
builder.mount("ct", resolve={"http": {"url": "https://cdn.com/ct.zmp"}})

# Mount with embedded data
builder.mount("mri", data=Path("mri.zmp").read_bytes())

builder.write("study.zmp")
```

This creates a single ZMP that serves multiple datasets as a Zarr group:

```python
store = ZMPStore(Manifest("study.zmp"))
group = zarr.open_group(store, mode="r")
ct = group["ct"]    # → resolves through ct.zmp → S3 byte ranges
mri = group["mri"]  # → resolves through embedded mri.zmp → local files
```

---

## Querying ZMP metadata

The `zarr.json` entry in every ZMP is queryable via DuckDB without opening the Zarr store:

```sql
-- Single file
SELECT text->'$.attributes.duckn.space' as space,
       text->'$.shape' as shape
FROM read_parquet('brain.zmp')
WHERE path = 'zarr.json';

-- Across a collection
SELECT filename,
       text->'$.attributes.duckn.space' as space,
       text->'$.shape' as shape
FROM read_parquet('/data/*.zmp')
WHERE path = 'zarr.json';
```

For multi-array ZMPs (pyramids), each level has its own `zarr.json`:

```sql
SELECT path,
       text->'$.shape' as shape,
       text->'$.attributes.duckn.axes[0].space_direction[0]' as z_spacing
FROM read_parquet('pyramid.zmp')
WHERE path LIKE '%zarr.json';
```

---

## Virtual vs. hydrated

| | Virtual | Hydrated |
|---|---|---|
| ZMP size | 10-100 KB | Same as source data |
| Data location | External (S3, file, server) | Inline in ZMP |
| Requires network | Yes (for remote sources) | No |
| Chunk read speed | Network-dependent | Local disk speed |
| Content hashes | Optional | Optional |
| Use case | Index for data at rest | Portable self-contained archive |

Hydration is useful for:
- Creating portable archives that work offline
- Caching frequently accessed data
- Distributing datasets as single files

Virtual is better for:
- Indexing large datasets without copying data
- Accessing data that's already hosted (S3, PACS)
- Keeping the manifest small and fast to transfer

A ZMP can be partially hydrated — some chunks inline, others virtual. This supports selective caching of hot regions while leaving cold data remote.

---

## Performance characteristics

Measured on representative datasets:

| Source | Build time | ZMP size | Slice read |
|--------|-----------|----------|-----------|
| Local .zarr.zip (30 slices) | <1s | 14 KB | <1ms |
| Remote 47 GB OME-NGFF zip (2,830 chunks) | 17s | 62 KB | 460ms (S3) |
| Local NIfTI 0.5mm brain (378 slices) | <1s | 17 KB | 0.6ms |
| IDC CT 165 slices (uncompressed) | 2.6s | 20 KB | 250ms (S3) |
| IDC CT 172 slices (JPEG compressed) | 3.7s | 21 KB | 300ms (S3) |
| DICOMweb 165 instances | 1.2s | 27 KB | 700ms (proxy) |

Full volume reads from S3 achieve 25-50 MB/s throughput with parallel chunk fetching. Local reads from hydrated ZMPs achieve 1+ GB/s.
