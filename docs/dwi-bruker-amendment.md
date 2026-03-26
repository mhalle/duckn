# DWI Extension Amendment: Gradient Pulse Timing and Bruker Mapping

**Applies to:** `dwmri` extension v1.0
**Status:** Draft

---

## 1. New `acquisition` Fields: Gradient Pulse Timing

The following fields are added to the `acquisition` object (§4.1).

### 1.1 Updated `acquisition` Example

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

### 1.2 New Field Definitions

These rows are added to the `acquisition` field table:

| Field | Type | Description | BIDS equivalent | Bruker equivalent |
|---|---|---|---|---|
| `gradient_pulse_duration` | number | Duration of each diffusion-sensitizing gradient pulse (δ, "little delta") in seconds. For a standard pulsed gradient spin echo (PGSE) sequence, this is the time each trapezoidal gradient lobe is applied. | — | `PVM_DwGradDur` (ms → s) |
| `gradient_pulse_separation` | number | Time between the leading edges of the two diffusion-sensitizing gradient pulses (Δ, "big delta") in seconds. For PGSE, this is the interval from the onset of the first gradient pulse to the onset of the second. | — | `PVM_DwGradSep` (ms → s) |

Both fields are optional. They follow the existing convention that all time values in `acquisition` are in seconds (consistent with `echo_time`, `repetition_time`, `total_readout_time`, `effective_echo_spacing`, and `slice_timing`). They are most commonly available from Bruker preclinical scanners, where researchers routinely vary δ and Δ as experimental parameters. They may also be extractable from Siemens CSA headers or DICOM Enhanced MR Diffusion Macro attributes on some clinical platforms.

### 1.3 Relationship to b-value

For a PGSE sequence with rectangular gradient pulses, the b-value is determined by:

$b = γ^2 G^2 δ^2 (Δ - δ/3)$

where $γ$ is the gyromagnetic ratio and $G$ is the gradient amplitude. When `gradient_pulse_duration` and `gradient_pulse_separation` are present alongside `b_value`, a reader can recover the gradient amplitude $G$ — or verify consistency between the reported b-value and the timing parameters.

**Pulse shape assumption.** The formula above assumes ideal rectangular gradient pulses. Real implementations use trapezoidal pulses with finite ramp times, and some sequences use sinusoidal or half-sine lobes. The stored `gradient_pulse_duration` and `gradient_pulse_separation` are the *programmed* sequence parameters (the flat-top duration and the center-to-center separation for trapezoidal pulses, respectively), not effective values corrected for ramp shape. For trapezoidal pulses, the effective δ is shorter than the programmed value by approximately one-third of the ramp time. Scanners that report `PVM_DwEffBval` (Bruker) or compute b-values internally already account for this — the `b_value` field remains authoritative. A future version of this extension may add a `gradient_pulse_shape` field (`"rectangular"`, `"trapezoidal"`, `"sinusoidal"`) and ramp time parameters for applications that need exact pulse shape modeling.

These fields are informational. The `b_value` field (or per-volume `b_values`) remains authoritative for the diffusion weighting applied. The timing parameters are needed for:

- **Quantitative diffusion models** beyond DTI (e.g., CHARMED, NODDI, axon diameter estimation) that model restricted diffusion as a function of δ and Δ independently
- **Preclinical protocol reporting** where δ and Δ are standard parameters to document
- **Cross-scanner reproducibility** where matching b-value alone is insufficient — two acquisitions with the same b-value but different δ/Δ probe different diffusion time scales

### 1.4 Updated Consistency Rules

The following rules are added to §8:

- If `gradient_pulse_duration` is present, it must be a positive number in seconds.
- If `gradient_pulse_separation` is present, it must be a positive number in seconds, and must be greater than or equal to `gradient_pulse_duration` (since Δ ≥ δ for a physically realizable PGSE sequence).
- Both fields follow the existing convention that all time values in `acquisition` are in seconds.

---

## 2. Bruker Format Mapping (§7.6)

This section is added after §7.5 (Mapping from BIDS).

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

## 3. Updated BIDS Mapping Table (§7.5)

Two rows are added to the BIDS mapping table. While BIDS does not currently define standard sidecar fields for δ and Δ, some BIDS datasets include them as additional JSON sidecar entries:

| BIDS JSON field | duckn field |
|---|---|
| *(all existing rows unchanged)* | |
| `DiffusionPulseDuration` (non-standard) | `acquisition.gradient_pulse_duration` |
| `DiffusionPulseSeparation` (non-standard) | `acquisition.gradient_pulse_separation` |

---

## 4. Design Notes

**Why `gradient_pulse_duration` and `gradient_pulse_separation` are in `acquisition`.** These are physical pulse sequence timing parameters, not diffusion encoding descriptors. They describe *how* the b-value was achieved, not *what* the b-value is. Two acquisitions with identical b-values, gradient directions, and B-matrices but different δ/Δ are indistinguishable for standard DTI but produce different signal in tissue with restricted diffusion (small pores, axons). The parameters are essential for advanced diffusion models (NODDI, CHARMED, AxCaliber, temporal diffusion spectroscopy) and for preclinical work where δ and Δ are routinely varied as experimental variables. Bruker ParaVision exposes these parameters directly in the `method` file (`PVM_DwGradDur`, `PVM_DwGradSep`); on clinical scanners they are sometimes available in vendor-specific DICOM headers or CSA fields but are not part of the DICOM standard or BIDS core specification.

**Why these fields are scalar, not per-volume.** In standard PGSE sequences, δ and Δ are constant across all diffusion-weighted volumes in a series — only the gradient direction and amplitude vary. Sequences with per-volume timing variation (e.g., temporal diffusion spectroscopy with varying Δ) are rare and would require a different data model (per-volume arrays, analogous to `b_values`). The current scalar representation covers the vast majority of preclinical and clinical DWI acquisitions. A future version of the extension could add per-volume arrays if the need arises.

**Why all `acquisition` time values use seconds without per-field unit tags.** The `acquisition` object follows the same convention as BIDS: all time values are in SI seconds. This is stated once in §4.1 and applies uniformly to `echo_time`, `repetition_time`, `total_readout_time`, `effective_echo_spacing`, `slice_timing`, `gradient_pulse_duration`, and `gradient_pulse_separation`. Adding per-field unit tags (e.g., `gradient_pulse_duration_units`) would be inconsistent with the existing fields and redundant with the blanket rule. Converters from formats that use milliseconds (Bruker `PVM_DwGradDur`, `PVM_DwGradSep`) must divide by 1000.
