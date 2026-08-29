# duckn documentation

duckn is a metadata convention layered inside Zarr V3: a store remains a
valid Zarr array that any reader can open, and readers that understand the
convention additionally get axis semantics, spatial embedding, and value
interpretation.

**Start here:** [duckn-spec.md](duckn-spec.md) is the convention. If you
are writing code against duckn rather than reading the specification,
[implementers-guide.md](implementers-guide.md) covers the rules that are
easy to get wrong.

## The convention

| Document | What it defines |
|---|---|
| [duckn-spec.md](duckn-spec.md) | The convention: top-level and per-axis fields, value interpretation, consistency rules. Authoritative for the set of top-level keys. |
| [units-spec.md](units-spec.md) | Structured unit objects and the `unit_systems` registry (convention 1.1+). |
| [transform-spec.md](transform-spec.md) | The `space_transforms` field: relating an array's space to templates, atlases, other acquisitions (convention 1.1). |

## Extensions

Each defines one key under the convention's `extensions` object. A reader
that does not recognize an extension ignores it.

| Extension | Document | Purpose |
|---|---|---|
| `seg` | [segmentation-ext-spec.md](segmentation-ext-spec.md) | Segmentations: segments, label values, designations, DICOM SEG classification. |
| `dicom` | [dicom-spec.md](dicom-spec.md) | DICOM provenance — describes the *source object*, never the array it is attached to. |
| `nifti` | [nifti-spec.md](nifti-spec.md) | NIfTI header provenance. |
| `dwmri` | [dwi-extension.md](dwi-extension.md) | Diffusion-weighted MRI: gradients, b-values, B-matrices. |
| `fits` | [fits-extension.md](fits-extension.md) | FITS header provenance (astronomy). |
| `microscopy` | [microscopy-extension.md](microscopy-extension.md) | Microscopy acquisition metadata. |
| `provenance` | [provenance-extension.md](provenance-extension.md) | Format-agnostic lineage: origin, processing history, authorship. |

## Guides

| Document | Purpose |
|---|---|
| [implementers-guide.md](implementers-guide.md) | Writing readers and writers: calibration, axis order, geometry, derivation. |
| [zmp-guide.md](zmp-guide.md) | Building ZMP manifests — Zarr-compatible access to data at rest in other formats. |
| [performance.md](performance.md) | Benchmarks for virtual DICOM access through ZMP. |

## Archive

[archive/](archive/) holds historical records — superseded plans and
decision records. **Nothing there is normative**; see
[archive/README.md](archive/README.md) for what each document is and why it
was kept.

## A note on status

Every document here is marked **Draft**. The convention is at version 1.1;
extension versions are independent and declared in each extension's own
`version` field.
