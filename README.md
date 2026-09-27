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

- **Faithful conversion.** A DICOM series or a NIfTI file becomes the same data on the same grid, with the source's metadata kept as provenance you can use - JSON, never a second copy of a header to parse. Faithful to the data, not to the source's bytes: the source keeps those (dicom-spec §1).
- **Progressive disclosure.** A bare duckn store is a valid Zarr array any reader can open. Adding spatial metadata makes it an oriented image. Adding domain extensions carries the source format's metadata alongside it, never contradicting the array.
- **One format across domains** with shared tooling.
- **Full Zarr compatibility**, which brings high-performance data access on the desktop, in the cloud, and everything in between.
- **Extensible** to new imaging domains and evolutions of existing ones.

## Where duckn fits: a modern NRRD, in a plain zip file

duckn metadata works wherever Zarr v3 works - a directory, an object store, a zip file - because
it is nothing but attributes in a standard `zarr.json`. Its sweet spot is **a modern NRRD**:

- **NRRD's semantics.** Per-axis kinds and centering, a named space with directions and an
  origin, measurement frames, a value mapping - the coordinate model NRRD got right, stated
  once for every domain.
- **A plain zip file as the container.** Where NRRD prepends a text header to the data (or
  keeps a detached pair), a duckn file is a standard Zarr v3 zip store: `zarr.json` plus chunk
  members, in an ordinary zip any tool can list and any Zarr v3 reader can open. This is the
  recommended way to keep data locally: one file to copy, cache or hand to someone, not a
  directory tree. Chunks are stored, not deflated, so a reader range-reads them in place,
  from disk or over HTTP.
- **Sharding keeps the member count manageable.** A zip member per chunk grows with the array
  and with how finely it is chunked - a finely chunked volume is thousands of members, and every
  one is a central-directory entry a reader must list. Zarr v3's sharding codec packs many
  chunks into one member with an index at its end, so a large array stays a handful of members
  while reads stay chunk-sized. duckn's own converters do not write shards yet; any Zarr v3
  writer can (zarr-python: `create_array(..., shards=...)`), and duckn metadata is unaffected.
- **Many kinds of data in one file.** Zarr v3 groups put volumes, segmentations, derived fields
  and their metadata side by side in one zip.
- **Complete Zarr v3 compatibility.** Nothing duckn adds needs a duckn reader: a Zarr v3 library
  reads the array; a duckn reader also gets its axes, its place in space and what its values
  mean.

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
```

### CLI

```bash
duckn from-nrrd scan.nrrd scan.zarr
duckn from-nifti brain.nii.gz brain.zarr
duckn from-dicom dicom_series/ ct.zarr
duckn to-nrrd scan.zarr scan_out.nrrd
duckn to-nifti brain.zarr brain_out.nii.gz
duckn to-dicom ct.zarr ct_enhanced.dcm
duckn to-bids ct.zarr ct.json
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
      "version": "1.2",
      "space": "left-posterior-superior",
      "space_origin": [0.0, 0.0, 0.0],
      "value_transforms": [],
      "axes": [
        { "kind": "space", "centering": "cell", "space_direction": [0, 0, 3.0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0, 0.5, 0], "unit": "mm" },
        { "kind": "space", "centering": "cell", "space_direction": [0.5, 0, 0], "unit": "mm" }
      ]
    }
  }
}
```

`"value_transforms": []` says the stored values are the values. The common case is written
out, not implied: from convention 1.2 an absent `value_transforms` means the mapping is not
stated. The [core profile](docs/core-profile.md) is the one-page version of what a plain volume
needs.

duckn metadata can live in any Zarr v3 store — a directory on disk, an object store, or a `.zarr.zip` file. The metadata convention is independent of the storage mechanism.

## Versions

Three numbers, each about a different thing:

- **The convention** - the file format - is versioned in every file's `duckn.version`
  (currently **1.2**). It is the only version a reader acts on. Minor versions only add:
  a reader of 1.0 reads a 1.2 file safely, and every change of meaning is spelled out
  (duckn-spec §3.1 `version`).
- **Each extension** (`seg`, `dicom`, `nifti`, `nrrd`, ...) carries its own `version` in its
  block, independent of the convention. A `0.x` extension is unstable.
- **This library** has its own release numbers (0.x: its Python API may still change).
  A release says which convention versions it writes and reads; this one writes 1.2 where
  a file needs it and reads 1.0 through 1.2.

The "early stage" note above is about the project as a whole. The convention's version is a
promise to files: a file written today keeps the meaning its version gives it.

## Converters

| Format | To Zarr | From Zarr | Zero-copy |
|--------|---------|-----------|-----------|
| NRRD   | `nrrd_to_zarr()` | `zarr_to_nrrd()` | Both directions |
| NIfTI  | `nifti_to_zarr()` | `zarr_to_nifti()` | Possible |
| DICOM  | `dicom_to_zarr()` | `zarr_to_dicom()` | Streaming mode |
| DICOM SEG | `dicom_to_zarr()` | — | — |

## Extensions

Domain-specific metadata lives inside `duckn.extensions`. Extensions depend on duckn semantics (coordinate systems, axis structure) to be interpretable. Defined extensions:

- **dwmri** — Diffusion-weighted MRI (gradients, b-values, acquisition parameters)
- **seg** — 3D Slicer segmentation (segments, terminologies, label maps)
- **nifti** — NIfTI provenance (sform/qform codes, intent, legacy affines)
- **dicom** — DICOM provenance (tags, transfer syntax, anonymization status)
- **nrrd** — NRRD fields the convention does not model (`spacings` of a file with no `space`, `old min`/`old max`, `content`)

## Documentation

[docs/README.md](docs/README.md) indexes everything below and distinguishes
the current specifications from the archived design records.

Writing a plain volume? The [core profile](docs/core-profile.md) is one page.
Writing a reader or writer? Start with the
[implementer's guide](docs/implementers-guide.md) — the rules that are easy
to get wrong, with the bugs that motivated them.

## Specifications

- [duckn convention](docs/duckn-spec.md) — core metadata convention
- [DWI extension](docs/dwi-extension.md) — diffusion-weighted MRI
- [Segmentation extension](docs/segmentation-ext-spec.md) — segments, label values, roles, colors (0.9; 0.10 adds the group form)
- [NIfTI extension](docs/nifti-spec.md) — NIfTI provenance
- [DICOM extension](docs/dicom-spec.md) — DICOM provenance
- [FITS extension](docs/fits-extension.md) — astronomy FITS provenance (first pass at a non-medical imaging domain)
- [Microscopy extension](docs/microscopy-extension.md) — whole-slide and fluorescence imaging
- [Provenance extension](docs/provenance-extension.md) — general processing history
- [Space transforms](docs/transform-spec.md) — named coordinate spaces and affine transforms
- [Units](docs/units-spec.md) — structured unit system

## Appendix: ZMP (deprecated)

[ZMP](https://github.com/mhalle/zarr-zmp) (Zarr Manifest Parquet) is a virtual index that maps a
Zarr store's chunk paths to byte ranges in other files - zip archives, DICOM files on S3,
DICOMweb servers - and this library can build ZMPs carrying duckn metadata (`duckn from-idc`,
`duckn from-zarr-zip`, `zarr_zip_to_zmp()`, and the builders in the [ZMP guide](docs/zmp-guide.md)).

It is deprecated. A Parquet manifest asks too much of the community's readers, and Parquet
readers are not compact. Its successor, **valiz**, is a similar idea built on zip files and is
still under development. The ZMP builders remain for now and are not being extended. One known
limitation stays unfixed: a virtual reference to a DICOM file's pixel bytes cannot mask unused
high bits or sign-extend a value narrower than its container (Bits Stored < Bits Allocated), so
such data reads as container words.

## License

Apache-2.0. See [LICENSE](LICENSE).
