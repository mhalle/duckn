# NIfTI Provenance Extension for NRRD-Zarr

**Extension name:** `nifti`
**Version:** 1.0
**Status:** Draft

---

## 1. Purpose

This document defines the `nifti` extension for the NRRD-Zarr convention. It preserves NIfTI-1 and NIfTI-2 header fields that cannot be losslessly derived from NRRD-Zarr convention fields or Zarr array parameters, enabling round-trip conversion between NIfTI and NRRD-Zarr without metadata loss.

The extension carries only what would otherwise be lost. NIfTI's spatial information — the sform affine, its coordinate space code, voxel sizes, and spatial units — decomposes losslessly into the convention's `space`, `space_origin`, per-axis `space_direction`, and per-axis `unit` fields. NIfTI's data type and dimensions map to Zarr's `data_type` and `shape`. The value rescaling slope and intercept map to the convention's `value_transforms`. None of these appear here.

What remains is acquisition metadata (slice timing, phase/frequency encoding), statistical intent parameters, display calibration, the secondary qform affine when it differs from the sform, and a handful of descriptive strings.

---

## 2. Relationship to NRRD-Zarr Convention Fields

The following table shows which NIfTI header fields are already captured by Zarr or the NRRD-Zarr convention and are therefore excluded from this extension.

| NIfTI field | Captured by | Notes |
|---|---|---|
| `dim[0..7]` | Zarr `shape` | Dimension count and sizes |
| `datatype`, `bitpix` | Zarr `data_type` | Element type |
| `pixdim[1..3]` (spatial) | `axes[i].space_direction` magnitude | Voxel spacing |
| `pixdim[4]` (temporal) | Time axis `space_direction` or `unit` | Temporal spacing |
| `sform44` | `space_origin` + `axes[i].space_direction` | Primary affine decomposition (see §3) |
| `sform_code` | `space` | Coordinate space identity |
| `qform44` | `space_origin` + `axes[i].space_direction` | Reconstructed from convention fields; only `qform_code` preserved in extension |
| `scl_slope`, `scl_inter` | `value_transforms` `linear` | Value rescaling |
| `xyzt_units` (spatial bits) | `axes[i].unit` on spatial axes | e.g., `"mm"` |
| `xyzt_units` (temporal bits) | `axes[t].unit` on time axis | e.g., `"s"`, `"ms"` |
| `vox_offset` | — | File layout artifact; no semantic content |
| `sizeof_hdr`, `magic` | — | Format identification; no semantic content |
| `regular` (NIfTI-2) | — | Always `'r'`; no information |

Fields not in this table and not in §4 (e.g., `glmin`, `glmax`, `data_type` as a 10-char string, `db_name`) are Analyze 7.5 vestiges with no meaningful content in NIfTI files. They are not preserved.

---

## 3. Sform Decomposition

The NIfTI sform is a 4×4 affine matrix. The NRRD-Zarr convention decomposes it completely:

- **Translation column** (elements `[0:3, 3]`) → `space_origin`
- **Rotation-scaling columns** (columns 0–2 of the 3×3 submatrix) → `axes[i].space_direction` for each spatial axis
- **Bottom row** is always `[0, 0, 0, 1]` — carries no information

The `sform_code` maps to the convention's `space` field:

| `sform_code` | `space` value |
|---|---|
| 1 (NIFTI_XFORM_SCANNER_ANAT) | `"scanner-xyz"` |
| 2 (NIFTI_XFORM_ALIGNED_ANAT) | `"right-anterior-superior"` |
| 3 (NIFTI_XFORM_TALAIRACH) | `"right-anterior-superior"` |
| 4 (NIFTI_XFORM_MNI_152) | `"right-anterior-superior"` |

All NIfTI sform coordinate systems are RAS-oriented (the NIfTI-1 spec defines the sform output as left-right / posterior-anterior / inferior-superior with the positive direction being right, anterior, superior). Codes 2–4 differ in what the space *means* (alignment target, atlas), not in the axis directions. The convention's `"right-anterior-superior"` captures the geometric orientation; this extension's `sform_code` field (§4.2) preserves the specific interpretation when round-trip fidelity is needed.

This decomposition is exact. No information is lost, and the sform can be reconstructed by assembling the columns back into a matrix.

---

## 4. Extension Fields

The `nifti` extension is declared at the top level of the `"nrrd"` object's `"extensions"`.

```json
"extensions": {
  "nifti": {
    "version": "1.0",
    "url": "https://nifti.nimh.nih.gov",
    "nifti_version": 1,
    "tags": {
      ...
    }
  }
}
```

Extension-level fields (`version`, `url`, `nifti_version`) describe the extension itself and the source format. NIfTI header fields are stored in the `tags` object, keeping NIfTI's vocabulary cleanly separated from extension metadata. This follows the same pattern as the DICOM extension's `tags` namespace.

### 4.1 Extension-Level Fields

#### `version`

Required. The version of this extension specification.

```json
"version": "1.0"
```

#### `url`

A URL pointing to the NIfTI specification or documentation. Optional. Provides provenance for the source format.

```json
"url": "https://nifti.nimh.nih.gov"
```

#### `nifti_version`

The NIfTI format version of the source file.

| Value | Meaning |
|---|---|
| `1` | NIfTI-1 (348-byte header) |
| `2` | NIfTI-2 (540-byte header) |

```json
"nifti_version": 1
```

### 4.2 `tags`

The `tags` object contains NIfTI header fields. All fields within `tags` are optional; their presence follows the "absent means unknown" principle.

#### `sform_code`

The original `sform_code` integer from the NIfTI header. Preserves the distinction between scanner-based, aligned, Talairach, and MNI coordinates that the convention's `space` field collapses.

```json
"sform_code": 2
```

Omit when the sform was not present (`sform_code` = 0) or when the code is already fully captured by the `space` field (i.e., `sform_code` = 1 mapping to `"scanner-xyz"`). In practice, include it whenever the source file had `sform_code` ≥ 2 to distinguish aligned, Talairach, and MNI.

#### `qform_code`

The original `qform_code` integer from the NIfTI header. Preserved so that a converter writing back to NIfTI can set both affine codes correctly.

```json
"qform_code": 1
```

On write-back, the qform *matrix* is reconstructed from the convention's `space_origin` and `axes[i].space_direction` fields — the same source as the sform. Only the code may differ: for example, `sform_code=4` (MNI) with `qform_code=1` (scanner) is a common pattern from dcm2niix.

Omit when the qform was not present (`qform_code` = 0) or when it equals the `sform_code` (the common case). When omitted, a converter should set the qform code equal to the sform code.

### 4.3 `legacy`

The `legacy` object stores original NIfTI data for provenance. Its contents are informational — the convention fields are always the authoritative source for spatial information.

#### `legacy.tags`

Contains the original NIfTI affine matrices as 4×4 arrays (row-major).

```json
"legacy": {
  "tags": {
    "sform": [
      [2.0, 0.0, 0.0, -100.0],
      [0.0, 2.0, 0.0, -100.0],
      [0.0, 0.0, 3.0, -50.0],
      [0.0, 0.0, 0.0, 1.0]
    ],
    "qform": [
      [2.0, 0.0, 0.0, -99.0],
      [0.0, 2.0, 0.0, -99.0],
      [0.0, 0.0, 3.0, -49.0],
      [0.0, 0.0, 0.0, 1.0]
    ]
  }
}
```

| Field | Type | Description |
|---|---|---|
| `sform` | 4×4 array of numbers | Original sform affine. Present when `sform_code` > 0. |
| `qform` | 4×4 array of numbers | Original qform affine. Present when `qform_code` > 0. |

A converter writing back to NIfTI should reconstruct both affines from convention fields, not from these legacy copies. When the qform and sform were identical (the common case from dcm2niix), both matrices will be equal. When they differed — for example, sform in MNI space and qform in scanner space — both originals are available for inspection.

#### `dim_info`

Identifies which array dimensions correspond to MRI frequency encoding, phase encoding, and slice acquisition directions.

```json
"dim_info": {
  "freq_dim": 1,
  "phase_dim": 2,
  "slice_dim": 3
}
```

| Field | Type | Description |
|---|---|---|
| `freq_dim` | integer (0–3) | Dimension index for frequency encoding. 0 = unknown. |
| `phase_dim` | integer (0–3) | Dimension index for phase encoding. 0 = unknown. |
| `slice_dim` | integer (0–3) | Dimension index for slice acquisition. 0 = unknown. |

In NIfTI, these are packed into a single byte. Here they are unpacked for readability. The dimension indices are 1-based, following NIfTI convention (1 = first spatial dimension, 2 = second, 3 = third). A value of 0 means the assignment is unknown.

Omit the entire field if all three are unknown (all zero). Omit individual sub-fields that are zero — e.g., if only `slice_dim` is known:

```json
"dim_info": {
  "slice_dim": 3
}
```

#### `intent`

The NIfTI intent, preserving the raw code and its associated parameters.

```json
"intent": {
  "code": 3,
  "name": "t-statistic",
  "p1": 42.0,
  "p2": 0.0,
  "p3": 0.0
}
```

| Field | Type | Description |
|---|---|---|
| `code` | integer | Raw `intent_code` value |
| `name` | string | The `intent_name` string (up to 15 characters in NIfTI-1). Omit if empty. |
| `p1` | number | `intent_p1`. Omit if zero and unused by the intent code. |
| `p2` | number | `intent_p2`. Omit if zero and unused by the intent code. |
| `p3` | number | `intent_p3`. Omit if zero and unused by the intent code. |

The convention's top-level `intent` field captures the broad purpose (e.g., `"statistical-map"`, `"displacement-field"`). This extension field preserves the specific NIfTI intent code and its parameters, which carry additional information — for example, degrees of freedom for a t-statistic, or the specific statistical test type.

Omit the entire field when `intent_code` is 0 (NIFTI_INTENT_NONE).

**Mapping between NIfTI intent codes and NRRD-Zarr `intent`:**

| NIfTI intent code | NIfTI name | NRRD-Zarr `intent` | Parameters |
|---|---|---|---|
| 2 (CORREL) | Correlation | `"statistical-map"` | `p1` = DOF |
| 3 (TTEST) | T-statistic | `"statistical-map"` | `p1` = DOF |
| 4 (FTEST) | F-statistic | `"statistical-map"` | `p1` = numerator DOF, `p2` = denominator DOF |
| 5 (ZSCORE) | Z-score | `"statistical-map"` | — |
| 1001 (ESTIMATE) | Parameter estimate | `"statistical-map"` | — |
| 1002 (LABEL) | Label index | `"label-map"` | — |
| 1005 (SYMMATRIX) | Symmetric matrix | `"diffusion-tensor"` (when appropriate) | — |
| 1006 (DISPVECT) | Displacement vector | `"displacement-field"` | — |
| 1007 (VECTOR) | Generic vector | `"velocity-field"` (when appropriate) | — |
| 2003 (POINTSET) | Point set | — | — |
| 0 (NONE) | None | — | — |
| All others | — | — | Raw code preserved here |

This mapping is a guideline for converters. The NIfTI extension always preserves the exact code; the convention's `intent` provides a coarser human-readable classification.

#### `slice_timing`

Acquisition timing metadata for the slice dimension.

```json
"slice_timing": {
  "code": "alternating-increasing",
  "start": 0,
  "end": 59,
  "duration": 0.05
}
```

| Field | Type | Description |
|---|---|---|
| `code` | string | Slice acquisition order (see table below) |
| `start` | integer | `slice_start` — index of the first acquired slice |
| `end` | integer | `slice_end` — index of the last acquired slice |
| `duration` | number | `slice_duration` — time in seconds to acquire one slice |

**Slice code values:**

| NIfTI `slice_code` | Extension value |
|---|---|
| 0 | (omit `code` — unknown) |
| 1 | `"sequential-increasing"` |
| 2 | `"sequential-decreasing"` |
| 3 | `"alternating-increasing"` |
| 4 | `"alternating-decreasing"` |
| 5 | `"alternating-increasing-2"` (starts at second slice) |
| 6 | `"alternating-decreasing-2"` (starts at second-to-last slice) |

Omit the entire field when all sub-fields are at their defaults (code unknown, start = 0, end = 0, duration = 0). Omit individual sub-fields that are at default values.

The `slice_dim` in `dim_info` (§4.5) identifies which array dimension these timing parameters apply to.

#### `toffset`

Temporal offset in seconds of the first volume relative to some reference time point.

```json
"toffset": 12.5
```

Omit when zero.

#### `cal`

Display calibration range — the suggested minimum and maximum values for display windowing.

```json
"cal": {
  "min": 0.0,
  "max": 2000.0
}
```

These are display hints, not data extremes. `cal_min` = `cal_max` = 0 means "no calibration specified" — omit the field in that case. Omit individual sub-fields that are zero when the other is non-zero.

The NRRD-Zarr convention deliberately excludes display hints. This field exists solely for NIfTI round-trip fidelity.

#### `descrip`

The NIfTI description string (up to 79 characters in NIfTI-1, 79 in NIfTI-2).

```json
"descrip": "FSL5.0"
```

Omit when empty.

#### `aux_file`

The NIfTI auxiliary filename string (up to 23 characters in NIfTI-1, 23 in NIfTI-2).

```json
"aux_file": "subj01_anatomy"
```

Omit when empty.

---

## 5. Fields Deliberately Excluded

| NIfTI field | Reason |
|---|---|
| `sizeof_hdr` | Format structure, not data semantics |
| `magic` | Format identification |
| `vox_offset` | File layout artifact |
| `regular` (NIfTI-2) | Always `'r'` |
| `dim[0..7]` | Zarr `shape` |
| `datatype`, `bitpix` | Zarr `data_type` |
| `pixdim[1..7]` | Spatial: recoverable from `space_direction` magnitudes. Temporal: convention axis metadata. |
| `sform44` | Decomposed into convention fields (§3) |
| `qform44` (when = sform) | Redundant |
| `scl_slope`, `scl_inter` | Convention `value_transforms` |
| `xyzt_units` | Convention per-axis `unit` |
| `glmin`, `glmax` | Analyze 7.5 legacy; unused in NIfTI |
| `data_type` (10-char string) | Analyze 7.5 legacy; unused in NIfTI |
| `db_name` | Analyze 7.5 legacy; unused in NIfTI |
| `extents` | Analyze 7.5 legacy; unused in NIfTI |
| `session_error` | Analyze 7.5 legacy; unused in NIfTI |

---

## 6. Consistency Rules

- When `qform_code` is present, it must be > 0.
- When `dim_info` is present, dimension indices must be in the range 0–3 and, if non-zero, must refer to valid spatial dimensions in the array.
- When `slice_timing` is present, `start` and `end` must be valid indices along the slice dimension identified by `dim_info.slice_dim`. If `dim_info.slice_dim` is unknown (0 or absent), `slice_timing` is still permitted but its axis association is ambiguous.
- `intent.code` and the convention-level `intent` field should be consistent when both are present. The extension preserves the NIfTI code exactly; the convention provides a coarser label.

---

## 7. Examples

### 7.1 Structural MRI (Simple Case)

A T1-weighted volume converted from NIfTI. The sform is the only affine, and there is minimal extra metadata:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [256, 256, 176],
  "data_type": "int16",
  "dimension_names": ["i", "j", "k"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 44] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "nrrd": {
      "version": "1.0",
      "space": "right-anterior-superior",
      "space_origin": [-127.5, -127.5, -87.5],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [1, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 1, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 1],
          "unit": "mm"
        }
      ],
      "extensions": {
        "nifti": {
          "version": "1.0",
          "nifti_version": 1,
          "tags": {
            "descrip": "FreeSurfer recon-all"
          }
        }
      }
    }
  }
}
```

Minimal extension — only the description string needed preservation.

### 7.2 Statistical Map with Intent Parameters

An fMRI t-statistic map with degrees of freedom:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [64, 64, 36],
  "data_type": "float32",
  "dimension_names": ["i", "j", "k"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 36] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0.0,
  "attributes": {
    "nrrd": {
      "version": "1.0",
      "space": "right-anterior-superior",
      "space_origin": [-94.5, -130.5, -72.0],
      "intent": "statistical-map",
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [3, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 3, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 3],
          "unit": "mm"
        }
      ],
      "extensions": {
        "nifti": {
          "version": "1.0",
          "nifti_version": 1,
          "tags": {
            "sform_code": 4,
            "intent": {
              "code": 3,
              "name": "ttest",
              "p1": 42.0
            },
            "cal": {
              "min": -8.0,
              "max": 8.0
            },
            "descrip": "SPM{T_[42.0]} - contrast 1"
          }
        }
      }
    }
  }
}
```

The convention's `intent` says `"statistical-map"`. The extension's `intent` says specifically "t-test with 42 degrees of freedom." The `sform_code` of 4 (MNI space) is preserved because the convention maps it to the same `"right-anterior-superior"` as codes 2 and 3.

### 7.3 fMRI Time Series with Full Acquisition Metadata

A 4D fMRI dataset with slice timing information and dual affines:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [64, 64, 36, 200],
  "data_type": "int16",
  "dimension_names": ["i", "j", "k", "t"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 36, 1] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "nrrd": {
      "version": "1.0",
      "space": "scanner-xyz",
      "space_origin": [-94.5, -130.5, -72.0],
      "value_transforms": [
        { "name": "linear", "parameters": { "slope": 2.5, "intercept": 0.0 } }
      ],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [3, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 3, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 3],
          "unit": "mm"
        },
        {
          "kind": "time",
          "unit": "s"
        }
      ],
      "extensions": {
        "nifti": {
          "version": "1.0",
          "nifti_version": 1,
          "tags": {
            "dim_info": {
              "freq_dim": 1,
              "phase_dim": 2,
              "slice_dim": 3
            },
            "slice_timing": {
              "code": "alternating-increasing",
              "start": 0,
              "end": 35,
              "duration": 0.0556
            },
            "toffset": 0.0,
            "sform_code": 1,
            "qform_code": 1,
            "cal": {
              "min": 0.0,
              "max": 32000.0
            },
            "descrip": "EPI BOLD 3mm iso TR=2s"
          },
          "legacy": {
            "tags": {
              "sform": [
                [3, 0, 0, -94.5],
                [0, 3, 0, -130.5],
                [0, 0, 3, -72.0],
                [0, 0, 0, 1]
              ],
              "qform": [
                [3, 0, 0, -94.5],
                [0, 3, 0, -130.5],
                [0, 0, 3, -72.0],
                [0, 0, 0, 1]
              ]
            }
          }
        }
      }
    }
  }
}
```

This example shows the full scope of the extension: dual affine codes (sform as primary spatial embedding, qform code preserved for round-tripping), MRI acquisition parameters (frequency/phase/slice encoding, slice timing), value rescaling, temporal metadata, display calibration, and legacy matrices for provenance. On write-back, both affines are reconstructed from the convention fields, not from the legacy copies.

### 7.4 Diffusion Tensor from NIfTI

A symmetric tensor volume with NIfTI intent code 1005:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [128, 128, 60, 6],
  "data_type": "float32",
  "dimension_names": ["i", "j", "k", "component"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [32, 32, 15, 6] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0.0,
  "attributes": {
    "nrrd": {
      "version": "1.0",
      "space": "right-anterior-superior",
      "space_origin": [-127.0, -127.0, 0.0],
      "measurement_frame": [[1,0,0], [0,1,0], [0,0,1]],
      "sample_units": "mm²/s",
      "intent": "diffusion-tensor",
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [2, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 2, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, 2],
          "unit": "mm"
        },
        {
          "kind": "3D-symmetric-matrix"
        }
      ],
      "extensions": {
        "nifti": {
          "version": "1.0",
          "nifti_version": 1,
          "tags": {
            "intent": {
              "code": 1005,
              "name": "symmatrix"
            }
          }
        }
      }
    }
  }
}
```

The spatial embedding and tensor semantics are fully captured by the convention. The extension preserves only the NIfTI intent code for exact round-tripping.

### 7.5 Minimal

A NIfTI-converted volume with nothing to preserve beyond the source format version:

```json
"extensions": {
  "nifti": {
    "version": "1.0",
    "nifti_version": 1
  }
}
```

This is a valid use of the extension. It says "this data came from a NIfTI-1 file" and nothing more.

---

## 8. NRRD Key/Value Encoding

When NIfTI provenance is stored in a NRRD file (rather than a Zarr store), the same fields are encoded as key/value pairs with a `nifti_` prefix. This enables lossless NIfTI → NRRD → Zarr conversion chains.

```
nifti_version:=1
nifti_sform_code:=4
nifti_intent_code:=3
nifti_intent_name:=ttest
nifti_intent_p1:=42.0
nifti_dim_info_freq:=1
nifti_dim_info_phase:=2
nifti_dim_info_slice:=3
nifti_slice_code:=alternating-increasing
nifti_slice_start:=0
nifti_slice_end:=35
nifti_slice_duration:=0.0556
nifti_toffset:=0.0
nifti_cal_min:=-8.0
nifti_cal_max:=8.0
nifti_descrip:=SPM{T_[42.0]} - contrast 1
nifti_aux_file:=
nifti_qform_code:=1
```

The mapping between NRRD key/value pairs and JSON extension fields is mechanical:

| NRRD key | JSON path |
|---|---|
| `nifti_version` | `nifti_version` |
| `nifti_sform_code` | `tags.sform_code` |
| `nifti_intent_code` | `tags.intent.code` |
| `nifti_intent_name` | `tags.intent.name` |
| `nifti_intent_p1` | `tags.intent.p1` |
| `nifti_intent_p2` | `tags.intent.p2` |
| `nifti_intent_p3` | `tags.intent.p3` |
| `nifti_dim_info_freq` | `tags.dim_info.freq_dim` |
| `nifti_dim_info_phase` | `tags.dim_info.phase_dim` |
| `nifti_dim_info_slice` | `tags.dim_info.slice_dim` |
| `nifti_slice_code` | `tags.slice_timing.code` |
| `nifti_slice_start` | `tags.slice_timing.start` |
| `nifti_slice_end` | `tags.slice_timing.end` |
| `nifti_slice_duration` | `tags.slice_timing.duration` |
| `nifti_toffset` | `tags.toffset` |
| `nifti_cal_min` | `tags.cal.min` |
| `nifti_cal_max` | `tags.cal.max` |
| `nifti_descrip` | `tags.descrip` |
| `nifti_aux_file` | `tags.aux_file` |
| `nifti_qform_code` | `tags.qform_code` |

The "absent means unknown" principle applies: omit keys whose values are at their defaults (0, empty string, or not applicable). A converter reading `nifti_` keys from a NRRD header groups them into the structured JSON objects for the Zarr extension, and vice versa.

---

## 9. Design Notes

**Why `tags` is a separate namespace.** Without the `tags` object, NIfTI header field names like `intent`, `cal`, and `descrip` share the same JSON namespace as extension metadata like `version` and `nifti_version`. As the extension evolves, the collision risk grows. The `tags` object makes the boundary explicit: everything inside is a NIfTI header field, everything outside is about the extension. This follows the same pattern as the DICOM extension's `tags` namespace.

**Why preserve `sform_code` separately.** The NRRD-Zarr convention's `space` field captures the geometric orientation (RAS) but not the semantic distinction between "aligned to scanner," "aligned to atlas," and "in MNI coordinates." For neuroimaging workflows where the distinction between native space and standard space matters, the raw code is needed.

**Why the convention fields are authoritative and the original matrices are legacy.** The convention's `space_origin` and `space_direction` fields are the single source of truth for spatial information. On write-back, both the sform and qform are reconstructed from these fields. The original 4×4 matrices are stored as `legacy_sform` and `legacy_qform` for provenance — they let a human or tool inspect what the source NIfTI file contained, but they do not participate in the spatial mapping. This avoids the classic NIfTI problem of two conflicting affines: once the data enters the convention, there is one spatial mapping, period. The legacy matrices and the two code integers are enough to understand the original file's intent without perpetuating the ambiguity.

**Why `cal_min`/`cal_max` are included despite being display hints.** The NRRD-Zarr convention excludes display preferences by design. However, this extension's purpose is round-trip fidelity, not convention purity. Display calibration values are part of the NIfTI header and are used by viewers (FSLeyes, MRIcron, FreeSurfer) to set initial window/level. Dropping them changes the user experience on round-trip.

**Why `dim_info` uses 1-based indexing.** This matches NIfTI's own convention, where dimension indices in `dim_info` are 1-based (1 = first spatial dimension). A converter must account for the axis ordering difference between NIfTI and Zarr when mapping these indices.

**Why `glmin`/`glmax` and other Analyze fields are excluded.** NIfTI inherited several fields from Analyze 7.5 that have no defined semantics in the NIfTI spec and are zero-filled by virtually all writers. Preserving them adds complexity for no informational value. If a legacy Analyze 7.5 file needs exact preservation, that is outside this extension's scope.

**Relationship to NIfTI-2.** NIfTI-2 expanded header field sizes (64-bit dimensions and offsets) but did not add new semantic fields. This extension handles both versions — the `nifti_version` field records which header format was used, and all field definitions are compatible with both versions.

**Tensor component ordering.** NIfTI's SYMMATRIX intent stores upper-triangle components in row-major order: `Dxx Dxy Dxz Dyy Dyz Dzz`. NRRD's `3D-symmetric-matrix` kind uses the same ordering. No reordering is needed during conversion, but this should be verified when the measurement frames differ between source and target.