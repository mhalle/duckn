# Diffusion-Weighted MRI Extension for duckn

**Extension name:** `dwmri`
**Version:** 1.0
**Status:** Draft
**Source convention:** [NA-MIC NRRD DWI format](https://www.na-mic.org/wiki/NAMIC_Wiki:DTI:Nrrd_format)

---

## 1. Purpose

This document defines the `dwmri` extension for the duckn convention. It replaces the NRRD key/value pair convention for diffusion-weighted MRI data — where acquisition parameters were encoded as `DWMRI_gradient_NNNN:=x y z` strings and `DWMRI_b-value:=b` — with a structured JSON representation.

The data model is the same. What changes is the encoding: JSON arrays and objects replace space-delimited strings and zero-padded index keys.

The purpose of a DWI file format is to record all the information necessary to unambiguously reconstruct diffusion tensors (or perform other diffusion models) from the measured images. This includes:

- The nominal b-value
- Per-volume gradient directions or full B-matrices
- The measurement frame: the relationship between the coordinate frame in which gradient coefficients are expressed and the world space in which image orientation is defined
- The order in which DWI values were acquired (encoded by the position of the diffusion axis among the spatial axes)
- Anatomical location of all images (handled by the duckn convention's spatial embedding)

### What this extension does not cover

Diffusion *tensor* volumes — where the voxel values are the six independent components of the estimated symmetric tensor — do not use this extension. Those are fully described by the convention's `kind: "3D-symmetric-matrix"` axis, `measurement_frame`, `sample_units: "mm²/s"`, and `intent: "diffusion-tensor"`. See convention §6.3 for an example.

This extension is for the *raw acquisition*: the 4D volume of signal intensities measured under different diffusion-sensitizing gradient configurations, from which tensors or other models are subsequently estimated.

---

## 2. Relationship to duckn Convention Fields

The following aspects of a DWI dataset are already captured by Zarr or the duckn convention and are not duplicated in this extension:

| DWI information | Captured by |
|---|---|
| Volume dimensions (spatial + DWI count) | Zarr `shape` |
| Voxel type (typically `int16` or `uint16`) | Zarr `data_type` |
| Anatomical orientation | `space`, `space_origin`, `axes[i].space_direction` |
| Voxel spacing | `axes[i].space_direction` magnitude |
| Slice thickness | `axes[i].thickness` |
| Measurement frame | `measurement_frame` |
| Axis semantics (spatial vs. DWI) | `axes[i].kind` (`"space"` vs. `"list"`) |

The convention's `measurement_frame` field is critical for DWI data. By default (when `gradient_frame` is absent or `"measurement"`), this extension's gradient vectors (or B-matrices) are expressed in the measurement frame — a reader must apply `measurement_frame` to rotate them into world space before computing tensor components in world coordinates. See the `gradient_frame` field (§4.1) for alternative coordinate frame conventions.

---

## 3. Data Layout

A DWI dataset is a 4D Zarr array: three spatial dimensions plus one non-spatial dimension indexing the diffusion-weighted volumes (including any non-diffusion-weighted baseline volumes). Each position along the non-spatial axis corresponds to one acquired volume with a specific gradient configuration.

### Axis identification

The non-spatial axis has `kind: "list"`. The NRRD convention also permits `kind: "vector"` for compatibility with ITK's default non-scalar output; this extension treats both identically.

### Axis ordering and interleaving

The non-spatial axis can appear at any position among the spatial axes. Its position determines the interleaving of the voxel data:

| Axis order (kind) | Interleaving | Description |
|---|---|---|
| `space space space list` | Volume | All slices for one gradient, then the next gradient |
| `space space list space` | Slice | All DWI values for one slice location, then the next slice |
| `list space space space` | Pixel | All DWI values for one voxel, then the next voxel |

The per-axis extension metadata (gradients, B-matrices) is attached to whichever axis has `kind: "list"`.

### Baseline volumes

Baseline (non-diffusion-weighted, b=0) volumes are identified by a zero-vector gradient `[0, 0, 0]` or a zero B-matrix `[0, 0, 0, 0, 0, 0]`. There is no requirement that baselines appear at any particular position along the DWI axis — they may be first, last, interspersed, or absent.

---

## 4. Extension Fields

The `dwmri` extension has both top-level fields (declared in the `"duckn"` object's `"extensions"`) and per-axis fields (on the `"list"` axis).

### 4.1 Top-Level Extension Fields

```json
"extensions": {
  "dwmri": {
    "version": "1.0",
    "b_value": 1000,
    "b_value_units": "s/mm²",
    "gradient_frame": "measurement",
    "acquisition": {
      "phase_encoding_direction": "j",
      "total_readout_time": 0.0256
    }
  }
}
```

#### `version`

Required. The version of this extension specification.

```json
"version": "1.0"
```

#### `b_value`

Required. The nominal scalar diffusion-weighting parameter. This is the b-value that applies to gradients (or B-matrices) at their maximum magnitude after implicit normalization (see §5).

```json
"b_value": 1000
```

The effective b-value for each individual volume depends on the magnitude of its gradient vector or B-matrix relative to the maximum (see §5).

#### `b_value_units`

The units of `b_value`. If absent, the default is `"s/mm²"` (seconds per square millimeter), which is the universal convention in diffusion MRI.

```json
"b_value_units": "s/mm²"
```

This field exists for explicitness and completeness. In practice, the value is always `"s/mm²"`.

#### `schema`

A URL pointing to a schema or specification document for the extension. Optional.

```json
"schema": "https://example.org/dwmri-zarr/v1.0/schema.json"
```

#### `gradient_frame`

A string identifying the coordinate frame in which `gradients` or `b_matrices` are expressed, relative to `measurement_frame`. Optional.

| Value | Meaning |
|---|---|
| `"measurement"` | Gradients are in the measurement frame (default). Apply `measurement_frame` to transform to world space. This is the NA-MIC NRRD convention. |
| `"world"` | Gradients are already in world space. `measurement_frame` is not needed for gradient interpretation (though it may still be present for other purposes). This is the MRtrix convention. |
| `"image"` | Gradients are in the image voxel coordinate frame. Apply the rotation part of the IJK-to-world affine to transform to world space. This is the FSL bvec convention. |

When absent, the default is `"measurement"`, matching NRRD convention behavior. This field exists to support lossless round-trip conversion from formats that use different coordinate conventions. In particular, data converted from FSL bvec/bval files has gradients in image space, and data converted from MRtrix `.b` files or DICOM has gradients in scanner/world space. Recording the original frame avoids silent reinterpretation errors.

When `gradient_frame` is `"image"`, the `measurement_frame` convention field is not used for gradient interpretation (it may be absent or identity).

#### `acquisition`

An optional object containing MR acquisition parameters relevant to DWI preprocessing. These fields are not needed for tensor estimation but are critical for distortion correction (eddy currents, susceptibility) and motion correction. They correspond to fields defined by the BIDS specification and commonly extracted from DICOM headers by tools like `dcm2niix`.

```json
"acquisition": {
  "phase_encoding_direction": "j",
  "total_readout_time": 0.0256,
  "effective_echo_spacing": 0.00059,
  "echo_time": 0.089,
  "repetition_time": 7.2,
  "gradient_pulse_duration": 0.0035,
  "gradient_pulse_separation": 0.0085,
  "multiband_acceleration_factor": 3,
  "parallel_reduction_factor_in_plane": 2,
  "slice_timing": [0.0, 0.5, 1.0, 0.0, 0.5, 1.0]
}
```

All fields within `acquisition` are optional. All time values are in SI seconds. The field names follow BIDS JSON sidecar conventions (in snake_case) where applicable:

| Field | Type | Description | BIDS equivalent | Bruker equivalent |
|---|---|---|---|---|
| `phase_encoding_direction` | string | Phase encoding axis and polarity. Values use image-axis convention: `"i"`, `"j"`, `"k"`, `"i-"`, `"j-"`, `"k-"`. | `PhaseEncodingDirection` | — |
| `total_readout_time` | number | Time (seconds) from center of first echo to center of last echo in the EPI readout. Required for FSL `topup`/`eddy`. | `TotalReadoutTime` | — |
| `effective_echo_spacing` | number | Time (seconds) between the centers of adjacent echoes in the EPI readout train. | `EffectiveEchoSpacing` | — |
| `echo_time` | number | Echo time (seconds). | `EchoTime` | — |
| `repetition_time` | number | Volume repetition time (seconds). | `RepetitionTime` | — |
| `gradient_pulse_duration` | number | Duration of each diffusion-sensitizing gradient pulse (δ, "little delta") in seconds. For a standard pulsed gradient spin echo (PGSE) sequence, this is the time each trapezoidal gradient lobe is applied. | — | `PVM_DwGradDur` (ms → s) |
| `gradient_pulse_separation` | number | Time between the leading edges of the two diffusion-sensitizing gradient pulses (Δ, "big delta") in seconds. For PGSE, this is the interval from the onset of the first gradient pulse to the onset of the second. | — | `PVM_DwGradSep` (ms → s) |
| `multiband_acceleration_factor` | integer | Simultaneous multi-slice (SMS / multiband) factor. | `MultibandAccelerationFactor` | — |
| `parallel_reduction_factor_in_plane` | integer | In-plane parallel imaging acceleration (GRAPPA, ARC, SENSE). | `ParallelReductionFactorInPlane` | — |
| `slice_timing` | array of numbers | Time (seconds) of each slice acquisition relative to the start of the volume. Length equals the number of slices. | `SliceTiming` | — |

These fields are optional because they are acquisition metadata, not diffusion encoding parameters. A file that only records gradient directions and b-values is still a valid DWMRI extension. However, including acquisition parameters greatly improves reusability — without them, distortion correction is impossible or requires manual parameter entry.

#### Gradient pulse timing and b-value

For a PGSE sequence with rectangular gradient pulses, the b-value is determined by:

$b = γ^2 G^2 δ^2 (Δ - δ/3)$

where $γ$ is the gyromagnetic ratio and $G$ is the gradient amplitude. When `gradient_pulse_duration` and `gradient_pulse_separation` are present alongside `b_value`, a reader can recover the gradient amplitude $G$ — or verify consistency between the reported b-value and the timing parameters.

**Pulse shape assumption.** The formula above assumes ideal rectangular gradient pulses. Real implementations use trapezoidal pulses with finite ramp times, and some sequences use sinusoidal or half-sine lobes. The stored `gradient_pulse_duration` and `gradient_pulse_separation` are the *programmed* sequence parameters (the flat-top duration and the center-to-center separation for trapezoidal pulses, respectively), not effective values corrected for ramp shape. For trapezoidal pulses, the effective δ is shorter than the programmed value by approximately one-third of the ramp time. Scanners that report `PVM_DwEffBval` (Bruker) or compute b-values internally already account for this — the `b_value` field remains authoritative. A future version of this extension may add a `gradient_pulse_shape` field (`"rectangular"`, `"trapezoidal"`, `"sinusoidal"`) and ramp time parameters for applications that need exact pulse shape modeling.

The `b_value` field (or per-volume `b_values`) remains authoritative for the diffusion weighting applied. The timing parameters are needed for:

- **Quantitative diffusion models** beyond DTI (e.g., CHARMED, NODDI, axon diameter estimation) that model restricted diffusion as a function of δ and Δ independently
- **Preclinical protocol reporting** where δ and Δ are standard parameters to document
- **Cross-scanner reproducibility** where matching b-value alone is insufficient — two acquisitions with the same b-value but different δ/Δ probe different diffusion time scales

When converting from BIDS, the JSON sidecar fields map directly to these fields. When converting from DICOM, `dcm2niix` and similar tools extract these values from standard and private DICOM tags.

### 4.2 Per-Axis Extension Fields

The per-axis metadata is carried on the `"list"` (or `"vector"`) axis within the `axes` array. This is where the per-volume diffusion encoding is described.

```json
{
  "kind": "list",
  "extensions": {
    "dwmri": {
      "gradients": [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1]
      ]
    }
  }
}
```

Exactly one of `gradients` or `b_matrices` must be present. A file must not contain both.

#### `gradients`

An array of 3-component vectors, one per position along the DWI axis. Each vector gives the diffusion-sensitizing gradient direction. The coordinate frame depends on the `gradient_frame` field (§4.1): by default, vectors are in the measurement frame and must be transformed to world space via `measurement_frame`.

```json
"gradients": [
  [0, 0, 0],
  [-0.824, -0.418, -0.383],
  [-0.568, 0.502, -0.652],
  [0.430, 0.144, 0.891]
]
```

The length of the `gradients` array must equal the size of the DWI axis (the corresponding element of Zarr `shape`).

When `b_values` is absent, gradient vectors are subject to implicit normalization (§5): their magnitudes encode relative b-value weighting. When `b_values` is present, implicit normalization is superseded and gradients should be interpreted as directions (see §4.2.1).

A zero vector `[0, 0, 0]` identifies a non-diffusion-weighted (baseline) volume.

#### `b_matrices`

An array of 6-component vectors, one per position along the DWI axis. Each vector gives the upper triangle of the symmetric 3×3 B-matrix in the order `[Bxx, Bxy, Bxz, Byy, Byz, Bzz]`. The coordinate frame follows the same rules as `gradients` (see `gradient_frame`, §4.1).

```json
"b_matrices": [
  [0, 0, 0, 0, 0, 0],
  [0.5, 0.0, 0.5, 0.0, 0.0, 0.5],
  [0.5, 0.0, -0.5, 0.0, 0.0, 0.5]
]
```

The off-diagonal entries (`Bxy`, `Bxz`, `Byz`) are **not** pre-multiplied by 2. This matches the NA-MIC convention: the signal model is $A_i = A_0 \exp(-b \cdot \mathrm{trace}(\mathbf{B}^T \mathbf{D}))$, which expands to $A_0 \exp(-b \cdot (B_{xx} D_{xx} + 2 B_{xy} D_{xy} + 2 B_{xz} D_{xz} + B_{yy} D_{yy} + 2 B_{yz} D_{yz} + B_{zz} D_{zz}))$. The factor of 2 on cross-terms comes from the trace operation on the symmetric matrices, not from the stored values.

In the simple (and common) case where imaging gradient contributions are negligible, $\mathbf{B} = \mathbf{g}\mathbf{g}^T$. Full B-matrices are used when the contribution of imaging gradients to diffusion weighting is known and significant.

The length of the `b_matrices` array must equal the size of the DWI axis. A zero matrix `[0, 0, 0, 0, 0, 0]` identifies a baseline volume. B-matrices are subject to implicit normalization (§5).

#### `nex`

An object mapping DWI axis indices (as strings) to repeat counts. Indicates that the volume at the given index and the subsequent `count - 1` volumes are repeated acquisitions with the same gradient configuration.

```json
"nex": {
  "0": 2,
  "5": 3
}
```

This means: index 0 and index 1 are repeats (NEX=2 starting at 0); indices 5, 6, and 7 are repeats (NEX=3 starting at 5). The gradient or B-matrix at the starting index applies to all volumes in the run; the entries for the subsequent indices in `gradients` (or `b_matrices`) should be identical copies.

`nex` is optional. When absent, every volume has a unique gradient/B-matrix entry and no repeat structure is assumed. When present, it provides a hint to readers about the acquisition's repeat structure — it does not change the requirement that every position along the DWI axis has an entry in `gradients` or `b_matrices`.

---

## 5. Implicit Normalization

The NA-MIC convention uses implicit normalization to encode multi-b-value acquisitions with a single nominal b-value. This extension preserves that convention when `b_values` is absent.

The convention works as follows: the nominal `b_value` applies to whichever gradient vector (or B-matrix) has the largest magnitude. All other gradients' effective b-values are determined by their magnitude relative to this maximum. Gradients need not be pre-normalized; the reader computes the normalization factor from the data.

For gradient vectors, magnitude is the L² norm: $\|\mathbf{g}\| = \sqrt{g_x^2 + g_y^2 + g_z^2}$.

For B-matrices, magnitude is the Frobenius norm: $\|\mathbf{B}\|_F = \sqrt{B_{xx}^2 + 2B_{xy}^2 + 2B_{xz}^2 + B_{yy}^2 + 2B_{yz}^2 + B_{zz}^2}$.

The effective b-value for volume $i$ is:

- **Gradients:** $b_{\mathrm{eff},i} = b_{\mathrm{nominal}} \cdot \|\mathbf{g}_i\|^2 / \|\mathbf{g}_{\max}\|^2$
- **B-matrices:** $b_{\mathrm{eff},i} = b_{\mathrm{nominal}} \cdot \|\mathbf{B}_i\|_F / \|\mathbf{B}_{\max}\|_F$

Note the different exponents: gradients use squared magnitude because b-value is proportional to $g^2$, while B-matrices already incorporate this quadratic relationship (since $\mathbf{B} \propto \mathbf{g}\mathbf{g}^T \propto g^2$), so a linear ratio of Frobenius norms is correct.

### Multi-b-value encoding

To represent an acquisition with multiple b-values using a single nominal b-value, gradient magnitudes are scaled by $\sqrt{b_{\mathrm{desired}} / b_{\mathrm{nominal}}}$.

For example, with `b_value: 1000`:

- A volume at b=1000 has unit-length gradient vectors (after normalization)
- A volume at b=500 has gradient vectors scaled to $\sqrt{500/1000} = \sqrt{1/2} \approx 0.707$ of the maximum length
- A baseline (b=0) volume has a zero vector

### Alternative: explicit b-values

When the implicit normalization convention is inconvenient — for example, in multi-shell acquisitions where explicit per-volume b-values are clearer — the per-axis field `b_values` (§4.2.1) may be used. When `b_values` is present, the implicit normalization convention is superseded.

### 4.2.1 `b_values` (per-axis, optional)

An array of explicit per-volume b-values, one per position along the DWI axis, in the same units as `b_value_units`. When present, this provides the effective b-value for each volume directly, and gradient vectors should be interpreted as unit directions (or near-unit, for the purpose of identifying the sensitization axis) rather than magnitude-encoding the b-value.

```json
{
  "kind": "list",
  "extensions": {
    "dwmri": {
      "b_values": [0, 1000, 1000, 1000, 1000, 1000, 1000, 2000, 2000, 2000, 2000, 2000, 2000],
      "gradients": [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
        [0.707, 0.707, 0],
        [0.707, 0, 0.707],
        [0, 0.707, 0.707],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
        [0.707, 0.707, 0],
        [0.707, 0, 0.707],
        [0, 0.707, 0.707]
      ]
    }
  }
}
```

When `b_values` is present, the top-level `b_value` is still required and gives the nominal b-value for the acquisition (typically the maximum shell). The implicit normalization convention (§5) is superseded: the `b_values` array is authoritative for per-volume diffusion weighting.

The length of `b_values` must equal the size of the DWI axis and the length of `gradients` (or `b_matrices`).

---

## 6. Dependencies on Convention Fields

This extension depends on the following duckn convention fields. These dependencies should be checked by a reader.

| Convention field | Role | Required? |
|---|---|---|
| `measurement_frame` | Defines the coordinate frame for gradient vectors and B-matrices | Required when `gradient_frame` is `"measurement"` (the default). Not needed for gradient interpretation when `gradient_frame` is `"world"` or `"image"`. If absent and `gradient_frame` is `"measurement"` or unset, gradients are assumed to be in world space (the measurement frame defaults to identity). |
| `space` | Names the world coordinate system | Required for unambiguous gradient interpretation |
| `space_origin` | Translational position of the volume | Required for spatial embedding |
| `axes[i].space_direction` | Per-axis displacement vectors | Required for spatial axes. Also used for gradient transformation when `gradient_frame` is `"image"`. |
| `axes[i].kind` | Identifies the DWI axis (`"list"` or `"vector"`) | Required |

The measurement frame is the critical link between gradient coordinates and world space. Without it, the coordinate frame of the gradient vectors is ambiguous. The NA-MIC convention documentation emphasizes this as a persistent source of errors in DWI processing.

---

## 7. Mapping from Other Formats

### 7.1 Mapping from NRRD Key/Value Pairs

| NRRD key/value | duckn `dwmri` extension field |
|---|---|
| `modality:=DWMRI` | Presence of `dwmri` extension (implicit) |
| `DWMRI_b-value:=b` | `b_value` (top-level) |
| `DWMRI_gradient_NNNN:=x y z` | `gradients[NNNN]` (per-axis) |
| `DWMRI_B-matrix_NNNN:=xx xy xz yy yz zz` | `b_matrices[NNNN]` (per-axis) |
| `DWMRI_NEX_NNNN:=M` | `nex["NNNN"]` (per-axis) |
| `measurement frame:` | `measurement_frame` (convention field, not extension) |
| `kinds: ... list ...` | `axes[i].kind: "list"` (convention field) |

The NRRD `modality:=DWMRI` key is not mapped to a field in the extension. The presence of the `dwmri` extension in the `extensions` object serves the same purpose: it declares that this array is a diffusion-weighted MRI acquisition.

### 7.1.1 NRRD key/value encoding

When DWI metadata is stored in a NRRD file (rather than a Zarr store), the same fields are encoded as key/value pairs with the `DWMRI_` prefix, following the original NA-MIC convention. This enables lossless NRRD ↔ Zarr conversion.

```
modality:=DWMRI
DWMRI_b-value:=1000
DWMRI_gradient_0000:=0 0 0
DWMRI_gradient_0001:=1 0 0
DWMRI_gradient_0002:=0 1 0
DWMRI_gradient_0003:=0 0 1
DWMRI_NEX_0000:=2
```

The mapping is mechanical: the four-digit zero-padded index becomes an array index, and space-delimited components become JSON arrays.

### 7.2 Mapping from DICOM

DICOM defines DWI-related tags in the MR Diffusion Macro (Supplement 49, §C.8.12.5.9). The relevant standard tags are:

| DICOM tag | Name | duckn field |
|---|---|---|
| (0018,9087) | Diffusion b-value | `b_values[i]` (per-volume), or `b_value` (if single) |
| (0018,9089) | Diffusion Gradient Orientation | `gradients[i]` |
| (0018,9075) | Diffusion Directionality | (use to identify baselines: `"NONE"` → zero gradient) |
| (0018,9602) | DiffusionBValueXX | `b_matrices[i][0]` |
| (0018,9603) | DiffusionBValueXY | `b_matrices[i][1]` |
| (0018,9604) | DiffusionBValueXZ | `b_matrices[i][2]` |
| (0018,9605) | DiffusionBValueYY | `b_matrices[i][3]` |
| (0018,9606) | DiffusionBValueYZ | `b_matrices[i][4]` |
| (0018,9607) | DiffusionBValueZZ | `b_matrices[i][5]` |
| (0018,9117) | MR Diffusion Sequence | (container sequence for the above) |

Important caveats for DICOM conversion:

**Measurement frame is identity.** The DICOM specification for Diffusion Gradient Orientation (0018,9089) defines the gradient direction in the patient coordinate system (LPS), which implies the measurement frame is the identity matrix. When converting from DICOM, if the duckn `space` is `"left-posterior-superior"`, the measurement frame should be set to the identity. If the convention uses RAS, the gradient components need the standard LPS↔RAS sign flips (negate first two components).

**DICOM B-matrix tags are b-weighted.** The per-component tags (0018,9602–9607) store the elements of $b \cdot \mathbf{g}\mathbf{g}^T$, not the normalized B-matrix. To convert to this extension's `b_matrices` (which use the implicit normalization convention), divide each component by `b_value`. Alternatively, store the DICOM per-volume b-values in the `b_values` array and the gradient direction in `gradients`, bypassing `b_matrices` entirely.

**B-matrix loses gradient polarity.** DICOM's per-component B-value tags (0018,9602–9607) encode $b \cdot \mathbf{g}\mathbf{g}^T$, which is a positive semi-definite matrix. The sign of the gradient direction is lost. This makes the B-matrix representation insufficient for tools like FSL's `eddy`, which leverages polar-opposite gradient pairs to model eddy current distortions. When full B-matrices are converted from DICOM, the `gradients` array should also be preserved if available.

**Vendor-specific private tags.** Most scanners store DWI parameters in vendor-specific private DICOM tags rather than the standard tags above. A converter should check standard tags first, then fall back to private tags. The major vendor conventions are documented at the [NA-MIC DICOM for DWI page](https://www.na-mic.org/wiki/NAMIC_Wiki:DTI:DICOM_for_DWI_and_DTI):

| Vendor | B-value tag | Gradient direction tags | Notes |
|---|---|---|---|
| Siemens (V-series) | (0019,100C) | (0019,100E) or CSA header `B_matrix` | CSA B-matrix is more reliable than gradient direction |
| Siemens (X-series) | (0018,9087) | (0018,9089) | Uses standard public tags in enhanced DICOM |
| GE | (0043,1039) mod 100000 | (0019,10BB/BC/BD) | Gradients in image frame; interpretation depends on PhaseEncodingDirection |
| Philips | (2001,1003) | (2005,10B0/B1/B2) | Also stores derived (isotropic) images in same series; filter these |
| Canon/Toshiba | (0018,9087) | Image comments or (0018,9089) | Classic format may omit b=0; enhanced format uses public tags |
| UIH | (0065,1009) | (0065,1037) | |

When DICOM source data is preserved via the `dicom` extension (see dicom-extension.md), the original DICOM tags are available in `extensions.dicom.tags`. The `dwmri` extension's structured fields are the preferred interface for DWI processing; the DICOM tags serve as provenance.

### 7.3 Mapping from FSL bvec/bval

FSL represents DWI encoding as a pair of sidecar files alongside the NIfTI image:

- `.bval`: a single row of space-delimited b-values (one per volume, in s/mm²)
- `.bvec`: three rows of space-delimited floats — x, y, z components of unit gradient vectors (one column per volume)

The FSL format has several properties that differ from the NRRD/duckn convention:

**Explicit per-volume b-values.** FSL uses explicit b-values for every volume (like the `b_values` per-axis field in this extension) rather than encoding b-value in gradient magnitude. When converting, populate both the per-axis `b_values` array and the `gradients` array (with unit-norm directions).

**Image-space gradient directions.** FSL bvec directions are defined relative to the image voxel axes, not world or scanner coordinates. Specifically, they use FSL's internal "radiological voxel" convention: if the NIfTI has a neurological storage orientation (positive determinant of qform/sform), the first component of the bvec is flipped relative to the NIfTI voxel axes. When converting to duckn, either:
1. Transform the gradients into measurement-frame or world-space coordinates and set `gradient_frame: "measurement"` (or `"world"`), or
2. Store them as-is and set `gradient_frame: "image"` to record that they are in voxel space.

Option (1) is preferred for interoperability with the NRRD ecosystem. Option (2) preserves the original data for lossless round-trip with FSL.

**Caveat on `gradient_frame: "image"` with FSL data.** If option (2) is used, readers must be aware that FSL bvecs use a radiological voxel convention: when the NIfTI sform/qform has a positive determinant (neurological storage), FSL internally flips the sign of the first bvec component. Storing raw FSL bvecs as `gradient_frame: "image"` is only correct if the reader applies the same determinant-dependent x-flip. This subtlety is the reason option (1) — transforming to world or measurement space at conversion time — is strongly preferred. If option (2) is used, a converter should document whether the x-flip has been applied or whether the bvecs are stored verbatim.

**No measurement frame.** The FSL format has no concept of measurement frame. The coupling of gradient directions to image axes means that any reorientation of the NIfTI image (e.g., `fslreorient2std`) must be accompanied by corresponding rotation of the bvec file. This is a documented source of errors.

### 7.4 Mapping from MRtrix

MRtrix uses a single gradient table (the `dw_scheme` header entry or a standalone `.b` file) with one row per volume: `[x, y, z, b]`, where the direction is in **scanner/world coordinates** (not image space) and `b` is the b-value in s/mm².

| MRtrix | duckn |
|---|---|
| `dw_scheme` row `[gx, gy, gz, b]` | `gradients[i] = [gx, gy, gz]`, `b_values[i] = b` |
| Direction in scanner (real) space | Set `gradient_frame: "world"` or transform to measurement frame |
| Per-volume b-value | `b_values` (per-axis) |

MRtrix normalizes gradient vectors to unit length and scales b-values accordingly. This is the opposite of the NRRD convention's implicit normalization. When converting MRtrix data to duckn, the explicit `b_values` representation is the natural fit.

### 7.5 Mapping from BIDS

BIDS stores DWI data as NIfTI + `.bval` + `.bvec` + JSON sidecar. The bval/bvec files follow FSL format (§7.3). The JSON sidecar provides acquisition parameters that map to the `acquisition` object:

| BIDS JSON field | duckn field |
|---|---|
| `PhaseEncodingDirection` | `acquisition.phase_encoding_direction` |
| `TotalReadoutTime` | `acquisition.total_readout_time` |
| `EffectiveEchoSpacing` | `acquisition.effective_echo_spacing` |
| `EchoTime` | `acquisition.echo_time` |
| `RepetitionTime` | `acquisition.repetition_time` |
| `MultibandAccelerationFactor` | `acquisition.multiband_acceleration_factor` |
| `ParallelReductionFactorInPlane` | `acquisition.parallel_reduction_factor_in_plane` |
| `SliceTiming` | `acquisition.slice_timing` |
| `DiffusionPulseDuration` (non-standard) | `acquisition.gradient_pulse_duration` |
| `DiffusionPulseSeparation` (non-standard) | `acquisition.gradient_pulse_separation` |
| `.bval` file | `b_values` (per-axis) |
| `.bvec` file | `gradients` (per-axis), with `gradient_frame: "image"` |

### 7.6 Mapping from Bruker ParaVision

Bruker preclinical MRI scanners (ParaVision / ParaVision 360) store acquisition parameters in plaintext `method` and `acqp` files within each scan directory. Diffusion parameters are stored as ParaVision Managed (PVM) parameters.

ParaVision distinguishes *input* parameters (user-specified) from *output* parameters (computed by the sequence). Both are available in the `method` file:

| Role | Parameter | Description |
|------|-----------|-------------|
| Input | `PVM_DwBvalEach` | Nominal b-value(s) requested by the user (s/mm²). Array for multi-shell (e.g., `[1000, 2000]`). |
| Input | `PVM_DwDir` | User-specified gradient directions (unit vectors). Does **not** include the b=0 volume. |
| Input | `PVM_DwNDiffDir` | Number of diffusion directions |
| Input | `PVM_DwNDiffExpEach` | Number of diffusion experiments per direction |
| Input | `PVM_DwAoImages` | Number of A0 (b=0) reference images acquired per repetition |
| Input | `PVM_DwNDiffExp` | Total number of diffusion experiments (directions × shells × repetitions) |
| Output | `PVM_DwEffBval` | Effective b-values for all volumes (including b=0), accounting for imaging gradient contributions |
| Output | `PVM_DwGradVec` | Gradient vectors for all volumes (including b=0). **Not unit-normalized** — magnitudes are proportional to the applied gradient amplitude relative to the maximum. |
| Output | `PVM_DwGradDur` | Gradient pulse duration δ (ms) |
| Output | `PVM_DwGradSep` | Gradient pulse separation Δ (ms) |
| Geometry | `PVM_SPackArrGradOrient` | Slice pack gradient orientation matrix (3×3) |

`PVM_DwAoImages` and `PVM_DwNDiffExp` do not map to duckn fields but explain the total volume count and the placement of b=0 volumes within the DWI axis. The b=0 volumes are identified by zero-vector entries in `gradients` (or zero `b_values` entries), not by a separate count field.

#### Mapping table

| Bruker parameter | duckn field | Notes |
|---|---|---|
| `PVM_DwBvalEach` | `b_value` | Use `max(PVM_DwBvalEach)` as the scalar `b_value`. `PVM_DwBvalEach` is an array for multi-shell acquisitions (e.g., `[1000, 2000]`); the top-level `b_value` takes the maximum shell. |
| `PVM_DwEffBval` | `b_values` (per-axis) | Preferred over `PVM_DwBvalEach` for accuracy. Includes imaging gradient contribution. |
| `PVM_DwGradVec` | `gradients` (per-axis) | See gradient normalization below. |
| `PVM_DwDir` | — | Not used directly. `PVM_DwGradVec` is the authoritative source because it includes the b=0 volume and reflects the actual gradient amplitudes. |
| `PVM_DwGradDur` | `acquisition.gradient_pulse_duration` | Convert ms → s (divide by 1000) |
| `PVM_DwGradSep` | `acquisition.gradient_pulse_separation` | Convert ms → s (divide by 1000) |
| `PVM_SPackArrGradOrient` | `measurement_frame` | See coordinate frame discussion below |

#### Gradient normalization

`PVM_DwGradVec` contains gradient vectors for all volumes (including b=0). These vectors are **not unit-normalized** — their magnitudes are proportional to the applied gradient amplitude relative to the maximum.

- **With explicit `b_values`:** Normalize `PVM_DwGradVec` to unit length and store in `gradients`. Use `PVM_DwEffBval` for the per-axis `b_values` array. The gradient vectors serve as pure directions.
- **With implicit normalization:** Store `PVM_DwGradVec` magnitudes as-is in `gradients`. The magnitude ratios encode relative b-value weighting per the convention in §5. Use `max(PVM_DwBvalEach)` as the top-level `b_value`.

#### Coordinate frame

Bruker `PVM_DwGradVec` values are in the **gradient hardware frame** — they represent waveform amplitudes on the physical X, Y, Z gradient coils. To transform them to the subject (anatomical) coordinate system, they must be multiplied by the transpose of `PVM_SPackArrGradOrient`:

```python
import numpy as np

grad_vec = get_array(method["PVM_DwGradVec"])       # (N, 3)
orientation = get_array(method["PVM_SPackArrGradOrient"])[0]  # (3, 3)

# Transform to subject coordinates
grad_subject = np.einsum("ij,kj->ki", orientation.T, grad_vec)
```

When converting to duckn, there are two options:

1. **Apply the transform at conversion time** and set `gradient_frame: "world"`. Store the resulting subject-space gradient directions in `gradients`. This is the preferred approach.

2. **Store the raw gradient amplitudes** and set the convention's `measurement_frame` to the transpose of `PVM_SPackArrGradOrient`, with `gradient_frame: "measurement"` (or omit it, since `"measurement"` is the default). This preserves the original Bruker representation for lossless round-trip.

#### Effective vs. nominal b-values

Bruker's `PVM_DwEffBval` includes the diffusion weighting contribution of the imaging gradients, which can be non-negligible at high spatial resolution or low b-values. This makes the effective b-values slightly different from the nominal `PVM_DwBvalEach`. For accurate quantitative modeling, use `PVM_DwEffBval` in the per-axis `b_values` field. For compatibility with tools that expect "clean" shell values, use `PVM_DwBvalEach` in the top-level `b_value` and rely on implicit normalization.

#### Bruker Enhanced DICOM

When Bruker data is exported to Enhanced DICOM, diffusion encoding is stored as B-matrices in the standard DICOM B-value component tags (0018,9602–9607) rather than as gradient direction vectors. This maps directly to the `b_matrices` per-axis field. However, as noted in §7.2, the B-matrix loses gradient polarity. Bruker's Enhanced DICOM export may also store gradient directions in Bruker private tags (group 0019), which preserve polarity. When available, these private tags should be preferred over reconstructing directions from the B-matrix. If the original Bruker `method` file is accessible, extracting `PVM_DwGradVec` and converting to `gradients` remains the most reliable approach.

#### Example: Preclinical DTI

A mouse brain DTI acquisition on a Bruker 9.4T with 30 directions, b=1000, δ=3.5 ms, Δ=8.5 ms:

```json
"extensions": {
  "dwmri": {
    "version": "1.0",
    "b_value": 1000,
    "b_value_units": "s/mm²",
    "gradient_frame": "world",
    "acquisition": {
      "echo_time": 0.05687,
      "repetition_time": 0.5,
      "gradient_pulse_duration": 0.0035,
      "gradient_pulse_separation": 0.0085
    }
  }
}
```

---

## 8. Consistency Rules

- The `"dwmri"` extension must be declared at the top level of `"extensions"` with at least a `"version"` and `"b_value"`.
- Exactly one axis must have `kind: "list"` (or `"vector"`) — this is the DWI axis.
- The per-axis extension must contain exactly one of `gradients` or `b_matrices`, not both.
- The length of `gradients` (or `b_matrices`) must equal the size of the DWI axis (`shape[k]` where axis `k` is the `"list"` axis).
- If `b_values` is present, its length must also equal the DWI axis size.
- All gradient vectors must have exactly 3 components.
- All B-matrix entries must have exactly 6 components, in upper-triangle order `[Bxx, Bxy, Bxz, Byy, Byz, Bzz]`.
- If `nex` is present, for each entry `"N": M`, indices N through N+M-1 must be valid DWI axis positions, and the gradient/B-matrix values at those positions should be identical.
- `measurement_frame`, if present, must be a square matrix with side length equal to the space dimension (typically 3×3).
- If `gradient_frame` is present, it must be one of `"measurement"`, `"world"`, or `"image"`.
- If `gradient_frame` is `"measurement"` (or absent), `measurement_frame` should be present in the convention fields. If `gradient_frame` is `"image"`, the spatial axes must have `space_direction` values sufficient to construct the IJK-to-world rotation.
- If `acquisition` is present, all time values (`total_readout_time`, `effective_echo_spacing`, `echo_time`, `repetition_time`, `gradient_pulse_duration`, `gradient_pulse_separation`, and entries in `slice_timing`) must be in seconds. `phase_encoding_direction` must be one of `"i"`, `"j"`, `"k"`, `"i-"`, `"j-"`, `"k-"`.
- If `gradient_pulse_duration` is present, it must be a positive number in seconds.
- If `gradient_pulse_separation` is present, it must be a positive number in seconds, and must be greater than or equal to `gradient_pulse_duration` (since Δ ≥ δ for a physically realizable PGSE sequence).

---

## 9. Examples

### 9.1 Single-Shell DWI with Gradients

A 128×128×60 volume with 13 DWI acquisitions (1 baseline + 12 gradient directions) at b=1000 s/mm², volume-interleaved. This corresponds to the NA-MIC convention's multi-b-value example.

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [128, 128, 60, 13],
  "data_type": "int16",
  "dimension_names": ["i", "j", "k", "diffusion"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 20, 13] }
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
      "space_origin": [-128.0, -142.2, 99.7],
      "measurement_frame": [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
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
          "thickness": 2.2,
          "space_direction": [0, 0, -2.2],
          "unit": "mm"
        },
        {
          "kind": "list",
          "extensions": {
            "dwmri": {
              "gradients": [
                [0, 0, 0],
                [0.707107, 0.0, 0.707107],
                [-0.707107, 0.0, 0.707107],
                [0.0, 0.707107, 0.707107],
                [0.0, 0.707107, -0.707107],
                [0.707107, 0.707107, 0.0],
                [-0.707107, 0.707107, 0.0],
                [1, 0, 1],
                [-1, 0, 1],
                [0, 1, 1],
                [0, 1, -1],
                [1, 1, 0],
                [-1, 1, 0]
              ]
            }
          }
        }
      ],
      "extensions": {
        "dwmri": {
          "version": "1.0",
          "b_value": 1000,
          "b_value_units": "s/mm²"
        }
      }
    }
  }
}
```

Note the implicit normalization: gradients 1–6 have magnitude ≈1.0 (b≈500 after normalization against the maximum), while gradients 7–12 have magnitude ≈1.414 (the maximum, so b=1000). This encodes a dual-shell acquisition (b=500 and b=1000) using the single nominal b-value convention.

### 9.2 Multi-Shell with Explicit b-Values

The same dataset as §9.1, but using explicit per-volume b-values for clarity:

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [128, 128, 60, 13],
  "data_type": "int16",
  "dimension_names": ["i", "j", "k", "diffusion"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 20, 13] }
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
      "space_origin": [-128.0, -142.2, 99.7],
      "measurement_frame": [[-1, 0, 0], [0, 1, 0], [0, 0, 1]],
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
          "thickness": 2.2,
          "space_direction": [0, 0, -2.2],
          "unit": "mm"
        },
        {
          "kind": "list",
          "extensions": {
            "dwmri": {
              "b_values": [0, 500, 500, 500, 500, 500, 500, 1000, 1000, 1000, 1000, 1000, 1000],
              "gradients": [
                [0, 0, 0],
                [0.707107, 0.0, 0.707107],
                [-0.707107, 0.0, 0.707107],
                [0.0, 0.707107, 0.707107],
                [0.0, 0.707107, -0.707107],
                [0.707107, 0.707107, 0.0],
                [-0.707107, 0.707107, 0.0],
                [0.707107, 0.0, 0.707107],
                [-0.707107, 0.0, 0.707107],
                [0.0, 0.707107, 0.707107],
                [0.0, 0.707107, -0.707107],
                [0.707107, 0.707107, 0.0],
                [-0.707107, 0.707107, 0.0]
              ]
            }
          }
        }
      ],
      "extensions": {
        "dwmri": {
          "version": "1.0",
          "b_value": 1000,
          "b_value_units": "s/mm²"
        }
      }
    }
  }
}
```

With explicit `b_values`, the gradient vectors are pure directions (here all ≈ unit length). The b-value information is carried entirely in the `b_values` array.

### 9.3 Dartmouth DWI with NEX and RAS Space

A 256×256×36 volume with 14 acquisitions (2 baselines via NEX + 12 gradient directions) at b=800, in RAS space. This corresponds to the Dartmouth example from the NA-MIC specification.

```json
{
  "zarr_format": 3,
  "node_type": "array",
  "shape": [256, 256, 36, 14],
  "data_type": "int16",
  "dimension_names": ["i", "j", "k", "diffusion"],
  "chunk_grid": {
    "name": "regular",
    "configuration": { "chunk_shape": [64, 64, 18, 14] }
  },
  "codecs": [
    { "name": "bytes", "configuration": { "endian": "little" } },
    { "name": "zstd", "configuration": { "level": 3 } }
  ],
  "fill_value": 0,
  "attributes": {
    "duckn": {
      "version": "1.0",
      "space": "right-anterior-superior",
      "space_origin": [125.0, 124.1, 79.3],
      "measurement_frame": [[0, -1, 0], [1, 0, 0], [0, 0, -1]],
      "axes": [
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [-0.9375, 0, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "space_direction": [0, -0.9375, 0],
          "unit": "mm"
        },
        {
          "kind": "space",
          "centering": "cell",
          "thickness": 3.0,
          "space_direction": [0, 0, -3],
          "unit": "mm"
        },
        {
          "kind": "list",
          "extensions": {
            "dwmri": {
              "gradients": [
                [0, 0, 0],
                [0, 0, 0],
                [-0.8238094, -0.4178235, -0.3830949],
                [-0.5681645, 0.5019867, -0.6520725],
                [0.4296590, 0.1437401, 0.8914774],
                [-0.0482123, 0.6979894, 0.7144833],
                [0.8286872, -0.0896669, -0.5524829],
                [0.9642489, -0.2240180, 0.1415627],
                [-0.1944068, 0.9526976, -0.2336092],
                [0.1662157, 0.6172332, -0.7690224],
                [-0.3535898, -0.9178798, -0.1801968],
                [-0.7404186, -0.5774342, 0.3440203],
                [-0.2763061, 0.0476582, 0.9598873],
                [0.6168819, -0.7348858, -0.2817793]
              ],
              "nex": {
                "0": 2
              }
            }
          }
        }
      ],
      "extensions": {
        "dwmri": {
          "version": "1.0",
          "b_value": 800
        }
      }
    }
  }
}
```

The `nex: {"0": 2}` indicates that indices 0 and 1 are repeated baseline acquisitions. Both have gradient `[0, 0, 0]` in the `gradients` array.

### 9.4 Full B-Matrices

A DWI volume where the full B-matrix (including imaging gradient contributions) is known:

```json
"extensions": {
  "dwmri": {
    "version": "1.0",
    "b_value": 1000,
    "b_value_units": "s/mm²"
  }
}
```

Per-axis:

```json
{
  "kind": "list",
  "extensions": {
    "dwmri": {
      "b_matrices": [
        [0, 0, 0, 0, 0, 0],
        [0.502, 0.003, 0.498, 0.003, 0.002, 0.498],
        [0.502, -0.003, -0.498, 0.003, -0.002, 0.498],
        [0.003, 0.002, 0.003, 0.502, 0.498, 0.498],
        [0.003, -0.002, 0.003, 0.502, -0.498, 0.498],
        [0.502, 0.498, 0.003, 0.498, 0.002, 0.003],
        [0.502, -0.498, 0.003, 0.498, -0.002, 0.003]
      ]
    }
  }
}
```

The small off-unity values (e.g., 0.502 instead of 0.5) reflect the imaging gradient contribution. The B-matrix entries are in upper-triangle order `[Bxx, Bxy, Bxz, Byy, Byz, Bzz]`, with off-diagonals *not* pre-multiplied by 2.

### 9.5 Slice-Interleaved DWI

A DWI volume where the diffusion axis is between the two in-plane spatial axes and the slice axis, resulting in slice interleaving:

```json
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
    "kind": "list",
    "extensions": {
      "dwmri": {
        "gradients": [
          [0, 0, 0],
          [1, 0, 0],
          [0, 1, 0],
          [0, 0, 1]
        ]
      }
    }
  },
  {
    "kind": "space",
    "centering": "cell",
    "thickness": 3.0,
    "space_direction": [0, 0, 3],
    "unit": "mm"
  }
]
```

The DWI axis at position 2 (between the second spatial axis and the slice axis) means the data is slice-interleaved. A reader that does not understand the `dwmri` extension still sees a 4D array with the correct spatial embedding on three of the four axes.

### 9.6 Minimal

A DWI dataset with the smallest useful metadata:

```json
"extensions": {
  "dwmri": {
    "version": "1.0",
    "b_value": 1000
  }
}
```

Per-axis:

```json
{
  "kind": "list",
  "extensions": {
    "dwmri": {
      "gradients": [
        [0, 0, 0],
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
        [0.707, 0.707, 0],
        [0.707, 0, 0.707],
        [0, 0.707, 0.707]
      ]
    }
  }
}
```

### 9.7 BIDS-Converted Multi-Shell with Acquisition Metadata

A dataset converted from BIDS format, demonstrating the `gradient_frame`, `b_values`, and `acquisition` fields:

Top-level:

```json
"extensions": {
  "dwmri": {
    "version": "1.0",
    "b_value": 2000,
    "b_value_units": "s/mm²",
    "gradient_frame": "world",
    "acquisition": {
      "phase_encoding_direction": "j-",
      "total_readout_time": 0.0512,
      "effective_echo_spacing": 0.00058,
      "echo_time": 0.089,
      "repetition_time": 3.6,
      "multiband_acceleration_factor": 3,
      "parallel_reduction_factor_in_plane": 1,
      "slice_timing": [0.0, 0.6, 1.2, 1.8, 0.0, 0.6, 1.2, 1.8]
    }
  }
}
```

Per-axis:

```json
{
  "kind": "list",
  "extensions": {
    "dwmri": {
      "b_values": [0, 1000, 1000, 1000, 2000, 2000, 2000],
      "gradients": [
        [0, 0, 0],
        [0.707, 0.0, 0.707],
        [-0.707, 0.0, 0.707],
        [0.0, 1.0, 0.0],
        [0.577, 0.577, 0.577],
        [-0.577, 0.577, 0.577],
        [0.577, -0.577, 0.577]
      ]
    }
  }
}
```

Here `gradient_frame: "world"` indicates the gradient directions have been transformed from FSL bvec image space into world coordinates at conversion time. The `acquisition` object preserves the BIDS JSON sidecar fields needed for susceptibility distortion correction (`phase_encoding_direction`, `total_readout_time`) and slice timing correction (`slice_timing`).

---

## 10. Design Notes

**Why `gradients` is an array, not indexed keys.** The NRRD key/value convention uses zero-padded keys (`DWMRI_gradient_0000`, `DWMRI_gradient_0001`, ...) because NRRD key/value pairs are unstructured strings. JSON has arrays. An array is the natural representation: it preserves ordering, eliminates parsing of index strings, and makes the length obvious. The array index *is* the DWI axis position.

**Why `b_matrices` stores 6 components, not 9.** The B-matrix is symmetric ($\mathbf{B} = \mathbf{B}^T$), so only the upper triangle needs to be stored. This matches the NRRD convention's `DWMRI_B-matrix_NNNN` format and the `3D-symmetric-matrix` kind's component ordering: `[Bxx, Bxy, Bxz, Byy, Byz, Bzz]`.

**Why both implicit normalization and explicit `b_values` are supported.** The implicit normalization convention (gradient magnitude encodes relative b-value) is deeply embedded in the NA-MIC ecosystem. Tools like 3D Slicer, Teem, and ITK readers expect it. Dropping it would break round-trip compatibility. However, for modern multi-shell acquisitions (e.g., HCP-style with b=1000, 2000, 3000), explicit per-volume b-values are far clearer. Supporting both lets converters preserve the original encoding while giving new writers a more transparent option.

**Why `nex` is a hint, not a compact encoding.** In the NRRD convention, `DWMRI_NEX_0000:=2` elides the next gradient key — you skip `DWMRI_gradient_0001` because it's implied to be the same as `0000`. This is a compact encoding trick that saves key/value pairs but makes parsing stateful and error-prone. In this extension, every DWI axis position has an explicit entry in `gradients` or `b_matrices`. The `nex` field is metadata *about* the acquisition (how many repeats were taken), not a mechanism for omitting data. This makes the extension simpler to read and harder to get wrong.

**Why `measurement_frame` is a convention field, not an extension field.** The measurement frame applies to any array with vector or tensor content — not just DWI. Diffusion tensors, velocity fields, and other vector quantities all need it. Making it a convention-level field (which the NRRD format already does) avoids duplicating the concept in every extension that deals with non-scalar data. This extension documents the dependency but does not redefine the field.

**Why `"list"` and `"vector"` are both accepted.** The NRRD convention uses `kind: "list"` for the DWI axis. However, ITK's NRRD writer historically emits `kind: "vector"` for non-scalar data. Both identify a non-spatial, non-resamplable axis. Requiring only `"list"` would reject ITK-generated files that are otherwise valid. Accepting both follows the NA-MIC convention's pragmatic compatibility stance.

**Relationship to the convention's diffusion tensor example.** The convention (§6.3) shows a diffusion tensor volume with `kind: "3D-symmetric-matrix"`, `intent: "diffusion-tensor"`, `sample_units: "mm²/s"`, and `measurement_frame`. That is the *output* of tensor estimation. This extension describes the *input*: the raw DWI signal volumes from which tensors are computed. The two are complementary and may coexist in the same Zarr group (one array for the DWI, another for the estimated tensors).

**Why `gradient_frame` exists.** The gradient coordinate frame is the single largest source of errors in DWI processing. NRRD uses an explicit measurement frame (gradients in a named frame, transformed to world space by a matrix). FSL bvecs are in image-voxel space and must be rotated with the image. MRtrix stores gradients in scanner/world space directly. DICOM standard tags use patient (LPS) space, but vendor-specific tags may use image space (GE) or scanner space (Siemens). When data is converted between formats, the coordinate frame of the gradients can be silently reinterpreted, causing incorrect tensor orientations. The `gradient_frame` field makes this explicit. The default `"measurement"` matches the NRRD convention. Writers converting from other formats can set it to `"image"` or `"world"` to preserve the original representation without risk of incorrect transformation.

**Why `acquisition` metadata is in the extension, not the convention.** Parameters like `phase_encoding_direction` and `total_readout_time` are MRI acquisition details, not general raster data properties. They are essential for DWI preprocessing (susceptibility distortion correction via FSL `topup`, eddy current correction via `eddy`) but are meaningless for non-MRI data. Placing them in the `dwmri` extension keeps the convention clean while providing a structured home for these values. This is analogous to how BIDS provides a JSON sidecar for parameters that NIfTI cannot represent.

**Why DICOM B-matrix component tags are mapped to `b_matrices`.** DICOM Supplement 49 defines per-component B-value tags (0018,9602–9607) that together form the upper triangle of $b \cdot \mathbf{B}$. These map directly to the `b_matrices` field. However, as noted in the NA-MIC documentation, the B-matrix representation loses the sign of the gradient direction (since $\mathbf{B} = \mathbf{g}\mathbf{g}^T$ is positive semi-definite). When both gradient direction and B-matrix are available from DICOM, preserving both in `gradients` and the per-volume `b_values` is preferred over using `b_matrices` alone, unless the full B-matrix (including imaging gradient contributions) is specifically needed.

**Why `gradient_pulse_duration` and `gradient_pulse_separation` are in `acquisition`.** These are physical pulse sequence timing parameters, not diffusion encoding descriptors. They describe *how* the b-value was achieved, not *what* the b-value is. Two acquisitions with identical b-values, gradient directions, and B-matrices but different δ/Δ are indistinguishable for standard DTI but produce different signal in tissue with restricted diffusion (small pores, axons). The parameters are essential for advanced diffusion models (NODDI, CHARMED, AxCaliber, temporal diffusion spectroscopy) and for preclinical work where δ and Δ are routinely varied as experimental variables. Bruker ParaVision exposes these parameters directly in the `method` file (`PVM_DwGradDur`, `PVM_DwGradSep`); on clinical scanners they are sometimes available in vendor-specific DICOM headers or CSA fields but are not part of the DICOM standard or BIDS core specification.

**Why `gradient_pulse_duration` and `gradient_pulse_separation` are scalar, not per-volume.** In standard PGSE sequences, δ and Δ are constant across all diffusion-weighted volumes in a series — only the gradient direction and amplitude vary. Sequences with per-volume timing variation (e.g., temporal diffusion spectroscopy with varying Δ) are rare and would require a different data model (per-volume arrays, analogous to `b_values`). The current scalar representation covers the vast majority of preclinical and clinical DWI acquisitions. A future version of the extension could add per-volume arrays if the need arises.

**Why all `acquisition` time values use seconds without per-field unit tags.** The `acquisition` object follows the same convention as BIDS: all time values are in SI seconds. This is stated once in §4.1 and applies uniformly to `echo_time`, `repetition_time`, `total_readout_time`, `effective_echo_spacing`, `slice_timing`, `gradient_pulse_duration`, and `gradient_pulse_separation`. Adding per-field unit tags (e.g., `gradient_pulse_duration_units`) would be inconsistent with the existing fields and redundant with the blanket rule. Converters from formats that use milliseconds (Bruker `PVM_DwGradDur`, `PVM_DwGradSep`) must divide by 1000.