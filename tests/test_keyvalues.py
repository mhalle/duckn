"""The converter's `keyvalues` object (keyvalues-extension.md).

Import writes the versioned form, ``{"version": "1.0", "entries": {...}}``,
so a NRRD key named ``version`` cannot collide with the extension's own
field. Export reads that form and the unversioned one the released converter
wrote (§6), reporting a legacy object whose pairs look like the new fields.
"""

from __future__ import annotations

import numpy as np
import pytest

from duckn.convert import keyvalue_entries, nrrd_to_zarr, zarr_to_nrrd
from duckn.zarr_io import get_zarr_attrs

nrrd = pytest.importorskip("nrrd")

HEADER = {
    "space": "left-posterior-superior",
    "space directions": np.eye(3),
    "space origin": np.zeros(3),
    "kinds": ["domain"] * 3,
}


def _nrrd(tmp_path, pairs):
    path = tmp_path / "in.nrrd"
    nrrd.write(str(path), np.zeros((2, 2, 2), dtype=np.int16), {**HEADER, **pairs})
    return path


def _zarr_with(tmp_path, keyvalues):
    import zarr

    path = tmp_path / "in.zarr"
    arr = zarr.create_array(store=str(path), shape=(2, 2, 2), dtype="int16",
                            chunks=(2, 2, 2), zarr_format=3)
    arr[:] = 0
    arr.attrs["duckn"] = {
        "version": "1.1",
        "space": "left-posterior-superior",
        "space_origin": [0.0, 0.0, 0.0],
        "axes": [{"kind": "space", "space_direction": v}
                 for v in ([0, 0, 1.0], [0, 1.0, 0], [1.0, 0, 0])],
        "extensions": {"keyvalues": keyvalues},
    }
    return path


class TestImport:
    def test_unclaimed_pairs_are_written_versioned(self, tmp_path):
        nrrd_to_zarr(_nrrd(tmp_path, {"Scanner": "unit-3", "version": "7"}), tmp_path / "o.zarr")
        kv = get_zarr_attrs(tmp_path / "o.zarr")["duckn"]["extensions"]["keyvalues"]
        assert kv == {"version": "1.0", "entries": {"Scanner": "unit-3", "version": "7"}}

    def test_no_unclaimed_pairs_writes_no_extension(self, tmp_path):
        nrrd_to_zarr(_nrrd(tmp_path, {}), tmp_path / "o.zarr")
        exts = get_zarr_attrs(tmp_path / "o.zarr")["duckn"].get("extensions") or {}
        assert "keyvalues" not in exts


class TestExport:
    def test_round_trip_keeps_every_pair_including_one_named_version(self, tmp_path):
        pairs = {"Scanner": "unit-3", "version": "7", "support_offset": "6.6 6.3 7.6"}
        nrrd_to_zarr(_nrrd(tmp_path, pairs), tmp_path / "o.zarr")
        assert zarr_to_nrrd(tmp_path / "o.zarr", tmp_path / "o.nrrd") == []
        _, header = nrrd.read(str(tmp_path / "o.nrrd"))
        assert {k: header[k] for k in pairs} == pairs
        assert "entries" not in header

    def test_the_unversioned_form_still_exports(self, tmp_path):
        src = _zarr_with(tmp_path, {"Scanner": "unit-3"})
        assert zarr_to_nrrd(src, tmp_path / "o.nrrd") == []
        _, header = nrrd.read(str(tmp_path / "o.nrrd"))
        assert header["Scanner"] == "unit-3"

    def test_a_legacy_pair_named_version_exports_and_is_reported(self, tmp_path):
        src = _zarr_with(tmp_path, {"Scanner": "unit-3", "version": "7"})
        found = zarr_to_nrrd(src, tmp_path / "o.nrrd")
        assert [d.code for d in found] == ["keyvalues-legacy-ambiguous"]
        _, header = nrrd.read(str(tmp_path / "o.nrrd"))
        assert (header["Scanner"], header["version"]) == ("unit-3", "7")


class TestKeyvalueEntries:
    @pytest.mark.parametrize("ext, pairs", [
        ({"version": "1.0", "entries": {"a": "1"}}, {"a": "1"}),
        ({"a": "1", "b": 2}, {"a": "1", "b": "2"}),
        ({"version": "7", "entries": "x"}, {"version": "7", "entries": "x"}),
        ({"entries": "x"}, {"entries": "x"}),
        (None, {}),
    ])
    def test_reads_both_forms(self, ext, pairs):
        assert keyvalue_entries(ext) == pairs

    @pytest.mark.parametrize("ext, reported", [
        ({"version": "1.0", "entries": {"a": "1"}}, False),
        ({"a": "1"}, False),
        ({"version": "7"}, True),
        ({"entries": "x", "a": "1"}, True),
    ])
    def test_reports_only_a_legacy_object_that_looks_versioned(self, ext, reported):
        found = []
        keyvalue_entries(ext, diagnostics=found)
        assert bool(found) is reported
