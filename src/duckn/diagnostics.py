"""Diagnostics: what a reader, importer, migration, or exporter reports.

To *report* is to hand the caller a diagnostic alongside the result, and
not to fail (seg extension spec, §5). A diagnostic carries a code from the
spec's §10, a severity, and the thing it is about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class About:
    """The thing a diagnostic is about. Build one with the classmethods."""

    kind: Literal[
        "extension", "segment", "value", "layer", "scheme", "reference", "omitted"
    ]
    key: tuple = ()

    @classmethod
    def extension(cls) -> About:
        return cls("extension")

    @classmethod
    def segment(cls, segment_id: str, index: int | None = None) -> About:
        """A segment, by id. `index` is its position in `segments`, given
        where the id alone does not identify it (a repeated id)."""
        return cls("segment", (segment_id,) if index is None else (segment_id, index))

    @classmethod
    def value(cls, layer: int, value: int) -> About:
        return cls("value", (layer, value))

    @classmethod
    def layer(cls, layer: int) -> About:
        return cls("layer", (layer,))

    @classmethod
    def scheme(cls, key: str) -> About:
        return cls("scheme", (key,))

    @classmethod
    def reference(cls, ref: str) -> About:
        return cls("reference", (ref,))

    @classmethod
    def omitted(cls, original_id: str) -> About:
        """A segment of the source file that a migration omitted."""
        return cls("omitted", (original_id,))

    def __str__(self) -> str:
        if self.kind == "extension":
            return "extension"
        if self.kind == "value":
            return f"layer {self.key[0]}, value {self.key[1]}"
        return f"{self.kind} {' #'.join(str(k) for k in self.key)}"


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: Severity
    about: About
    message: str = ""

    def __str__(self) -> str:
        text = f"{self.severity} {self.code} ({self.about})"
        return f"{text}: {self.message}" if self.message else text


class DiagnosticsError(ValueError):
    """Raised by a strict mode that turns reports into failures."""

    def __init__(self, diagnostics: Iterable[Diagnostic]):
        self.diagnostics = list(diagnostics)
        super().__init__("\n".join(str(d) for d in self.diagnostics))


def raise_on(
    diagnostics: Iterable[Diagnostic], *, warnings: bool = False
) -> None:
    """Raise DiagnosticsError if any diagnostic is an error (or, with
    `warnings=True`, if there are any at all)."""
    found = [d for d in diagnostics if warnings or d.severity == "error"]
    if found:
        raise DiagnosticsError(found)
