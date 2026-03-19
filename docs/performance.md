# ZMP Performance: Virtual DICOM Access

Benchmarks for reading DICOM imaging data through ZMP manifests with
virtual byte-range references into DICOM files at rest on S3 and GCS.

No pixel data is copied or converted — the ZMP manifest contains only
metadata and byte-range pointers. Compressed DICOM frames (JPEG,
JPEG 2000) are decoded at read time using the matching Zarr codec.

All reads use the standard Zarr Python API via `zarr.open_array(ZMPStore)`.

**Test environment:** macOS, Apple Silicon, residential internet (~200 Mbps),
US East Coast. S3 bucket: `idc-open-data` (us-east-1).
GCS bucket: `idc-open-data`.

---

## Read Performance (S3)

| Dataset | Chunks | Decoded size | ZMP size | 1 chunk | All chunks | Codec |
|---------|--------|-------------|----------|---------|------------|-------|
| MR Prostate (28 slices, 128x128) | 28 | 0.9 MB | 51 KB | 57 ms | 523 ms | raw |
| CT Chest (147 slices, 512x512) | 147 | 73.5 MB | 57 KB | 87 ms | 1,304 ms | JPEG |
| WSI Level 0 (3585x3655, 256x256 tiles) | 225 | 37.5 MB | 66 KB | 60 ms | 1,787 ms | JPEG 2000 |
| WSI Level 1 (1792x1827, 256x256 tiles) | 56 | 9.4 MB | 66 KB | — | 425 ms | JPEG 2000 |
| TotalSegmentator (33 organs, 58x512x512) | 33 | 478.5 MB | 233 KB | 3 ms | 129 ms | zstd (inline) |

**Notes:**
- "1 chunk" times are median of 5 runs with warm connection pool.
- TotalSegmentator is inline (data embedded in the ZMP), not virtual.
  3 ms per organ is decompression only, no network.
- WSI ZMP covers a 2-level pyramid (full resolution + 2x downsample)
  in a single manifest with OME-NGFF multiscales metadata.

## S3 vs GCS Comparison

| Dataset | S3 1 slice | GCS 1 slice | S3 all | GCS all |
|---------|-----------|-------------|--------|---------|
| MR Prostate (28) | 57 ms | — | 523 ms | — |
| CT Chest (147) | 87 ms | 117 ms | 1,304 ms | 1,850 ms |

S3 is approximately 1.4-1.6x faster from this test location, likely due
to geographic proximity.

## Local vs Remote Comparison

| Dataset | Local Zarr | S3 ZMP | Overhead |
|---------|-----------|--------|----------|
| MR Prostate (28 slices) | 17 ms | 523 ms | 31x (network dominated) |
| CT Chest (147 slices) | 792 ms | 1,304 ms | 1.6x |

For small datasets, network latency dominates. For larger datasets,
the overhead approaches the theoretical minimum (HTTP round-trip per
chunk batch).

## Manifest Build Time

Building a ZMP requires fetching only DICOM headers (~5 KB per file)
via progressive HTTP range requests. No pixel data is downloaded.

| Dataset | S3 build | GCS build | Headers fetched |
|---------|---------|-----------|-----------------|
| MR Prostate (28 files) | 2.5 s | 4.0 s | 28 |
| CT Chest (147 files, compressed) | 25.9 s | 32.0 s | 147 + 147 frame scans |

Compressed DICOM requires an additional range request per file to
scan the encapsulated frame offsets, doubling the build time.

## Manifest Sizes

| Manifest | Size | Contents |
|----------|------|----------|
| MR Prostate (28 slices, raw) | 51 KB | 28 virtual chunk refs + metadata |
| CT Chest (147 slices, JPEG) | 57 KB | 147 virtual chunk refs + metadata |
| WSI Pyramid (281 tiles, JP2) | 66 KB | 281 virtual chunk refs + 2 arrays + OME metadata |
| TotalSegmentator (33 organs, inline) | 233 KB | 33 zstd-compressed chunks + segment metadata |

All manifests include full duckn metadata (spatial embedding, axes,
coordinate system) and DICOM provenance tags.

## Compression Ratios (TotalSegmentator Segmentation)

| Format | Size | Ratio |
|--------|------|-------|
| Dense 4D raw (33x58x512x512 uint8) | 478 MB | 1x |
| DICOM SEG (binary, bit-packed, uncompressed) | 14.2 MB | 34x |
| duckn Zarr (auto-chunked, zstd) | 915 KB | 523x |
| ZMP inline (1 chunk/segment, zstd) | 233 KB | 2,053x |
| ZMP 3D label map (single chunk, zstd) | 54 KB | 8,861x |

## Connection Pooling Impact

HTTP connection reuse via `httpx.AsyncClient` with HTTP/2:

| Configuration | 225 WSI tiles from S3 |
|---------------|----------------------|
| No pooling (new connection per tile) | 6,075 ms |
| Connection pooling + HTTP/2 | 1,787 ms |
| Raw HTTP ceiling (concurrency=50) | 1,883 ms |

Connection pooling provides a 3x speedup by eliminating TCP/TLS
handshake overhead. Zarr's built-in concurrency (`async.concurrency=10`)
is close to the raw network ceiling.

## Architecture

```
┌──────────────┐     ┌─────────────┐     ┌──────────────────┐
│  ZMP File    │────>│  ZMPStore   │────>│  Zarr Array API  │
│  (manifest)  │     │  (virtual)  │     │  arr[50:60]      │
└──────────────┘     └──────┬──────┘     └──────────────────┘
                            │
                   ┌────────┴────────┐
                   │  _fetch_uri()   │
                   │  HTTP range req │
                   └────────┬────────┘
                            │
              ┌─────────────┴─────────────┐
              │  DICOM files at rest      │
              │  (S3, GCS, local)         │
              │  No conversion needed     │
              └───────────────────────────┘
```

Each Zarr chunk read triggers one HTTP range request to fetch the
compressed frame bytes from the original DICOM file, followed by
image codec decode (JPEG, JPEG 2000, or raw bytes). The ZMP manifest
maps Zarr chunk paths to byte ranges within DICOM files.
