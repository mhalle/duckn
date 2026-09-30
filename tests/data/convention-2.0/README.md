# Convention 2.0 fixtures

What the adversarial review of `docs/proposals/duckn-2.0.md` used (rounds 1-10, 2026-09-27 to
2026-09-30), kept for the 2.0 reader and writers. `tests/test_convention2_fixtures.py` holds the
files to each other now; the reader's tests will hold the reader to them.

| File | What it is |
|---|---|
| `scenarios.md` | S1-S26: a dataset's source facts, as a converter author has them. S1-S16 are rounds 1-3's; S17-S26 rounds 4-10's. |
| `questionnaire.md` | Q1-Q16: what a reader must recover from a header. |
| `truth.py`, `truth.json` | What a reader must recover, computed from the scenarios' facts alone, never from a header (positions, extents, values, times). Regenerate with `python truth.py`. |
| `headers/S17.json` … `S26.json` | Reference 2.0 headers under revision 14. Two writers in rounds 4-8 wrote the same core headers independently; these are those, updated to revision 14 (`dicom` 2.0, `provenance` `sources`, S20's untyped `domain` axes and `nrrd` `no_space`, S21 without an unstated unit and, since 2026-09-30, without a centering on its volumes (nifti 2.0 §2: a NIfTI volume has a time, not an extent or an instant within one), S23's axis id `wavelength`). S25 equals S18: the draft now requires a materializing converter to restate padding. |
| `faults/` | 2.0 files with a planted fault, and the verdict below. |
| `legacy/` | 1.x files for the §14 mapping, and the verdict below. |

S1-S16 have no reference headers in 2.0's vocabulary yet: rounds 1-3 wrote them against
revisions 2-4, whose field names 2.0 changed. The 2.0 writers will produce them.

## Verdicts

A reader keeps validity (§10) and conformance (§9, §6's encodings, §8) apart.

| File | Valid | Conformant | What a reader must say |
|---|---|---|---|
| `F1-missing-through-linear-resample` | yes | no (§8) | `values.missing` kept through a linear resample: the blended edge samples are unmarked. A writer drops it. |
| `F2-axis-linear-one-short` | yes | no (§6) | `axis_linear` has 2 slopes for a dimension of 3 samples: malformed parameters, so the quantity is unknown; stored values stay available and are never shown in Bq/mL. Index 2 is at 120 s (the frame's middle, [90, 150] s), 107.5 s in `stimulus` time, which is local to this array. |
| `F3-stored-padding-beside-hounsfield` | yes | no (§9) | `PixelPaddingValue` -2000 in stored units beside an array of Hounsfield units (`stored_values: false`, `transforms: []`) contradicts what the file alone says. `values.missing` [-3024] is the statement to keep. |
| `L1-version-1.2` | yes | - | Read under 1.2: `value_transforms` absent means not stated, so `values.transforms` is absent and stored values are never shown in HU. |
| `L2-version-1.1` | yes | - | The same header under 1.1: absent `value_transforms` means identity, so `values.transforms: []` and a stored 1000 is 1000 HU. |
| `L3-dwi-1.0-malformed` | yes (core) | - | The 1.0 `measurement_frame` is by columns (§14), and governs nothing here (no spatial components; `gradient_frame` `world`, so the gradients are in world axes): reported as not carried. The `dwmri` block breaks dwi 1.0 (per-axis fields at the top level, no `b_value`), so it is ignored and reported (§2.3). |
| `L4-no-version-with-1.1-field` | yes (as 1.0) | - | No `version` and none of 2.0's fields, so a 1.0 file (§2.1, §14): `sample_units` HU with identity, a stored 1000 is 1000 HU. Its `space_transforms` is a 1.1 field with a string `from`, which the transform specification requires as an object: read as 1.x says and reported, not repaired. |
