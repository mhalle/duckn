# DICOM Provenance Extension for duckn

**Extension name:** `dicom`
**Version:** 1.0
**Status:** Draft

---

## 1. Purpose

This document defines the `dicom` extension for the duckn convention. It preserves DICOM metadata from source imaging studies — acquisition parameters, patient context, series identification, equipment description, and other attributes — as structured JSON within a Zarr store.

The design follows two principles:

- **DICOM tags live in a `tags` namespace.** All DICOM data elements are stored in a `tags` object, keeping DICOM's vocabulary cleanly separated from extension-level fields (`version`, `anonymized`, etc.). This prevents collisions between DICOM keywords and extension metadata, and makes it unambiguous which keys come from the DICOM standard.

- **Use DICOM keywords and JSON-native types.** Tags are keyed by their PS3.6 keyword (PascalCase, e.g., `Modality`, `PatientID`, `PixelSpacing`) rather than hex group-element codes. Values use JSON-native types (numbers, strings, arrays) rather than DICOM's string-encoded value representations. The goal is usability for developers, not round-trip VR fidelity. If lossless binary DICOM round-tripping is required, use the standard DICOM JSON Model (PS3.18 Annex F) in a separate attribute.

### What this extension is not

This is a provenance and metadata carrier, not a DICOM encoding. It does not attempt to represent every DICOM IOD or encode the full DICOM information model. It preserves the header fields a researcher or pipeline needs to understand where the data came from, how it was acquired, and what parameters were used — without requiring a DICOM toolkit to read it.

---

## 2. Relationship to duckn Convention Fields

The following DICOM attributes are already captured by Zarr or the duckn convention and should generally not be duplicated in `tags`. When present in `tags`, the extension values are provenance (the DICOM-native values from the source); the convention fields are authoritative for processing.

| DICOM attribute | Keyword | Captured by |
|---|---|---|
| Rows, Columns | `Rows`, `Columns` | Zarr `shape` |
| Bits Allocated, Bits Stored, High Bit, Pixel Representation | various | Zarr `data_type` |
| Number of Frames | `NumberOfFrames` | Zarr `shape` |
| Image Position (Patient) | `ImagePositionPatient` | `space_origin` |
| Image Orientation (Patient) | `ImageOrientationPatient` | `axes[i].space_direction` |
| Pixel Spacing | `PixelSpacing` | `axes[i].space_direction` magnitude |
| Spacing Between Slices | `SpacingBetweenSlices` | `axes[i].space_direction` magnitude |
| Slice Thickness | `SliceThickness` | `axes[i].thickness` |
| Rescale Slope, Rescale Intercept | `RescaleSlope`, `RescaleIntercept` | `value_transforms` `linear` |
| Rescale Type | `RescaleType` | `sample_units` |
| Pixel Data | `PixelData` | Zarr array data |

These fields *may* appear in `tags` to record the DICOM-native values for provenance (e.g., the exact `PixelSpacing` string as DICOM encoded it, or `SliceThickness` before any reconstruction), but the convention fields take precedence for computation.

---

## 3. Extension Structure

The `dicom` extension is declared at the top level of the `"nrrd"` object's `"extensions"`.

```json
"extensions": {
  "dicom": {
    "version": "1.0",
    "tags": {
      ...
    }
  }
}
```

### 3.1 Top-Level Extension Fields

These are extension metadata — fields *about* the DICOM provenance, not DICOM data elements themselves.

#### `version`

Required. The version of this extension specification.

```json
"version": "1.0"
```

#### `anonymized`

Boolean. `true` if the source DICOM data was anonymized or de-identified before conversion. When `true`, tags that were redacted should be present in `tags` with a `null` value (see §4.3).

```json
"anonymized": true
```

Omit if anonymization status is unknown or not applicable.

#### `schema`

A URL pointing to a schema or specification document for the extension. Optional. Readers may use this for validation or surface it to users as documentation.

```json
"schema": "https://example.org/dicom-zarr/v1.0/schema.json"
```

#### `source_transfer_syntax`

The Transfer Syntax UID of the source DICOM file. Useful for understanding the original pixel data encoding (e.g., whether the source was JPEG 2000 compressed).

```json
"source_transfer_syntax": "1.2.840.10008.1.2.4.90"
```

Omit when unknown or not meaningful.

#### `standard_version`

The DICOM standard edition the source data conforms to (e.g., `"2024a"`). Useful when keyword definitions may have changed between editions.

```json
"standard_version": "2024a"
```

Omit when unknown.

### 3.2 `tags`

An object containing DICOM data elements. Each key is a DICOM keyword from PS3.6; each value is the tag's content encoded in JSON-native types.

```json
"tags": {
  "Modality": "CT",
  "Manufacturer": "GE MEDICAL SYSTEMS",
  "KVP": 120,
  "PixelSpacing": [0.703, 0.703],
  "PatientName": null
}
```

See §4 for encoding rules.

---

## 4. Tag Encoding Rules

### 4.1 Keys: DICOM Keywords

Tag keys are the standard DICOM keywords from PS3.6 (the Data Dictionary). These are PascalCase strings, e.g., `Modality`, `StudyInstanceUID`, `ImagePositionPatient`.

Keywords provide a stable, human-readable, and tool-friendly namespace. They are preferred over hex tag codes (`"00080060"`) for readability. The PS3.6 keyword is the canonical form; do not use the tag name (which may contain spaces) or ad hoc abbreviations.

For private data elements, use the hex tag code as the key (e.g., `"00091001"`), since private tags have no standard keywords.

### 4.2 Values: JSON-Native Encoding

DICOM values are encoded using JSON-native types. The mapping from DICOM Value Representations to JSON types follows these rules:

| DICOM VR | JSON encoding | Examples |
|---|---|---|
| CS (Code String) | string | `"CT"`, `"MR"` |
| SH, LO (Short/Long String) | string | `"GE MEDICAL SYSTEMS"` |
| LT, ST, UT (Text) | string | `"Chest CT without contrast"` |
| UI (UID) | string | `"1.2.840.113619.2.55.3..."` |
| DA (Date) | string | `"20240115"` (DICOM format: YYYYMMDD) |
| TM (Time) | string | `"143025.000"` (DICOM format: HHMMSS.FFFFFF) |
| DT (DateTime) | string | `"20240115143025.000"` |
| AS (Age String) | string | `"065Y"` |
| DS (Decimal String) | number | `120.0` (parsed to JSON number) |
| IS (Integer String) | number | `256` (parsed to JSON number) |
| US, SS, UL, SL (integers) | number | `512`, `-1024` |
| FL, FD (float/double) | number | `0.703125` |
| PN (Person Name) | string or null | `"Doe^John"` or `null` if redacted |
| AT (Attribute Tag) | string | `"00081030"` (uppercase hex) |
| SQ (Sequence) | array of objects | See §4.4 |
| OB, OW, OF, OD, OL, OV (binary) | excluded | See §4.5 |

**Key differences from PS3.18 Annex F (DICOM JSON Model):**

- Keys are **keywords**, not hex tag codes.
- There is no `vr` field on each value — the VR is implicit from the keyword (looked up in PS3.6).
- Numeric VRs (DS, IS, FL, FD, US, SS, UL, SL) are encoded as JSON numbers directly, not wrapped in `{"vr": "DS", "Value": [...]}`.
- Multi-valued attributes are JSON arrays. Single-valued attributes are bare values (not wrapped in an array).
- The `Value` / `BulkDataURI` / `InlineBinary` structure of PS3.18 is not used.

This encoding trades VR-level round-trip fidelity for readability and simplicity. A DS value of `"120.000 "` in DICOM becomes `120.0` in JSON — the numeric meaning is preserved, the original string representation is not.

### 4.3 Null Values and Redaction

A tag set to `null` means the value existed in the source DICOM data but was deliberately removed (typically by anonymization). This is distinct from the tag being absent, which means it was not present in the source or is unknown.

```json
"PatientName": null,
"PatientID": null,
"PatientBirthDate": null,
"InstitutionName": null
```

This convention follows JSON's standard semantics: `null` means "explicitly empty," absent means "not stated."

### 4.4 Sequences

DICOM Sequence (SQ) attributes are encoded as JSON arrays of objects. Each item in the sequence is a JSON object following the same keyword → value rules.

```json
"AnatomicRegionSequence": [
  {
    "CodeValue": "T-11000",
    "CodingSchemeDesignator": "SRT",
    "CodeMeaning": "Lung"
  }
]
```

Sequences may be nested (a sequence item may contain another sequence attribute).

### 4.5 Binary Data

Binary DICOM attributes (VRs: OB, OW, OF, OD, OL, OV) should generally be excluded from `tags`. Pixel data, overlay data, and similar bulk binary content belongs in the Zarr array itself or in separate Zarr arrays, not in JSON metadata.

If a small binary attribute must be preserved (e.g., an ICC profile or a lookup table), it may be included as a base64-encoded string with a `_base64` suffix on the key:

```json
"ICCProfile_base64": "AAAAAA..."
```

This is the escape hatch, not the normal case.

### 4.6 Multi-Valued Attributes

DICOM attributes with Value Multiplicity (VM) > 1 are encoded as JSON arrays:

```json
"PixelSpacing": [0.703, 0.703],
"ImagePositionPatient": [-249.5, -249.5, -150.0],
"ImageOrientationPatient": [1, 0, 0, 0, 1, 0],
"WindowCenter": [40],
"WindowWidth": [400]
```

Single-valued attributes (VM = 1) are bare values, not single-element arrays. Attributes whose VM is defined as `1-n` or `2` or similar are always arrays, even when only one value is present. This ensures consistent typing — a reader always knows that `PixelSpacing` is an array.

**Guiding rule:** attributes whose VM in PS3.6 is always exactly 1 are bare values. Attributes whose VM can be > 1 (including `1-n`, `2`, `2-n`, `3`, `6`, etc.) are always arrays.

---

## 5. Recommended Tags by Module

This section lists the DICOM tags most commonly useful for provenance, organized by DICOM module. This is guidance, not a requirement — writers should include whatever tags are relevant to the use case.

### 5.1 Patient Module

| Keyword | VR | Description |
|---|---|---|
| `PatientName` | PN | Patient's name |
| `PatientID` | LO | Primary identifier |
| `PatientBirthDate` | DA | Birth date |
| `PatientSex` | CS | Sex: `M`, `F`, `O` |
| `PatientAge` | AS | Age at acquisition |
| `PatientWeight` | DS | Weight in kg |

These are the most commonly anonymized fields. When anonymized, include them with `null` values.

### 5.2 Study Module

| Keyword | VR | Description |
|---|---|---|
| `StudyInstanceUID` | UI | Unique study identifier |
| `StudyDate` | DA | Date of study |
| `StudyTime` | TM | Time of study |
| `StudyDescription` | LO | Study description |
| `AccessionNumber` | SH | Accession number |
| `ReferringPhysicianName` | PN | Referring physician |
| `StudyID` | SH | Study ID |

### 5.3 Series Module

| Keyword | VR | Description |
|---|---|---|
| `SeriesInstanceUID` | UI | Unique series identifier |
| `SeriesNumber` | IS | Series number |
| `SeriesDescription` | LO | Series description |
| `SeriesDate` | DA | Date of series |
| `Modality` | CS | Modality: `CT`, `MR`, `PT`, `US`, etc. |
| `BodyPartExamined` | CS | Imaged body part |
| `ProtocolName` | LO | Protocol name |
| `Laterality` | CS | Laterality of body part |

### 5.4 Equipment Module

| Keyword | VR | Description |
|---|---|---|
| `Manufacturer` | LO | Equipment manufacturer |
| `ManufacturerModelName` | LO | Model name |
| `StationName` | SH | Station name |
| `DeviceSerialNumber` | LO | Serial number |
| `SoftwareVersions` | LO | Software version(s) |
| `InstitutionName` | LO | Institution name |
| `InstitutionalDepartmentName` | LO | Department |

### 5.5 CT-Specific

| Keyword | VR | Description |
|---|---|---|
| `KVP` | DS | Peak kilovoltage |
| `XRayTubeCurrent` | IS | Tube current in mA |
| `ExposureTime` | IS | Exposure time in ms |
| `Exposure` | IS | Exposure in mAs |
| `CTDIvol` | FD | Volume CT dose index |
| `ConvolutionKernel` | SH | Reconstruction kernel |
| `ReconstructionDiameter` | DS | Reconstruction diameter in mm |
| `DataCollectionDiameter` | DS | Data collection diameter |
| `FilterType` | SH | Filter type |
| `FocalSpots` | DS | Focal spot size(s) |
| `SingleCollimationWidth` | FD | Single collimation width in mm |
| `TotalCollimationWidth` | FD | Total collimation width in mm |
| `TableHeight` | DS | Patient table height in mm |
| `GantryDetectorTilt` | DS | Gantry tilt in degrees |
| `SpiralPitchFactor` | FD | Pitch factor |

### 5.6 MR-Specific

| Keyword | VR | Description |
|---|---|---|
| `MagneticFieldStrength` | DS | Field strength in Tesla |
| `MRAcquisitionType` | CS | `2D` or `3D` |
| `RepetitionTime` | DS | TR in ms |
| `EchoTime` | DS | TE in ms |
| `InversionTime` | DS | TI in ms |
| `FlipAngle` | DS | Flip angle in degrees |
| `SequenceName` | SH | Pulse sequence name |
| `ScanningSequence` | CS | Scanning sequence type(s) |
| `SequenceVariant` | CS | Sequence variant(s) |
| `ImagingFrequency` | DS | Imaging frequency in MHz |
| `EchoNumbers` | IS | Echo number(s) |
| `EchoTrainLength` | IS | Echo train length |
| `PixelBandwidth` | DS | Pixel bandwidth in Hz/pixel |
| `NumberOfAverages` | DS | Number of signal averages |
| `ReceiveCoilName` | SH | Receive coil name |
| `TransmitCoilName` | SH | Transmit coil name |
| `InPlanePhaseEncodingDirection` | CS | `ROW` or `COL` |
| `SAR` | DS | Specific absorption rate in W/kg |

### 5.7 PET-Specific

| Keyword | VR | Description |
|---|---|---|
| `Radiopharmaceutical` | LO | Radiopharmaceutical agent |
| `RadionuclideTotalDose` | DS | Total injected dose in Bq |
| `RadiopharmaceuticalStartTime` | TM | Injection time |
| `DecayCorrection` | CS | Decay correction applied |
| `AttenuationCorrectionMethod` | LO | Attenuation correction |
| `ScatterCorrectionMethod` | LO | Scatter correction |
| `ReconstructionMethod` | LO | Reconstruction method |
| `Units` | CS | `BQML`, `CNTS`, `GML`, etc. |

### 5.8 Frame of Reference

| Keyword | VR | Description |
|---|---|---|
| `FrameOfReferenceUID` | UI | Frame of reference UID |
| `PositionReferenceIndicator` | LO | Position reference |

### 5.9 SOP Common

| Keyword | VR | Description |
|---|---|---|
| `SOPClassUID` | UI | SOP Class UID |
| `SOPInstanceUID` | UI | SOP Instance UID |
| `InstanceCreationDate` | DA | Instance creation date |
| `InstanceCreationTime` | TM | Instance creation time |

---

## 6. Series-Level vs. Instance-Level Metadata

DICOM metadata exists at multiple levels of the information model hierarchy (patient → study → series → instance). When converting a DICOM series to a single Zarr array, most of the metadata in `tags` will be series-level — values shared across all instances in the series.

Instance-level metadata that varies across slices (e.g., per-slice `ImagePositionPatient`, per-slice `InstanceNumber`) is generally not stored in `tags`. The spatial embedding that varies per instance is captured by the convention's `space_origin` and `space_direction` fields, which represent the reconstructed 3D geometry.

If per-instance metadata must be preserved (e.g., per-frame acquisition times in a dynamic series), it can be stored as:

- A separate Zarr array in the same group (e.g., `acquisition_times` with one value per frame)
- An array-valued tag in `tags` where the array length matches the relevant axis size

The right approach depends on the use case. This extension does not mandate a specific mechanism for per-instance metadata.

---

## 7. Consistency Rules

- Tag keys must be valid PS3.6 keywords or, for private tags, uppercase hex tag codes.
- `null` values represent deliberately redacted data. Absent keys represent data that was not present in the source or is unknown.
- Multi-valued attributes (VM > 1 in PS3.6) must be encoded as JSON arrays, even when only one value is present.
- Single-valued attributes (VM = 1 in PS3.6) must be bare values, not wrapped in arrays.
- Sequence attributes must be encoded as arrays of objects, even when containing a single item.
- When a tag appears in both `tags` and is represented by a convention field (e.g., `SliceThickness` in `tags` and `thickness` on an axis), the convention field is authoritative for processing. The `tags` value is provenance.
- The `anonymized` flag should be set to `true` when any tags have been redacted. When set, readers should expect `null` values on patient-identifying fields.

---

## 8. Examples

### 8.1 CT Volume

An anonymized chest CT with full acquisition metadata:

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
    "nrrd": {
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
            "SOPClassUID": "1.2.840.10008.5.1.4.1.1.2",
            "SeriesInstanceUID": "1.2.840.113619.2.55.3.604688119.969.1069843699.84",
            "StudyDescription": "CHEST W/O CONTRAST",
            "SeriesDescription": "AXIAL 5mm",
            "BodyPartExamined": "CHEST",

            "Manufacturer": "GE MEDICAL SYSTEMS",
            "ManufacturerModelName": "LightSpeed16",
            "SoftwareVersions": ["06MW03.5"],
            "InstitutionName": null,

            "KVP": 120,
            "XRayTubeCurrent": 200,
            "ExposureTime": 570,
            "ConvolutionKernel": ["STANDARD"],
            "SliceThickness": 5.0,
            "PixelSpacing": [0.703, 0.703],
            "ReconstructionDiameter": 360.0,
            "GantryDetectorTilt": 0.0,

            "RescaleSlope": 1.0,
            "RescaleIntercept": -1024.0,
            "RescaleType": "HU",

            "PatientName": null,
            "PatientID": null,
            "PatientBirthDate": null,
            "PatientSex": "M",
            "PatientAge": "065Y",

            "StudyDate": "20040126",
            "AcquisitionDate": null,

            "WindowCenter": [40],
            "WindowWidth": [400]
          }
        }
      }
    }
  }
}
```

Note that `SliceThickness`, `PixelSpacing`, `RescaleSlope`, `RescaleIntercept`, and `RescaleType` overlap with convention fields. They are preserved in `tags` for provenance; the convention fields are authoritative.

### 8.2 MR Brain

A T1-weighted brain MRI with MR-specific acquisition parameters:

```json
"extensions": {
  "dicom": {
    "version": "1.0",
    "tags": {
      "Modality": "MR",
      "SOPClassUID": "1.2.840.10008.5.1.4.1.1.4",
      "SeriesInstanceUID": "1.2.840.113654.2.70.1.18342...",
      "StudyDescription": "BRAIN MRI",
      "SeriesDescription": "SAG T1 MPRAGE",
      "ProtocolName": "SAG T1 MPRAGE",
      "BodyPartExamined": "BRAIN",

      "Manufacturer": "SIEMENS",
      "ManufacturerModelName": "Prisma",
      "MagneticFieldStrength": 3.0,
      "InstitutionName": "Example Medical Center",

      "MRAcquisitionType": "3D",
      "ScanningSequence": ["GR", "IR"],
      "SequenceVariant": ["SK", "SP", "MP"],
      "RepetitionTime": 2300.0,
      "EchoTime": 2.98,
      "InversionTime": 900.0,
      "FlipAngle": 9.0,
      "NumberOfAverages": 1.0,
      "EchoTrainLength": 1,
      "PixelBandwidth": 240.0,
      "ReceiveCoilName": "HeadNeck_64",
      "InPlanePhaseEncodingDirection": "ROW",

      "SliceThickness": 1.0,
      "PixelSpacing": [1.0, 1.0],
      "SpacingBetweenSlices": 1.0,

      "PatientName": "Doe^John",
      "PatientID": "MRN12345",
      "PatientSex": "M",
      "PatientAge": "042Y",
      "StudyDate": "20240115",
      "FrameOfReferenceUID": "1.2.840.113654.2.70.1.18342..."
    }
  }
}
```

### 8.3 PET/CT

A PET volume with radiopharmaceutical information:

```json
"extensions": {
  "dicom": {
    "version": "1.0",
    "tags": {
      "Modality": "PT",
      "SOPClassUID": "1.2.840.10008.5.1.4.1.1.128",
      "SeriesDescription": "PET WB (AC)",
      "Units": "BQML",

      "Manufacturer": "Siemens",
      "ManufacturerModelName": "Biograph Vision 600",

      "RadiopharmaceuticalInformationSequence": [
        {
          "Radiopharmaceutical": "Fluorodeoxyglucose F^18^",
          "RadionuclideTotalDose": 370000000.0,
          "RadiopharmaceuticalStartTime": "091500.000",
          "RadionuclideHalfLife": 6586.2,
          "RadionuclideCodeSequence": [
            {
              "CodeValue": "C-111A1",
              "CodingSchemeDesignator": "SRT",
              "CodeMeaning": "^18^Fluorine"
            }
          ]
        }
      ],
      "DecayCorrection": "START",
      "AttenuationCorrectionMethod": "CT-based",
      "ReconstructionMethod": "PSF+TOF 3i21s",

      "SliceThickness": 2.0,
      "PixelSpacing": [2.0, 2.0],

      "PatientWeight": 75.0,
      "PatientSex": "F",
      "StudyDate": "20240220",

      "FrameOfReferenceUID": "1.2.840.113619.2.55.3..."
    }
  }
}
```

This shows nested sequences: `RadiopharmaceuticalInformationSequence` contains one item, which itself contains the nested `RadionuclideCodeSequence`.

### 8.4 Minimal

A converted DICOM file with only the modality preserved:

```json
"extensions": {
  "dicom": {
    "version": "1.0",
    "tags": {
      "Modality": "CT"
    }
  }
}
```

### 8.5 With Coded Sequence Attributes

A CT volume that includes coded anatomy using DICOM's standard sequence pattern:

```json
"tags": {
  "Modality": "CT",
  "AnatomicRegionSequence": [
    {
      "CodeValue": "T-28000",
      "CodingSchemeDesignator": "SRT",
      "CodeMeaning": "Lung"
    }
  ],
  "ProcedureCodeSequence": [
    {
      "CodeValue": "RPID5740",
      "CodingSchemeDesignator": "99ORBIS",
      "CodeMeaning": "CT Chest without contrast"
    }
  ]
}
```

---

## 9. Fields Deliberately Excluded from `tags`

| DICOM attribute | Reason |
|---|---|
| Pixel Data (7FE0,0010) | Zarr array data |
| Overlay Data | Separate array if needed |
| Waveform Data | Out of scope (not imaging) |
| Private tags (by default) | Include only if specifically needed, using hex keys |
| Group Length tags | Encoding artifact, per PS3.18 |
| File Meta Information (0002,xxxx) | File format metadata, not data semantics |

---

## 10. Design Notes

**Why keywords instead of hex codes.** The standard DICOM JSON Model (PS3.18 Annex F) uses hex tag codes (`"00080060"`) as keys. This is correct for a lossless DICOM encoding, but hostile to developers who don't have a tag dictionary memorized. Keywords like `Modality` and `PatientID` are self-documenting. Since this extension is for provenance — not round-trip binary DICOM encoding — readability wins.

**Why no `vr` on each value.** PS3.18 includes a `vr` field on every attribute. This extension omits it because the VR is determined by the keyword (via PS3.6 lookup), and the JSON type makes the encoding unambiguous. Including `vr` on every tag would roughly double the size of `tags` for no practical gain. If VR-level fidelity is needed, use PS3.18 in a separate attribute.

**Why values are JSON-native, not wrapped in `Value` arrays.** PS3.18 wraps every value in `{"vr": "...", "Value": [...]}`. This makes sense for a general-purpose DICOM JSON encoding where any tag might appear, but for provenance metadata that humans read, `"KVP": 120` is better than `"00180060": {"vr": "DS", "Value": [120]}`.

**Why `tags` is a separate namespace.** Without the `tags` object, DICOM keywords would share the same JSON namespace as extension metadata like `version`, `anonymized`, and `schema`. DICOM has thousands of keywords — the collision risk is real (e.g., a future extension field could conflict with a DICOM keyword). The `tags` object makes the boundary explicit: everything inside is DICOM, everything outside is about the extension.

**Why `null` means redacted, not absent.** In anonymized datasets, it matters whether a field was removed (it existed but was scrubbed) or was never present. A `null` value distinguishes these cases. This follows JSON convention and matches the existing duckn precedent from the convention's CT example.

**Why multi-valued attributes are always arrays.** DICOM's VM rules mean that `PixelSpacing` always has exactly 2 values and `ImageOrientationPatient` always has 6. Making these consistently arrays (not sometimes a bare value, sometimes an array) eliminates a class of reader bugs. The rule is simple: if PS3.6 says VM > 1, it's an array.

**Why display fields like `WindowCenter`/`WindowWidth` are permitted.** The duckn convention excludes display hints by design, but this extension's purpose is provenance. Window/level values from the source DICOM are useful context for understanding how the data was intended to be viewed, even if they don't constrain the Zarr rendering pipeline.

**Relationship to the slicerseg extension's `dicom` field.** The segmentation extension has its own `dicom` object within each segment for the DICOM Segmentation IOD classification (category, type, anatomic region). That is segment-level DICOM metadata. This extension is array-level DICOM metadata — acquisition parameters, patient context, series identification. They coexist without conflict: the slicerseg extension's `dicom` lives inside segment objects; this extension's `dicom` lives at the top level of `"extensions"`.

**Relationship to PS3.18 Annex F.** This extension is not a replacement for the standard DICOM JSON Model. If a workflow requires lossless DICOM metadata round-tripping (preserving VRs, exact DS string representations, private tag creators), it should store the PS3.18 JSON in a separate Zarr attribute (e.g., `"dicom_json"`). This extension provides a human-friendly subset.