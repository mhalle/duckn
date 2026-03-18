# Data Provenance Extension for duckn

**Extension name:** `provenance`
**Version:** 1.0
**Status:** Draft

---

## 1. Purpose

This document defines the `provenance` extension for the duckn convention. It records the origin, processing history, and authorship of array data — who created it, what it was derived from, what tools transformed it, and under what conditions — as structured JSON within a Zarr store.

The other duckn provenance extensions (DICOM, NIfTI, FITS) preserve *format-specific* header fields from source files. This extension is *format-agnostic*: it captures the data's lineage regardless of what file format it came from, whether any conversion occurred, or whether the data was computed rather than acquired. The two are complementary. A CT scan converted from DICOM might carry both a `dicom` extension (preserving DICOM header fields) and a `provenance` extension (recording who converted it, with what tool, and from what source files).

The extension addresses three needs:

- **Origin.** Where did this data come from? A scanner, a simulation, a manual annotation, a published dataset? What were the source files or identifiers?

- **Processing history.** What transformations were applied? Resampling, filtering, registration, segmentation model inference? In what order, with what parameters, using what software?

- **Attribution.** Who created or contributed to this data? Under what license or terms is it shared? What should be cited?

### What this extension is not

This is not a workflow execution engine, a full W3C PROV-O graph, or a reproducibility container. It captures the metadata a downstream consumer needs to understand the data's history and give proper credit — without requiring external infrastructure to interpret it. If full computational reproducibility is needed (exact container images, dependency graphs, DAG execution logs), use a dedicated workflow system and link to its outputs from this extension.

---

## 2. Relationship to duckn Convention Fields

This extension does not overlap with convention fields. The convention describes *what the array is* (axes, spatial embedding, value semantics). This extension describes *how the array came to be* (origin, processing, attribution). There are no fields to partition between the two.

The extension may reference convention fields by implication — for instance, a processing step that resampled the data produced the current `space_origin` and `space_direction` values — but it does not duplicate or override them.

Other extensions interact with this one as follows:

| Extension | Relationship |
|---|---|
| `dicom` | Records DICOM-specific source metadata. The `provenance` extension can reference the DICOM source in `sources` without duplicating the header fields. |
| `nifti` | Records NIfTI-specific source metadata. Same relationship as DICOM. |
| `fits` | Records FITS-specific source metadata. Same relationship as DICOM. |
| `segmentation` | Describes segment semantics. A `provenance` processing step might record the segmentation model that produced those segments. |

---

## 3. Extension Structure

The `provenance` extension is declared at the top level of the `"duckn"` object's `"extensions"`.

```json
"extensions": {
  "provenance": {
    "version": "1.0",
    "sources": [ ... ],
    "processing": [ ... ],
    "attribution": { ... }
  }
}
```

All fields within the extension except `version` are optional. Their presence or absence carries meaning: a missing field means the information is unknown or not applicable. This follows the convention's "absent means unknown" principle.

### 3.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification.

```json
"version": "1.0"
```

#### `schema`

A URL pointing to a schema or specification document for the extension. Optional.

```json
"schema": "https://example.org/provenance-zarr/v1.0/schema.json"
```

#### `sources`

An array of objects describing the input data from which this array was derived. See §4.

#### `processing`

An array of objects describing transformations applied to the data, in chronological order. See §5.

#### `attribution`

An object describing authorship, licensing, and citation information. See §6.

---

## 4. Sources

The `sources` array records where the data came from. Each entry describes one input — a file, a dataset, a scan, or another Zarr array.

```json
"sources": [
  {
    "type": "file",
    "format": "DICOM",
    "path": "series/1.2.840.113619.2.55.3.604688119.969.1069843699.84/",
    "description": "Original DICOM series, 300 slices"
  }
]
```

### 4.1 Source Fields

#### `type`

The kind of source. Recommended values:

| Value | Meaning |
|---|---|
| `"file"` | A file or directory on a filesystem |
| `"url"` | A resource identified by URL |
| `"doi"` | A dataset or publication identified by DOI |
| `"database"` | A record in a database or repository |
| `"acquisition"` | Data acquired directly from an instrument |
| `"computation"` | Data generated by simulation or computation |

Other values are permitted. A reader that encounters an unknown type should treat the source as opaque provenance.

```json
"type": "doi"
```

#### `format`

The file format or data format of the source. Free-form string. Common values: `"DICOM"`, `"NIfTI"`, `"NRRD"`, `"FITS"`, `"TIFF"`, `"HDF5"`, `"Zarr"`, `"CSV"`, `"raw"`.

```json
"format": "NIfTI"
```

Omit when not applicable (e.g., for `"acquisition"` or `"computation"` types).

#### `path`

A file path, relative or absolute, identifying the source. For local files.

```json
"path": "derivatives/sub-01/anat/sub-01_T1w.nii.gz"
```

#### `url`

A URL identifying or locating the source.

```json
"url": "https://doi.org/10.7937/K9/TCIA.2017.3r3fvz08"
```

#### `doi`

A DOI identifying the source dataset or publication.

```json
"doi": "10.7937/K9/TCIA.2017.3r3fvz08"
```

#### `identifier`

A free-form identifier for the source within its own system. This is the escape hatch for identifiers that are not DOIs, URLs, or file paths — accession numbers, database keys, internal tracking IDs.

```json
"identifier": "TCIA-LIDC-IDRI-0001"
```

#### `description`

A human-readable description of the source.

```json
"description": "T1-weighted structural MRI, 1mm isotropic, acquired 2024-01-15"
```

#### `created`

An ISO 8601 datetime string indicating when the source data was created or acquired.

```json
"created": "2024-01-15T14:30:25Z"
```

#### `note`

A free-form string for anything that does not fit in other fields — data quality observations, caveats, context that a downstream consumer should be aware of.

```json
"note": "Motion artifacts visible in the last 20 slices."
```

### 4.2 Multiple Sources

When an array is derived from multiple inputs (e.g., a registered atlas built from many subjects, or a difference image from two time points), each input is a separate entry in the `sources` array.

```json
"sources": [
  {
    "type": "file",
    "format": "NIfTI",
    "path": "sub-01_T1w.nii.gz",
    "description": "Baseline scan"
  },
  {
    "type": "file",
    "format": "NIfTI",
    "path": "sub-01_T1w_followup.nii.gz",
    "description": "6-month follow-up scan"
  }
]
```

The order of entries in `sources` is not semantically meaningful unless a processing step references them by index (see §5.3).

---

## 5. Processing

The `processing` array records the transformations applied to the data, in chronological order. Each entry describes one step — a conversion, a filter, a registration, a model inference — with enough detail to understand what happened.

```json
"processing": [
  {
    "name": "DICOM to NIfTI conversion",
    "software": {
      "name": "dcm2niix",
      "version": "1.0.20240202"
    },
    "executed": "2024-02-01T10:00:00Z"
  },
  {
    "name": "Brain extraction",
    "software": {
      "name": "FreeSurfer mri_synthstrip",
      "version": "7.4.1"
    },
    "parameters": {
      "border": 1
    },
    "executed": "2024-02-01T10:05:00Z"
  }
]
```

### 5.1 Processing Step Fields

#### `name`

A short human-readable name for the step. Required within each processing step.

```json
"name": "Resample to 1mm isotropic"
```

#### `description`

A longer human-readable description of what the step does and why.

```json
"description": "Resampled from native resolution (0.5mm in-plane, 2mm slice) to 1mm isotropic using trilinear interpolation for atlas registration compatibility."
```

#### `software`

An object identifying the tool that performed the step.

```json
"software": {
  "name": "ANTs",
  "version": "2.5.0",
  "url": "https://github.com/ANTsX/ANTs",
  "command": "antsRegistration -d 3 -o [output_, output_warped.nii.gz] ..."
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Software name. Required within `software`. |
| `version` | string | Version string. |
| `url` | string | URL to the software repository or documentation. |
| `command` | string | The command line or function call used. |

#### `method`

An object identifying the protocol, workflow definition, or method specification that this step followed. This is distinct from `software` — the software is the tool that executed the step; the method is the procedure it followed. A step may have both (the software that ran the protocol), either, or neither.

The field provides a natural home for protocols.io entries, lab SOPs, CWL or Nextflow workflow definitions, published methods sections, or any other citable description of a procedure.

```json
"method": {
  "name": "iDISCO+ whole-brain clearing",
  "doi": "10.17504/protocols.io.bji2kkge",
  "url": "https://dx.doi.org/10.17504/protocols.io.bji2kkge",
  "version": "2"
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Human-readable name of the method. Required within `method`. |
| `doi` | string | DOI for the method, if it has one. Protocols.io protocols have DOIs. |
| `url` | string | URL to the method definition or documentation. |
| `version` | string | Version or revision of the method that was followed. |

#### `parameters`

An object containing the parameters used for this step. Keys and values are free-form JSON, determined by the software. This is *not* a standardized parameter vocabulary — it is a record of what was passed to the tool.

```json
"parameters": {
  "interpolation": "trilinear",
  "target_spacing": [1.0, 1.0, 1.0],
  "reference": "MNI152_T1_1mm.nii.gz"
}
```

#### `executed`

An ISO 8601 datetime string indicating when the step was executed.

```json
"executed": "2024-02-01T10:05:00Z"
```

#### `note`

A free-form string for anything that does not fit in other fields — runtime observations, workarounds, reasons for parameter choices.

```json
"note": "Run twice; first attempt hit OOM at slice 245 with default chunk size."
```

#### `inputs`

An array of integers referencing entries in the `sources` array by 0-based index, identifying which sources were consumed by this step. Optional. When absent, the step is assumed to operate on the output of the previous step (or on all sources if it is the first step).

```json
"inputs": [0, 1]
```

#### `environment`

An object recording the computational environment. Useful for reproducibility.

```json
"environment": {
  "os": "Ubuntu 22.04",
  "architecture": "x86_64",
  "container": "docker://ants/ants:2.5.0",
  "gpu": "NVIDIA A100"
}
```

All fields within `environment` are free-form strings. Include whatever is relevant to reproducibility; omit the rest.

### 5.2 Step Ordering

Steps in the `processing` array are ordered chronologically — the first entry is the earliest transformation, the last is the most recent. The array represents a linear pipeline. For branching or merging workflows, the `inputs` field can reference specific sources; for true DAG provenance, link to an external workflow record.

### 5.3 Referencing Sources from Steps

The `inputs` field on a processing step uses 0-based indices into the `sources` array. This is useful when a step consumes specific sources:

```json
"sources": [
  { "type": "file", "path": "moving.nii.gz", "description": "Moving image" },
  { "type": "file", "path": "fixed.nii.gz", "description": "Fixed image (atlas)" }
],
"processing": [
  {
    "name": "Affine registration",
    "software": { "name": "ANTs", "version": "2.5.0" },
    "inputs": [0, 1]
  }
]
```

---

## 6. Attribution

The `attribution` object records authorship, licensing, and citation information for the data.

```json
"attribution": {
  "creators": [
    {
      "name": "Jane Doe",
      "orcid": "0000-0002-1234-5678",
      "affiliation": "Example University, Department of Radiology"
    }
  ],
  "license": "CC-BY-4.0",
  "license_url": "https://creativecommons.org/licenses/by/4.0/",
  "citation": "Doe J, et al. (2024). A multi-site brain atlas. NeuroImage, 280, 120345.",
  "doi": "10.1016/j.neuroimage.2024.120345",
  "funding": [
    "NIH R01-EB012345",
    "NSF Award 2345678"
  ]
}
```

### 6.1 Attribution Fields

#### `creators`

An array of objects identifying the people or organizations who created this data.

```json
"creators": [
  {
    "name": "Jane Doe",
    "orcid": "0000-0002-1234-5678",
    "affiliation": "Example University"
  },
  {
    "name": "Example Lab",
    "ror": "https://ror.org/0000000000"
  }
]
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Person or organization name. Required within each creator. |
| `orcid` | string | ORCID iD (for individuals). Format: `"0000-0002-1234-5678"`. |
| `ror` | string | ROR identifier (for organizations). |
| `affiliation` | string | Institutional affiliation. |
| `role` | string | Role in data creation (e.g., `"principal investigator"`, `"annotator"`, `"data curator"`). |

The order of entries in `creators` may indicate contribution order (first = primary creator), but this is not required.

#### `license`

An SPDX license identifier string, or a short license name.

```json
"license": "CC-BY-4.0"
```

Recommended values: `"CC-BY-4.0"`, `"CC-BY-SA-4.0"`, `"CC-BY-NC-4.0"`, `"CC0-1.0"`, `"Apache-2.0"`, `"MIT"`. Other SPDX identifiers are permitted. For custom licenses, use `"custom"` and provide `license_url`.

#### `license_url`

A URL pointing to the full license text.

```json
"license_url": "https://creativecommons.org/licenses/by/4.0/"
```

#### `citation`

A human-readable citation string — the preferred way to cite this data.

```json
"citation": "Doe J, et al. (2024). A multi-site brain atlas. NeuroImage, 280, 120345."
```

#### `doi`

The DOI for this data or the publication that describes it.

```json
"doi": "10.1016/j.neuroimage.2024.120345"
```

#### `funding`

An array of strings identifying funding sources.

```json
"funding": ["NIH R01-EB012345"]
```

#### `terms`

A human-readable string describing any usage terms, restrictions, or conditions beyond the license. Use for data use agreements, embargo notices, or institutional policies that are not captured by a standard license.

```json
"terms": "Data use requires IRB approval and a signed DUA. Contact data-access@example.edu."
```

#### `note`

A free-form string for anything that does not fit in other fields — acknowledgment requests, IRB approval dates, embargo timelines.

```json
"note": "Data sharing approved by IRB committee 2024-03-15. Embargo lifts 2025-01-01."
```

---

## 7. Consistency Rules

- The `version` field is required. All other fields are optional.
- `sources` entries should have at least one identifying field (`path`, `url`, `doi`, or `identifier`). A source with only a `description` is permitted but less useful.
- `processing` entries must have a `name` field. All other step fields are optional.
- `processing` is ordered chronologically. The first element is the earliest step.
- `inputs` indices in processing steps must be valid 0-based indices into `sources`. Out-of-range indices are an error.
- `creators` entries must have a `name` field. All other creator fields are optional.
- `null` values are not used in this extension. Omit fields that are unknown rather than setting them to `null`. (There is no redaction use case here, unlike the DICOM extension where `null` signals anonymized fields.)
- Datetime fields (`created`, `executed`) must be ISO 8601 format. UTC is recommended. If the timezone is unknown, omit the timezone designator.

---

## 8. Examples

### 8.1 CT Converted from DICOM

A CT scan converted from DICOM, carrying both the `dicom` extension (for header fields) and the `provenance` extension (for conversion history):

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [300, 512, 512],
  "data_type": "uint16",
  "dimension_names": ["k", "j", "i"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [30, 512, 512] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "left-posterior-superior",
      "space_origin": [-249.5, -249.5, -150.0],
      "sample_units": "HU",
      "value_transforms": [
        { "name": "linear", "parameters": { "slope": 1.0, "intercept": -1024.0 } }
      ],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 5.0],
          "thickness": 5.0,
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.703, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.703, 0, 0],
          "unit": "mm"
        }
      ],
      "extensions": {
        "dicom": {
          "version": "1.0",
          "anonymized": true,
          "tags": {
            "Modality": "CT",
            "SeriesInstanceUID": "1.2.840.113619.2.55.3.604688119.969.1069843699.84",
            "Manufacturer": "GE MEDICAL SYSTEMS",
            "ManufacturerModelName": "LightSpeed16",
            "KVP": 120,
            "PatientName": null,
            "PatientID": null
          }
        },
        "provenance": {
          "version": "1.0",
          "sources": [
            {
              "type": "file",
              "format": "DICOM",
              "path": "raw/series-84/",
              "description": "Original DICOM series, 300 axial slices"
            }
          ],
          "processing": [
            {
              "name": "DICOM anonymization",
              "software": {
                "name": "DicomCleaner",
                "version": "4.0.0"
              },
              "description": "Removed patient identifiers per IRB protocol 2024-0042.",
              "executed": "2024-03-01T09:00:00Z"
            },
            {
              "name": "DICOM to Zarr conversion",
              "software": {
                "name": "dicom2zarr",
                "version": "0.3.1",
                "url": "https://github.com/example/dicom2zarr"
              },
              "parameters": {
                "compression": "zstd",
                "compression_level": 3,
                "chunk_size": [30, 512, 512]
              },
              "executed": "2024-03-01T09:15:00Z"
            }
          ],
          "attribution": {
            "creators": [
              {
                "name": "Example Medical Center, Department of Radiology"
              }
            ],
            "license": "CC-BY-NC-4.0",
            "terms": "Research use only. Not for clinical decision-making."
          }
        }
      }
    }
  }
}
```

This shows the two extensions coexisting: `dicom` carries the scanner parameters, `provenance` records what happened to the data after it left the scanner.

### 8.2 Registered Brain Atlas

A population-average brain atlas built from multiple subjects:

```json
"extensions": {
  "provenance": {
    "version": "1.0",
    "sources": [
      {
        "type": "database",
        "identifier": "IXI dataset",
        "url": "https://brain-development.org/ixi-dataset/",
        "description": "581 T1-weighted brain MRIs from healthy subjects"
      }
    ],
    "processing": [
      {
        "name": "Brain extraction",
        "software": {
          "name": "FreeSurfer mri_synthstrip",
          "version": "7.4.1"
        }
      },
      {
        "name": "Affine registration to MNI",
        "software": {
          "name": "ANTs",
          "version": "2.5.0"
        },
        "parameters": {
          "transform": "Affine",
          "metric": "MI",
          "reference": "MNI152_T1_1mm.nii.gz"
        }
      },
      {
        "name": "Nonlinear registration to MNI",
        "software": {
          "name": "ANTs",
          "version": "2.5.0"
        },
        "parameters": {
          "transform": "SyN",
          "metric": "CC",
          "radius": 4,
          "reference": "MNI152_T1_1mm.nii.gz"
        }
      },
      {
        "name": "Template construction",
        "method": {
          "name": "ANTs multivariate template construction",
          "url": "https://doi.org/10.1016/j.neuroimage.2010.09.025"
        },
        "software": {
          "name": "ANTs buildtemplateparallel.sh",
          "version": "2.5.0"
        },
        "parameters": {
          "iterations": 4,
          "gradient_step": 0.2
        },
        "environment": {
          "os": "Ubuntu 22.04",
          "container": "docker://antsx/ants:2.5.0"
        }
      }
    ],
    "attribution": {
      "creators": [
        {
          "name": "Jane Doe",
          "orcid": "0000-0002-1234-5678",
          "affiliation": "Example University, Neuroimaging Lab",
          "role": "principal investigator"
        },
        {
          "name": "John Smith",
          "orcid": "0000-0001-8765-4321",
          "affiliation": "Example University, Neuroimaging Lab",
          "role": "data curator"
        }
      ],
      "license": "CC-BY-4.0",
      "license_url": "https://creativecommons.org/licenses/by/4.0/",
      "citation": "Doe J, Smith J. (2024). A population-average brain atlas from 581 subjects. NeuroImage, 280, 120345.",
      "doi": "10.1016/j.neuroimage.2024.120345",
      "funding": [
        "NIH R01-EB012345",
        "NSF Award 2345678"
      ]
    }
  }
}
```

### 8.3 AI Segmentation Output

A segmentation mask produced by a deep learning model:

```json
"extensions": {
  "segmentation": {
    "version": "1.0",
    "segments": [
      {
        "label": 1,
        "name": "Liver",
        "dicom": {
          "category": { "CodeValue": "T-D0050", "CodingSchemeDesignator": "SRT", "CodeMeaning": "Tissue" },
          "type": { "CodeValue": "T-62000", "CodingSchemeDesignator": "SRT", "CodeMeaning": "Liver" }
        }
      }
    ]
  },
  "provenance": {
    "version": "1.0",
    "sources": [
      {
        "type": "file",
        "format": "Zarr",
        "path": "../ct_volume/",
        "description": "Source CT volume"
      }
    ],
    "processing": [
      {
        "name": "Liver segmentation",
        "software": {
          "name": "TotalSegmentator",
          "version": "2.0.5",
          "url": "https://github.com/wasserth/TotalSegmentator"
        },
        "parameters": {
          "task": "total",
          "fast": false,
          "roi_subset": ["liver"]
        },
        "environment": {
          "gpu": "NVIDIA A100 40GB",
          "container": "docker://wasserth/totalsegmentator:2.0.5"
        },
        "executed": "2024-06-15T14:22:00Z"
      }
    ],
    "attribution": {
      "creators": [
        {
          "name": "Automated Pipeline",
          "role": "computation"
        }
      ],
      "terms": "Segmentation is algorithmically generated and has not been reviewed by a clinician."
    }
  }
}
```

### 8.4 Public Dataset from a Repository

An array downloaded from a public data repository:

```json
"extensions": {
  "provenance": {
    "version": "1.0",
    "sources": [
      {
        "type": "doi",
        "doi": "10.7937/K9/TCIA.2017.3r3fvz08",
        "url": "https://www.cancerimagingarchive.net/collection/lidc-idri/",
        "identifier": "LIDC-IDRI-0001",
        "description": "Lung Image Database Consortium, case 0001"
      }
    ],
    "attribution": {
      "citation": "Armato SG III, et al. The Lung Image Database Consortium (LIDC) and Image Database Resource Initiative (IDRI). Medical Physics, 38(2):915-931, 2011.",
      "doi": "10.1118/1.3528204",
      "license": "CC-BY-3.0"
    }
  }
}
```

### 8.5 Simulation Output

A synthetic phantom generated by computation:

```json
"extensions": {
  "provenance": {
    "version": "1.0",
    "sources": [
      {
        "type": "computation",
        "description": "Monte Carlo X-ray transport simulation of a digital anthropomorphic phantom"
      }
    ],
    "processing": [
      {
        "name": "X-ray transport simulation",
        "software": {
          "name": "GATE",
          "version": "9.3",
          "url": "https://opengate.readthedocs.io/"
        },
        "parameters": {
          "photon_histories": 1e9,
          "energy_kev": 120,
          "phantom": "XCAT v2.0"
        },
        "environment": {
          "os": "CentOS 7",
          "gpu": "NVIDIA V100",
          "wall_time_hours": 48.5
        },
        "executed": "2024-04-10T00:00:00Z"
      }
    ],
    "attribution": {
      "creators": [
        {
          "name": "Computational Imaging Lab",
          "affiliation": "Example University"
        }
      ],
      "license": "CC0-1.0",
      "funding": ["NIH U01-CA231860"]
    }
  }
}
```

### 8.6 Lightsheet Microscopy with Lab Protocol

A cleared tissue volume where the wet-lab protocol and the imaging are both recorded:

```json
"extensions": {
  "provenance": {
    "version": "1.0",
    "sources": [
      {
        "type": "acquisition",
        "description": "Mouse brain, C57BL/6, 8 weeks, perfusion-fixed"
      }
    ],
    "processing": [
      {
        "name": "Tissue clearing",
        "method": {
          "name": "iDISCO+ whole-brain immunolabeling and clearing",
          "doi": "10.17504/protocols.io.bji2kkge",
          "url": "https://dx.doi.org/10.17504/protocols.io.bji2kkge",
          "version": "2"
        },
        "note": "Incubation extended to 5 days due to antibody lot variability."
      },
      {
        "name": "Lightsheet imaging",
        "software": {
          "name": "LaVision BioTec UltraMicroscope II"
        },
        "parameters": {
          "objective": "4x/0.28 NA",
          "sheet_thickness_um": 5,
          "step_size_um": 2.5,
          "channels": ["488nm", "640nm"]
        }
      },
      {
        "name": "Stitching and deconvolution",
        "software": {
          "name": "BigStitcher",
          "version": "0.8.1",
          "url": "https://imagej.net/plugins/bigstitcher/"
        }
      }
    ],
    "attribution": {
      "creators": [
        {
          "name": "Smith Lab",
          "affiliation": "Example University, Department of Neuroscience"
        }
      ],
      "license": "CC-BY-4.0",
      "funding": ["NIH U19-NS123456"]
    }
  }
}
```

The tissue clearing step has a `method` (the protocols.io protocol) but no `software` — it is a wet-lab procedure, not a computation. The lightsheet imaging step has `software` (the instrument) but no `method`. The stitching step has only `software`. Each step carries what is relevant to it.

### 8.7 Minimal

Provenance with only the source recorded:

```json
"extensions": {
  "provenance": {
    "version": "1.0",
    "sources": [
      {
        "type": "file",
        "format": "NRRD",
        "path": "original.nrrd"
      }
    ]
  }
}
```

### 8.8 Attribution Only

Data shared with licensing and citation but no processing history:

```json
"extensions": {
  "provenance": {
    "version": "1.0",
    "attribution": {
      "license": "CC-BY-4.0",
      "citation": "Example Consortium (2024). Benchmark dataset v2.",
      "doi": "10.5281/zenodo.12345678"
    }
  }
}
```

---

## 9. NRRD Key/Value Encoding

When data provenance is stored in a NRRD file (rather than a Zarr store), the same fields are encoded as key/value pairs with a `prov_` prefix. This enables lossless NRRD → Zarr conversion chains.

The structured JSON does not map neatly to NRRD's flat key/value model, so the encoding uses a JSON blob approach:

```
prov_sources:=[{"type":"file","format":"DICOM","path":"raw/series-84/"}]
prov_processing:=[{"name":"DICOM to Zarr","software":{"name":"dcm2zarr","version":"0.3.1"}}]
prov_attribution:={"license":"CC-BY-4.0","doi":"10.1016/j.neuroimage.2024.120345"}
```

Each top-level field (`sources`, `processing`, `attribution`) is stored as a single JSON-encoded value. The `:=` separator indicates a typed value (per the NRRD specification). A converter parses the JSON strings back into structured objects for the Zarr extension.

For simpler cases, individual fields may be broken out:

```
prov_source_format:=DICOM
prov_source_path:=raw/series-84/
prov_license:=CC-BY-4.0
prov_doi:=10.1016/j.neuroimage.2024.120345
```

These flat keys are convenience aliases. When both forms are present, the JSON blob takes precedence. A converter must handle both forms.

---

## 10. Design Notes

**Why a separate extension rather than inline fields.** Provenance metadata is verbose, variable, and orthogonal to what the array *is*. Putting processing history and attribution into the top-level `"duckn"` object would clutter the spatial-semantic core with pipeline details that most readers don't need. An extension keeps it opt-in: readers that don't understand `provenance` ignore it; the array remains fully usable.

**Why `sources` is separate from `processing`.** Sources describe *what went in*; processing describes *what happened*. These are different questions with different structures. A source might have a DOI and a format; a processing step might have parameters and an environment. Merging them into a single "history" list would force a polymorphic schema that is harder to validate and query.

**Why `processing` is a flat array, not a DAG.** Most real-world data has a linear processing history: acquire → convert → preprocess → register → segment. A linear array is simple to write, simple to read, and sufficient for the vast majority of cases. For complex branching workflows (multi-input registration, ensemble model averaging), the `inputs` field provides minimal DAG semantics. For full DAG provenance, link to an external workflow execution record — that is not a problem this extension should try to solve.

**Why `parameters` is free-form JSON.** There is no universal parameter vocabulary across imaging software. ANTs parameters, FreeSurfer parameters, and TotalSegmentator parameters have nothing in common. Imposing a schema would either be too restrictive (cannot represent real parameters) or too permissive (a generic key-value store — which is what free-form JSON already is). The `parameters` field records what was passed to the tool; the `software.name` and `software.version` fields tell you how to interpret it.

**Why `method` is separate from `software`.** A processing step has two independent aspects: the procedure that was followed and the tool that executed it. A tissue clearing protocol (the method) is not the same thing as the microscope that imaged the result (the software/instrument). A registration pipeline defined in a CWL workflow (the method) is not the same thing as the CWL runner that executed it (the software). Keeping them separate means each can be cited, versioned, and linked independently. The `method` field is not specific to protocols.io — it accommodates any citable method specification — but protocols.io is the most common case in biomedical research, and its DOI-based versioned protocols fit the field structure naturally.

**Why SPDX for licenses.** SPDX license identifiers are a widely adopted, machine-readable standard. Using them enables automated license compliance checking without parsing natural-language license text. The `license_url` field handles cases where SPDX does not apply.

**Why `created` and `executed` rather than a generic `timestamp`.** A timestamp on a source means something different from a timestamp on a processing step. "When was the source created" and "when was this step run" are different questions. Named fields eliminate the ambiguity. If a source was acquired at one time and modified at another, `created` records the original acquisition; additional context goes in `note`.

**Why `note` rather than `notes`.** A single free-form string per entry, not an array. If you need to say two things about a source, you have `description` (what it is) and `note` (anything else). If you genuinely need multiple annotations on a single entry, concatenate them — this is a human-readable field, not a structured log.

**Why no checksum field.** An earlier draft included a `checksum` on sources for integrity verification. In practice, sources are often directories (DICOM series), database queries, or multi-file datasets that have no single hash. For single-file sources where a hash matters, record it in `note` or `identifier`. A dedicated field suggested a level of rigor that most real-world provenance does not sustain.

**Why `null` is not used.** The DICOM extension uses `null` to distinguish "field was redacted" from "field was never present." This extension has no redaction use case — there is no scenario where you know a processing step existed but deliberately scrubbed its name. Absent means unknown; present means known.

**Relationship to W3C PROV.** The W3C Provenance Data Model (PROV-DM) defines a rich ontology of entities, activities, and agents with formal derivation relationships. This extension is deliberately simpler: it captures the metadata that a data consumer typically needs without requiring familiarity with PROV semantics. A PROV-aware system could generate this extension's fields from a PROV graph, or vice versa. The extension does not prevent storing a full PROV-O representation in a separate Zarr attribute for workflows that need it.

**Relationship to RO-Crate and DCAT.** Research Object Crate (RO-Crate) and the Data Catalog Vocabulary (DCAT) are standards for packaging research data with metadata. They operate at a higher level — describing datasets, workflows, and their relationships. This extension operates at the array level — describing a single Zarr array's history. An RO-Crate could reference a Zarr store whose arrays carry `provenance` extensions, providing both package-level and array-level provenance.