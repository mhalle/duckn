"""Structural checks on the specification documents.

Documentation drifts from code silently — a stale field or a dangling
cross-reference misleads an implementer without failing anything. These
checks are the ones that actually caught drift in practice:

- every model field is documented (found `unit_systems`/`space_transforms`
  missing from duckn-spec while two companion specs defined them)
- every section cross-reference resolves (found several after a renumber)
- every JSON example parses, and seg extensions validate against the models

They are deliberately structural. Nothing here checks prose.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from duckn.models import AxisMetadata, DucknMetadata

DOCS = Path(__file__).parent.parent / "docs"

# Non-recursive by design: docs/archive/ holds superseded plans and decision
# records, which are non-normative and deliberately left as they were found.
SPEC_FILES = sorted(DOCS.glob("*.md"))


def _headings(text: str, level: str) -> set[str]:
    return set(re.findall(rf"^{level} `?([\w.]+)`?", text, re.M))


class TestConventionFieldsAreDocumented:
    """duckn-spec must document the full top-level key set it claims to own."""

    def _spec(self) -> str:
        return (DOCS / "duckn-spec.md").read_text()

    def test_top_level_fields_documented(self):
        spec = self._spec()
        documented = _headings(spec, "####")
        # `axes` has its own subsection rather than a #### entry.
        documented |= {"axes"}
        missing = sorted(set(DucknMetadata.model_fields) - documented)
        assert not missing, (
            f"top-level fields implemented but undocumented in duckn-spec: {missing}"
        )

    def test_per_axis_fields_documented(self):
        documented = _headings(self._spec(), "####")
        missing = sorted(set(AxisMetadata.model_fields) - documented)
        assert not missing, (
            f"per-axis fields implemented but undocumented in duckn-spec: {missing}"
        )

    def test_no_documented_field_is_unimplemented(self):
        """The reverse drift: a spec field the model never grew."""
        spec = self._spec()
        # Only check the field-definition sections, not extension examples.
        section = spec[spec.index("### 3.1"):spec.index("### 3.2")]
        documented = _headings(section, "####")
        known = set(DucknMetadata.model_fields)
        unimplemented = sorted(documented - known)
        assert not unimplemented, (
            f"documented in duckn-spec §3.1 but absent from DucknMetadata: "
            f"{unimplemented}"
        )


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
class TestCrossReferencesResolve:
    def test_section_references_exist(self, path):
        text = path.read_text()
        tops = {m.group(1) for m in re.finditer(r"^## (\d+)\.", text, re.M)}
        subs = {m.group(1) for m in re.finditer(r"^### (\d+\.\d+)", text, re.M)}
        if not tops:
            pytest.skip(f"{path.name} is not a numbered specification")

        # A spec may cite a section of the duckn convention ("§3.2 of the
        # duckn convention"), so a reference that does not resolve locally
        # is checked against duckn-spec before being called dangling.
        convention = (DOCS / "duckn-spec.md").read_text()
        conv_tops = {m.group(1) for m in re.finditer(r"^## (\d+)\.", convention, re.M)}
        conv_subs = {m.group(1) for m in re.finditer(r"^### (\d+\.\d+)", convention, re.M)}

        dangling = set()
        for major, minor in re.findall(r"§(\d+)(?:\.(\d+))?", text):
            local = major in tops and (not minor or f"{major}.{minor}" in subs)
            remote = major in conv_tops and (
                not minor or f"{major}.{minor}" in conv_subs
            )
            if not local and not remote:
                dangling.add(f"§{major}.{minor}" if minor else f"§{major}")
        assert not dangling, f"{path.name} references missing sections: {sorted(dangling)}"

    def test_referenced_sibling_docs_exist(self, path):
        """A spec naming another spec must name one that is there."""
        text = path.read_text()
        named = set(re.findall(r"`?([a-z0-9-]+-(?:spec|extension|guide)\.md)`?", text))
        missing = sorted(n for n in named if not (DOCS / n).exists())
        assert not missing, f"{path.name} references missing documents: {missing}"


@pytest.mark.parametrize("path", SPEC_FILES, ids=lambda p: p.name)
class TestJsonExamplesParse:
    def test_json_blocks_are_valid(self, path):
        text = path.read_text()
        bad = []
        for i, block in enumerate(re.findall(r"```json\n(.*?)```", text, re.S), 1):
            stripped = block.strip()
            if "..." in stripped or "//" in stripped:
                continue  # illustrative fragment, not a document
            for candidate in (stripped, "{" + stripped + "}"):
                try:
                    json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue
            else:
                bad.append((i, stripped[:60]))
        assert not bad, f"{path.name} has unparseable json blocks: {bad}"


class TestDocumentedApiIsReachable:
    """Every call the README shows must work from a clean `import duckn`.

    `duckn.zarr_to_dicom` was documented in the quick start and not exposed
    on the package — found by smoke-testing a built wheel, not by the suite,
    because the tests import submodules directly.
    """

    def _readme(self) -> str:
        return (Path(__file__).parent.parent / "README.md").read_text()

    def test_quickstart_calls_exist(self):
        import duckn

        names = sorted(set(re.findall(r"\bduckn\.(\w+)\(", self._readme())))
        assert names, "no duckn.<call>() examples found in README"
        missing = [n for n in names if not hasattr(duckn, n)]
        assert not missing, (
            f"README documents duckn.{{{','.join(missing)}}} but the package "
            "does not expose them"
        )

    def test_converter_entry_points_are_exposed(self):
        """A converter you can't reach from `duckn.` is effectively private."""
        import duckn
        from duckn import convert, dicom_convert, nifti_convert

        expected = set()
        for mod in (convert, dicom_convert, nifti_convert):
            for name in dir(mod):
                if name.startswith("_"):
                    continue
                obj = getattr(mod, name)
                if not callable(obj):
                    continue
                if getattr(obj, "__module__", "").startswith("duckn") and (
                    "_to_zarr" in name or name.startswith("zarr_to_")
                ):
                    expected.add(name)

        missing = sorted(n for n in expected if not hasattr(duckn, n))
        assert not missing, f"converters not reachable as duckn.<name>: {missing}"
