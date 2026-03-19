# duckn

A Zarr-based imaging file format with lovely nested semantics.

> **Early stage.** duckn is at the early conceptual phase. The specification, API, and on-disk format are all subject to change. Do not count on stability.

## Why duckn?

Imaging formats are balkanized. DICOM owns clinical imaging, NIfTI owns neuroimaging, NRRD is a robust and extensible option popular with researchers. Each community reinvents the same concepts — from data handling to coordinate systems to domain-specific metadata — in incompatible ways, and not always very well. They aren't interoperable, converting between them is lossy, and none of them capture the richness, correctness, versatility, or ease of use that the ideal imaging format would have.

duckn separates the problem into three composable layers:

1. **Zarr** handles storage — chunking, compression, typing, and cloud-native access.
2. **NRRD's coordinate model** handles the spatial problem — per-axis semantics, spatial orientation, centering, and measurement frames provide a precise, universal mapping between stored arrays and physical space.
3. **Typed extensions** handle domain semantics — DICOM tags, NIfTI intent codes, and other format-specific metadata are captured in extensions that preserve enough information to accurately round-trip well-formed files through duckn.

Semantic elements are clearly separated so they don't overlap the core Zarr and NRRD-inspired layers. Where the bytes come from and how they translate into a coordinate frame is always consistent, regardless of what domain extensions are present. A duckn store can even carry simultaneous DICOM and NIfTI metadata — the accuracy of that information is up to the data's creator, but inconsistencies in domain metadata don't prevent the image from being read or interpreted in the correct coordinate system. These are issues that constantly plague researchers and cause errors.

**What the user gets:**

- **Round-trip fidelity.** Convert a DICOM series or NIfTI file to duckn and back without losing metadata.
- **Progressive disclosure.** A bare duckn store is a valid Zarr array any reader can open. Adding spatial metadata makes it an oriented image. Adding domain extensions makes it a lossless representation of the source format's semantics.
- **One format across domains** with shared tooling.
- **Full Zarr compatibility**, which brings high-performance data access on the desktop, in the cloud, and everything in between.
- **Extensible** to new imaging domains and evolutions of existing ones.

## Design principles

- **The storage format is Zarr.** No new file types, no custom parsers. All metadata lives in `attributes` of a standard `zarr.json`.
- **Absent means unknown.** Omit optional fields entirely rather than inventing defaults.
- **Each axis is a coherent object.** Per-axis properties are bundled together, not scattered across parallel arrays.
- **Memory layout and spatial embedding are orthogonal.** Axis ordering describes storage; spatial fields describe the world.

## Installation

```bash
pip install duckn
```

Optional dependencies for format-specific converters:

```bash
pip install duckn[nifti]    # NIfTI support (nibabel)
pip install duckn[dicom]    # DICOM support (pydicom)
```

## Quick start

### Python

```python
import duckn

# Read a duckn Zarr store
data, meta = duckn.read_duckn("brain.zarr")
print(meta.space)          # "right-anterior-superior"
print(meta.axes[0].kind)   # "space"

# Convert from NRRD
duckn.nrrd_to_zarr("scan.nrrd", "scan.zarr")

# Convert from NIfTI
duckn.nifti_to_zarr("brain.nii.gz", "brain.zarr")

# Convert from DICOM
duckn.dicom_to_zarr("dicom_series/", "ct.zarr")

# Convert back
duckn.zarr_to_nrrd("scan.zarr", "scan_out.nrrd")
duckn.zarr_to_nifti("brain.zarr", "brain_out.nii.gz")
duckn.zarr_to_dicom("ct.zarr", "ct_enhanced.dcm")

# Generate BIDS sidecar
from duckn.bids import duckn_to_bids_sidecar
sidecar = duckn_to_bids_sidecar(meta)
```

### CLI

```bash
duckn to-zarr scan.nrrd scan.zarr
duckn to-nrrd scan.zarr scan_out.nrrd
duckn from-nifti brain.nii.gz brain.zarr
duckn to-nifti brain.zarr brain_out.nii.gz
duckn from-dicom dicom_series/ ct.zarr
duckn to-bids ct.zarr ct.json           # BIDS sidecar from Zarr
duckn to-bids dicom_series/             # BIDS sidecar from DICOM
duckn info scan.zarr
duckn header scan.zarr
duckn roundtrip scan.nrrd
```

### JavaScript

```js
import vtkDucknReader from '@duckn/reader';

const reader = vtkDucknReader.newInstance();
reader.setUrl('https://example.com/brain.zarr');
const imageData = await reader.loadData();
```

## What's in a duckn store

A duckn store is a standard Zarr V3 array with a `"duckn"` key in its attributes:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [60, 256, 256],
  "data_type": "int16",
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "left-posterior-superior",
      "space_origin": [0.0, 0.0, 0.0],
      "axes": [
        { "kind": "space", "centering": "cell", "space_direction": [0, 0, 3.0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 0.5, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0.5, 0, 0], "unit": "mm" }
      ]
    }
  }
}
```

## Converters

| Format | To Zarr | From Zarr | Zero-copy |
|--------|---------|-----------|-----------|
| NRRD   | `nrrd_to_zarr()` | `zarr_to_nrrd()` | Both directions |
| NIfTI  | `nifti_to_zarr()` | `zarr_to_nifti()` | — |
| DICOM  | `dicom_to_zarr()` | `zarr_to_dicom()` | Streaming mode |
| DICOM SEG | `dicom_to_zarr()` | — | — |
| BIDS sidecar | — | `duckn_to_bids_sidecar()` | — |

Zero-copy mode copies compressed data blobs directly without decompression/recompression (raw and gzip encodings).

## Extensions

Domain-specific metadata lives inside `duckn.extensions`. Extensions depend on duckn semantics (coordinate systems, axis structure) to be interpretable. Defined extensions:

- **dwmri** — Diffusion-weighted MRI (gradients, b-values, acquisition parameters)
- **slicerseg** — 3D Slicer segmentation (segments, terminologies, label maps)
- **nifti** — NIfTI provenance (sform/qform codes, intent, legacy affines)
- **dicom** — DICOM provenance (tags, transfer syntax, anonymization status)

## ZMP: Virtual Access to DICOM at Rest

duckn stores can be represented as [ZMP](https://github.com/mhalle/zarr-zmp)
manifests — lightweight Parquet files that map Zarr chunk paths to byte
ranges within existing DICOM files on S3 or GCS. No data is copied or
converted; compressed DICOM frames (JPEG, JPEG 2000) are decoded on
demand using matching Zarr codecs.

A 59 KB ZMP manifest gives random-access to any slice of a 147-slice
CT scan stored as DICOM on S3, readable through the standard Zarr API.

See [performance benchmarks](docs/performance.md) for read speeds.

## Specifications

- [duckn convention](docs/duckn-spec.md) — core metadata convention
- [DWI extension](docs/dwi-extension.md) — diffusion-weighted MRI
- [Segmentation extension](docs/segmentation-ext-spec.md) — label maps and segments
- [NIfTI extension](docs/nifti-spec.md) — NIfTI provenance
- [DICOM extension](docs/dicom-spec.md) — DICOM provenance
- [FITS extension](docs/fits-extension.md) — astronomy FITS provenance (first pass at a non-medical imaging domain)
- [Microscopy extension](docs/microscopy-extension.md) — whole-slide and fluorescence imaging
- [Provenance extension](docs/provenance-extension.md) — general processing history
- [Units](docs/units-spec.md) — structured unit system

## License

Apache-2.0. See [LICENSE](LICENSE).
