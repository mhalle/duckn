# FITS Provenance Extension for duckn

**Extension name:** `fits`
**Version:** 1.0
**Status:** Draft

---

## 1. Purpose

This document defines the `fits` extension for the duckn convention. It preserves FITS header metadata — World Coordinate System (WCS) parameters, observation context, and instrument provenance — as structured JSON within a Zarr store, enabling lossless conversion from FITS to duckn.

The extension addresses two complementary needs:

- **WCS provenance.** The FITS WCS defines a pixel-to-world coordinate transformation involving projections, reference systems, and spectral frames that go well beyond the duckn convention's linear spatial embedding. The convention captures the linear part (directions, origin, units); this extension preserves the nonlinear and frame-specific parts.

- **Observation metadata.** FITS headers carry telescope, instrument, observer, object, date, and other contextual fields that have no counterpart in the duckn convention or in Zarr itself. These are essential for scientific reproducibility and data discovery.

### What this extension is not

This is not a FITS reader. It does not attempt to represent arbitrary FITS extensions (binary tables, image sequences, random groups) or reproduce the full FITS header verbatim. It preserves the metadata a scientist or pipeline needs to understand the data's provenance and coordinate system — without requiring a FITS library to access it.

---

## 2. Relationship to duckn Convention Fields

The FITS WCS defines a multi-step pixel-to-world transformation:

1. **Pixel → intermediate pixel** (reference pixel offset): subtract `CRPIXj`
2. **Intermediate pixel → intermediate world** (linear transform): apply `CDi_j` (or `PCi_j` × `CDELTi`)
3. **Intermediate world → world** (nonlinear): apply the projection or spectral algorithm encoded in `CTYPEi`

Steps 1–2 decompose into the duckn convention's linear spatial embedding. Step 3 is where the extension is needed.

The following FITS constructs are already captured by Zarr or the convention:

| FITS construct | Captured by | Notes |
|---|---|---|
| `NAXISn` | Zarr `shape` | Dimension sizes |
| `BITPIX` | Zarr `data_type` | Element type |
| `CDi_j` or `PCi_j` × `CDELTi` | `axes[i].space_direction` | Linear transform (columns of the CD matrix become space directions) |
| `CRPIXj` + `CRVALi` | `space_origin` | See §3 |
| `CUNITi` (spatial) | `axes[i].unit` | Axis units |
| `BSCALE`, `BZERO` | `value_transforms` `linear` | Value rescaling |
| `BUNIT` | `sample_units` | Physical units of pixel values |

These fields *may* appear in `tags` for provenance but the convention fields are authoritative for processing.

### 2.1 Reference Point Decomposition

FITS uses a reference pixel (`CRPIXj`, 1-indexed) and a reference value (`CRVALi`) to anchor the coordinate system. The duckn convention uses `space_origin` — the world-space position of the first sample (index 0). For a linear WCS the conversion is:

```
space_origin[k] = CRVALi - Σ_j CDi_j × (CRPIXj - 1)
```

summed over pixel axes `j` that contribute to world axis `i` (typically the on-diagonal terms when the CD matrix is nearly diagonal). The subtraction of 1 accounts for FITS's 1-based indexing. This conversion is exact for linear axes. For nonlinear axes (celestial projections, spectral algorithms), the convention fields capture the linearization at the reference point; the extension preserves the full nonlinear transform.

---

## 3. WCS Decomposition

For **linear axes** (FITS `CTYPEi` is blank or `LINEAR`, or the axis has no projection algorithm), the CD matrix and reference point decompose completely into convention fields. No extension data is needed.

For **celestial axes** (FITS `CTYPEi` encodes a projection, e.g., `RA---TAN`, `GLON-AIT`), the linear part of the WCS (the CD matrix evaluated at the reference point) maps to `space_direction`, and the reference point maps to `space_origin`. The extension preserves:

- The projection type (the algorithm code from `CTYPEi`, e.g., `TAN`, `SIN`, `AIT`)
- The celestial coordinate system (`RADESYS`, `EQUINOX`)
- Projection parameters (`PVi_m`, `LONPOLE`, `LATPOLE`)
- The reference pixel (`CRPIXj`) and reference value (`CRVALi`) for exact round-tripping

For **spectral axes** (FITS `CTYPEi` encodes a spectral algorithm, e.g., `FREQ`, `VOPT-F2W`), the extension preserves:

- The spectral type and algorithm from `CTYPEi`
- The spectral reference frame (`SPECSYS`, `SSYSOBS`, `SSYSSRC`)
- Rest frequency or wavelength (`RESTFRQ`, `RESTWAV`)
- Source velocity (`ZSOURCE`, `VELOSYS`)

---

## 4. Extension Structure

The `fits` extension is declared at the top level of the `"duckn"` object's `"extensions"`.

```json
"extensions": {
  "fits": {
    "version": "1.0",
    "url": "https://fits.gsfc.nasa.gov/fits_standard.html",
    "wcs": {
      ...
    },
    "tags": {
      ...
    }
  }
}
```

### 4.1 Top-Level Extension Fields

#### `version`

Required. The version of this extension specification.

```json
"version": "1.0"
```

#### `schema`

A URL pointing to a schema or specification document for the extension. Optional.

```json
"schema": "https://example.org/fits-zarr/v1.0/schema.json"
```

#### `url`

A URL pointing to the FITS standard or WCS papers. Optional. Provides provenance for the source format.

```json
"url": "https://fits.gsfc.nasa.gov/fits_standard.html"
```

#### `wcs`

An object containing the WCS parameters that are not captured by the convention's linear spatial embedding. See §5.

#### `tags`

An object containing non-WCS FITS header keywords. See §6. All FITS keywords live inside `tags`, keeping FITS's vocabulary cleanly separated from extension-level fields (`version`, `schema`, `url`, `wcs`). This prevents collisions between FITS keywords and extension metadata, and makes it unambiguous which keys come from the FITS standard.

### 4.2 Per-Axis Extension Fields

Per-axis metadata is carried in the `extensions.fits` object on individual axes within the `axes` array. This is where per-axis WCS parameters that describe what a specific axis represents are stored.

```json
{
  "kind": "space",
  "centering": "cell",
  "space_direction": [-0.001, 0],
  "unit": "deg",
  "extensions": {
    "fits": {
      "ctype": "RA---TAN",
      "crpix": 512.0,
      "crval": 184.5575
    }
  }
}
```

---

## 5. WCS Fields

The `wcs` object at the top level of the extension contains coordinate system parameters that apply to the array as a whole.

### 5.1 Celestial Coordinate System

#### `radesys`

The celestial reference system. Values follow FITS WCS Paper II:

| Value | Description |
|---|---|
| `"ICRS"` | International Celestial Reference System |
| `"FK5"` | Mean place post-IAU 1976 |
| `"FK4"` | Mean place pre-IAU 1976 |
| `"FK4-NO-E"` | FK4 without E-terms of aberration |
| `"GAPPT"` | Geocentric apparent |

```json
"radesys": "ICRS"
```

Omit when no celestial axes are present.

#### `equinox`

The equinox of the celestial coordinate system, as a Julian or Besselian epoch (number). Required for FK4 and FK5; not applicable to ICRS.

```json
"equinox": 2000.0
```

#### `lonpole`

Native longitude of the celestial pole, in degrees. Default depends on the projection; include when the value differs from the projection-specific default.

#### `latpole`

Native latitude of the celestial pole, in degrees. Include when needed to resolve the pole ambiguity in oblique projections.

### 5.2 Spectral Reference Frame

#### `specsys`

The spectral reference frame in which `CRVALi` for the spectral axis is expressed.

| Value | Description |
|---|---|
| `"TOPOCENT"` | Topocentric |
| `"GEOCENTR"` | Geocentric |
| `"BARYCENT"` | Barycentric |
| `"HELIOCEN"` | Heliocentric |
| `"LSRK"` | Local Standard of Rest (kinematic) |
| `"LSRD"` | Local Standard of Rest (dynamic) |
| `"GALACTOC"` | Galactocentric |
| `"LOCALGRP"` | Local group |
| `"CMBDIPOL"` | Cosmic microwave background dipole |
| `"SOURCE"` | Source rest frame |

```json
"specsys": "BARYCENT"
```

#### `ssysobs`

The spectral reference frame in which the observation was made. Omit if identical to `specsys`.

#### `ssyssrc`

The spectral reference frame of the source. Typically `"SOURCE"`.

#### `restfrq`

Rest frequency in Hz. Required for velocity-frequency conversions.

```json
"restfrq": 1.420405752e9
```

#### `restwav`

Rest wavelength in meters. Alternative to `restfrq`.

#### `zsource`

Redshift of the source in the rest frame identified by `ssyssrc`.

#### `velosys`

Radial velocity of the reference frame relative to the observer, in m/s.

### 5.3 Projection Parameters

#### `pv`

Projection parameter values. An object keyed by `"i_m"` where `i` is the axis number (matching the axis index in the `axes` array, 0-based) and `m` is the parameter number.

```json
"pv": {
  "0_1": 0.0,
  "0_2": 0.0,
  "1_1": 45.0
}
```

These correspond to FITS `PVi_m` keywords. The axis index is 0-based (not FITS 1-based) to match the `axes` array.

### 5.4 Alternate WCS Descriptions

FITS supports multiple WCS descriptions for the same image (indicated by a trailing letter suffix on keywords, e.g., `CTYPE1A`, `CRVAL1A`). When alternate descriptions are present, they are stored in an `alternates` object keyed by the suffix letter:

```json
"alternates": {
  "A": {
    "radesys": "FK5",
    "equinox": 2000.0,
    "axes": [
      { "ctype": "RA---TAN", "crpix": 512.0, "crval": 184.5575 },
      { "ctype": "DEC--TAN", "crpix": 512.0, "crval": -5.7890 }
    ]
  }
}
```

The primary WCS (no suffix) is carried in the main `wcs` object and per-axis extension fields. Alternate descriptions carry the same field types but are namespaced under their suffix letter. Omit when no alternate WCS descriptions exist.

---

## 6. Per-Axis WCS Fields

The per-axis extension object carries the FITS WCS parameters specific to each array dimension. These appear within `axes[i].extensions.fits`.

#### `ctype`

The full FITS `CTYPEi` string. Encodes both the coordinate type and the projection or spectral algorithm in the standard `XXXX-YYY` format.

```json
"ctype": "RA---TAN"
```

This is the primary field that tells a reader what the axis represents in FITS terms. For celestial axes, the first 4 characters identify the coordinate (RA, DEC, GLON, GLAT, ELON, ELAT) and the last 3 identify the projection (TAN, SIN, AIT, etc.). For spectral axes, the format encodes the physical type and algorithm (e.g., `FREQ`, `VOPT-F2W`, `WAVE-TAB`).

#### `crpix`

The reference pixel for this axis (FITS 1-based). Preserved for exact round-tripping.

```json
"crpix": 256.5
```

#### `crval`

The world coordinate at the reference pixel for this axis.

```json
"crval": 184.5575
```

For celestial axes, this is in degrees. For spectral axes, it is in the units given by `cunit` or the default units for the spectral type.

#### `cdelt`

The coordinate increment per pixel along this axis, if the FITS source used the `CDELTi` + `PCi_j` formalism. Omit when the source used the `CDi_j` matrix (the information is fully captured in `space_direction`).

```json
"cdelt": -0.001
```

#### `cunit`

The FITS unit string for this axis, preserved when it differs from the convention's `unit` field or uses FITS-specific conventions.

```json
"cunit": "deg"
```

---

## 7. Observation Tags

The `tags` object contains non-WCS FITS keywords. Each key is a standard FITS keyword; each value is JSON-native.

```json
"tags": {
  "TELESCOP": "VLA",
  "INSTRUME": "EVLA C-band receiver",
  "OBSERVER": "Doe, J.",
  "OBJECT": "M51",
  "DATE-OBS": "2024-03-15T08:30:00",
  "EXPTIME": 3600.0,
  "MJD-OBS": 60384.354167,
  "FILTER": "C-band"
}
```

### 7.1 Encoding Rules

FITS keyword values are encoded using JSON-native types:

| FITS type | JSON encoding | Examples |
|---|---|---|
| Character string | string | `"VLA"`, `"2024-03-15T08:30:00"` |
| Integer | number | `1024`, `2` |
| Floating point | number | `3600.0`, `1.420405752e9` |
| Logical | boolean | `true`, `false` |

FITS keywords are at most 8 characters, uppercase, and may contain letters, digits, hyphens, and underscores. Preserve them exactly as they appear in the FITS header.

FITS `COMMENT` and `HISTORY` keywords are multi-valued and are encoded as arrays of strings:

```json
"COMMENT": [
  "Calibrated with CASA 6.5.2",
  "Primary beam corrected"
],
"HISTORY": [
  "Created by pipeline v2.3",
  "Continuum subtracted 2024-03-16"
]
```

### 7.2 Null Values

A header value set to `null` means the keyword was present in the source FITS header but its value was deliberately removed (e.g., during anonymization or redaction). This is distinct from the keyword being absent, which means it was not present in the source.

### 7.3 Recommended Header Keywords

This is guidance, not a requirement. Include whatever is relevant to the use case.

**Observation context:**

| Keyword | Description |
|---|---|
| `TELESCOP` | Telescope name |
| `INSTRUME` | Instrument name |
| `OBSERVER` | Observer name |
| `OBJECT` | Target object name |
| `DATE-OBS` | Observation date/time (ISO format) |
| `MJD-OBS` | Modified Julian Date of observation |
| `EXPTIME` | Exposure time in seconds |
| `AIRMASS` | Airmass at observation |

**Instrument parameters:**

| Keyword | Description |
|---|---|
| `FILTER` | Filter name |
| `GRATING` | Grating name |
| `APERTURE` | Aperture setting |
| `DETECTOR` | Detector name |
| `GAIN` | Detector gain (e-/ADU) |
| `RDNOISE` | Read noise (e-) |

**Provenance:**

| Keyword | Description |
|---|---|
| `ORIGIN` | Organization that created the file |
| `AUTHOR` | Author of the data |
| `REFERENC` | Bibliographic reference |
| `DATAMAX` | Maximum data value |
| `DATAMIN` | Minimum data value |

---

## 8. Fields Deliberately Excluded

| FITS construct | Reason |
|---|---|
| `SIMPLE`, `EXTEND` | Format identification; no semantic content |
| `NAXIS`, `NAXISn` | Zarr `shape` |
| `BITPIX` | Zarr `data_type` |
| `BSCALE`, `BZERO` | Convention `value_transforms` |
| `BUNIT` | Convention `sample_units` |
| `END` | Format terminator |
| `BLANK` | Integer null representation; Zarr uses `fill_value` |
| Pixel data | Zarr array data |
| Group parameters (`PTYPEn`, etc.) | Random groups are out of scope |
| Table keywords (`TFORMn`, `TTYPEn`, etc.) | Binary tables are out of scope |

---

## 9. Consistency Rules

- Per-axis `ctype` strings for celestial axes must appear in matched pairs (e.g., `RA---TAN` and `DEC--TAN` with the same projection code). The projection type must be the same for both axes of a celestial pair.
- If `radesys` is `"FK4"` or `"FK5"`, `equinox` should be present.
- If `radesys` is `"ICRS"`, `equinox` should be omitted.
- Axis indices in `pv` keys are 0-based, matching the `axes` array.
- `crpix` values in per-axis fields are FITS 1-based (not 0-based), preserved for round-trip fidelity.
- When both `restfrq` and `restwav` are present, they must be consistent (related by `c = f × λ`).

---

## 10. Examples

### 10.1 Radio Continuum Image (2D Celestial)

A VLA continuum image with a tangent-plane projection in ICRS coordinates:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [1024, 1024],
  "data_type": "float32",
  "dimension_names": ["dec", "ra"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [256, 256] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": "NaN",
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space_dimension": 2,
      "space_origin": [185.0698, -6.3012],
      "sample_units": "Jy/beam",
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.001],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "DEC--TAN",
              "crpix": 512.0,
              "crval": -5.7890
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [-0.001, 0],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "RA---TAN",
              "crpix": 512.0,
              "crval": 184.5575
            }
          }
        }
      ],
      "extensions": {
        "fits": {
          "version": "1.0",
          "wcs": {
            "radesys": "ICRS"
          },
          "tags": {
            "TELESCOP": "VLA",
            "INSTRUME": "EVLA C-band receiver",
            "OBJECT": "M51",
            "DATE-OBS": "2024-03-15T08:30:00",
            "MJD-OBS": 60384.354167,
            "EXPTIME": 3600.0,
            "OBSERVER": "Doe, J."
          }
        }
      }
    }
  }
}
```

The `space_direction` vectors encode the CD matrix columns (negative RA direction reflects the usual convention that RA increases to the left). The convention's `space_origin` gives the world coordinate of pixel (0, 0). The extension's per-axis `crpix` and `crval` preserve the original FITS reference point for exact round-tripping. A reader that ignores the `fits` extension still has a valid linear spatial embedding.

### 10.2 Spectral Line Cube (2D Celestial + Frequency)

A 3D radio data cube with two celestial axes and a frequency axis:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [256, 512, 512],
  "data_type": "float32",
  "dimension_names": ["freq", "dec", "ra"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [32, 128, 128] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": "NaN",
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space_dimension": 3,
      "space_origin": [1.41940575e9, -6.3012, 185.0698],
      "sample_units": "Jy/beam",
      "axes": [
        {
          "kind": "domain",
          "centering": "cell",
          "space_direction": [97656.25, 0, 0],
          "unit": "Hz",
          "extensions": {
            "fits": {
              "ctype": "FREQ",
              "crpix": 128.0,
              "crval": 1.420405752e9,
              "cunit": "Hz"
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.0005, 0],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "DEC--SIN",
              "crpix": 256.0,
              "crval": -5.7890
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0, -0.0005],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "RA---SIN",
              "crpix": 256.0,
              "crval": 184.5575
            }
          }
        }
      ],
      "extensions": {
        "fits": {
          "version": "1.0",
          "wcs": {
            "radesys": "FK5",
            "equinox": 2000.0,
            "specsys": "LSRK",
            "restfrq": 1.420405752e9
          },
          "tags": {
            "TELESCOP": "ALMA",
            "OBJECT": "NGC 5194",
            "DATE-OBS": "2024-06-20T14:00:00"
          }
        }
      }
    }
  }
}
```

The frequency axis uses `kind: "domain"` (not `"space"`) because it is a non-spatial independent variable that is meaningful to resample. The `space_dimension` is 3 because the world coordinate system has three dimensions (RA, Dec, frequency). The `specsys` field records that frequencies are in the LSRK frame.

### 10.3 Galactic Coordinates with Aitoff Projection

A 2D all-sky survey in galactic coordinates using an Aitoff projection:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [1800, 3600],
  "data_type": "float32",
  "dimension_names": ["glat", "glon"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [256, 256] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": "NaN",
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space_dimension": 2,
      "space_origin": [90.0, -180.0],
      "sample_units": "MJy/sr",
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [-0.1, 0],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "GLAT-AIT",
              "crpix": 900.5,
              "crval": 0.0
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, 0.1],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "GLON-AIT",
              "crpix": 1800.5,
              "crval": 0.0
            }
          }
        }
      ],
      "extensions": {
        "fits": {
          "version": "1.0",
          "tags": {
            "TELESCOP": "IRAS",
            "OBJECT": "ALL-SKY",
            "FILTER": "100um"
          }
        }
      }
    }
  }
}
```

Note: for an Aitoff projection, the convention's `space_origin` and `space_direction` capture a linearization at the reference point. Away from the reference point, the true coordinate transform requires the projection algorithm from `ctype`. A reader that ignores the extension will compute approximate coordinates that are accurate near the reference point but diverge at large angular distances. This is the expected degradation behavior — the linear embedding remains useful for local operations.

### 10.4 Stokes Cube (2D Celestial + Stokes)

A polarization data cube with Stokes parameters:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [4, 1024, 1024],
  "data_type": "float32",
  "dimension_names": ["stokes", "dec", "ra"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [4, 256, 256] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": "NaN",
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space_dimension": 2,
      "space_origin": [-6.3012, 185.0698],
      "sample_units": "Jy/beam",
      "axes": [
        {
          "kind": "list",
          "extensions": {
            "fits": {
              "ctype": "STOKES",
              "crpix": 1.0,
              "crval": 1.0,
              "cdelt": 1.0
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0.001, 0],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "DEC--TAN",
              "crpix": 512.0,
              "crval": -5.7890
            }
          }
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, -0.001],
          "unit": "deg",
          "extensions": {
            "fits": {
              "ctype": "RA---TAN",
              "crpix": 512.0,
              "crval": 184.5575
            }
          }
        }
      ],
      "extensions": {
        "fits": {
          "version": "1.0",
          "wcs": {
            "radesys": "ICRS"
          },
          "tags": {
            "TELESCOP": "VLA",
            "OBJECT": "Cygnus A"
          }
        }
      }
    }
  }
}
```

The Stokes axis uses `kind: "list"` because its positions (I=1, Q=2, U=3, V=4) are discrete labels, not a continuous domain. The `fits` per-axis extension records the FITS Stokes encoding scheme. A reader that ignores the extension still knows this is a non-resamplable list axis with 4 elements.

### 10.5 Minimal

A FITS-converted image with nothing to preserve beyond the source format:

```json
"extensions": {
  "fits": {
    "version": "1.0"
  }
}
```

This says "this data came from a FITS file" and nothing more.

---

## 11. Design Notes

**Why separate `wcs` from `tags`.** WCS parameters have well-defined semantics and are consumed by coordinate transformation code. Observation keywords are free-form metadata consumed by humans and discovery services. Separating them makes it clear which fields are structural (needed for coordinate computation) and which are contextual (useful for provenance). This parallels the DICOM extension's separation of extension-level fields from the `tags` namespace.

**Why `space_direction` captures the CD matrix, not CDELTi × PCi_j.** FITS has three historical representations of the linear transform: `CDi_j`, `CDELTi` + `PCi_j`, and `CDELTi` + `CROTAi`. The convention's `space_direction` is equivalent to the columns of the CD matrix — it encodes both scale and rotation in a single vector. This is the most general form and subsumes all three FITS representations. The extension's `cdelt` field is preserved only for round-tripping when the source used the CDELT formalism.

**Why per-axis WCS fields rather than a monolithic WCS object.** The duckn convention bundles per-axis metadata on the axis it describes. Following this pattern, `ctype`, `crpix`, `crval`, and `cdelt` live on their respective axes. This keeps axis descriptions coherent and avoids the "parallel arrays" antipattern where the i-th element of several separate arrays must be read together.

**Why `crpix` and `crval` are preserved despite `space_origin`.** For linear axes, `space_origin` captures the same information as `crpix` + `crval` + the CD matrix. For nonlinear axes (celestial projections, spectral algorithms), `space_origin` captures only the linearization. The original reference point is needed to reconstruct the full nonlinear transform. Dropping it would make round-tripping lossy.

**Why axis indices in `pv` are 0-based.** The duckn convention uses 0-based axis indexing (matching Zarr and Python convention). FITS uses 1-based indexing. To avoid confusion at the convention boundary, all axis references within the extension use 0-based indexing. A converter must add 1 when writing back to FITS.

**Why the Stokes axis is `kind: "list"`.** FITS encodes Stokes parameters as integer values (I=1, Q=2, U=3, V=4) on a linear axis. In duckn terms, this is a discrete labeling — the positions are not meaningful to interpolate. The `"list"` kind captures this correctly: it's a non-resamplable axis where each position has a distinct identity. The FITS encoding scheme (which integer maps to which Stokes parameter) is preserved in the per-axis extension.

**Relationship to the convention's `space` field.** The current convention has a fixed vocabulary of named spaces oriented toward medical imaging (RAS, LPS, scanner-xyz). FITS celestial coordinates (ICRS equatorial, galactic, ecliptic, super-galactic) do not map to any of these. When using this extension, use `space_dimension` rather than `space` to define the dimensionality of the world coordinate system. The extension's `radesys` and per-axis `ctype` fields identify the specific coordinate system. A future version of the convention may introduce extension-prefixed space names (e.g., `"fits:galactic"`) as a way to name these coordinate systems at the convention level.

**Nonlinear accuracy and graceful degradation.** The convention's linear spatial embedding is exact for linear FITS axes and a first-order approximation for projected axes. For a tangent-plane projection with small fields of view (typical of single-CCD observations), the linearization error is negligible. For wide-field or all-sky projections (Aitoff, Mollweide, HEALPix), the error grows significantly away from the reference point. The extension preserves the full projection specification so that a FITS-aware reader can compute exact coordinates. A reader that only understands the convention gets the best linear approximation — which is the intended degradation behavior of the layer cake.