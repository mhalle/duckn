"""Extension accessors for duckn volumes.

Provides typed access to known extensions (seg, dicom, dwmri)
and raw dict access for unknown extensions.

Usage:
    vol.extensions.seg.segments
    vol.extensions.dicom.tags["Modality"]
    vol.extensions["custom"]
"""

from __future__ import annotations

from typing import Any


class SegmentView:
    """Read-only view of a single segment."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def name(self) -> str | None:
        return self._data.get("name")

    @property
    def id(self) -> str | None:
        return self._data.get("id")

    @property
    def label_value(self) -> int | None:
        return self._data.get("label_value")

    @property
    def layer(self) -> int | None:
        return self._data.get("layer")

    @property
    def color(self) -> list[float] | None:
        return self._data.get("color")

    @property
    def identifiers(self) -> dict:
        return self._data.get("identifiers", {})

    @property
    def metadata(self) -> dict:
        return self._data.get("metadata", {})

    @property
    def raw(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        return f"Segment({self.name!r}, label={self.label_value})"


class SegAccessor:
    """Accessor for the segmentation extension."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def version(self) -> str | None:
        return self._data.get("version")

    @property
    def segments(self) -> list[SegmentView]:
        return [SegmentView(s) for s in self._data.get("segments", [])]

    @property
    def source_representation(self) -> str | None:
        return self._data.get("source_representation")

    @property
    def metadata(self) -> dict | None:
        return self._data.get("metadata")

    def segment(
        self,
        *,
        name: str | None = None,
        label_value: int | None = None,
        snomed: str | None = None,
    ) -> SegmentView | None:
        """Find a segment by name, label value, or SNOMED code.

        Returns a SegmentView, or None if not found.
        """
        for seg in self.segments:
            if name is not None and seg.name == name:
                return seg
            if label_value is not None:
                lv = seg.label_value
                if lv == label_value or lv == [label_value]:
                    return seg
            if snomed is not None:
                sct = seg.identifiers.get("snomedct", {})
                if sct.get("id") == snomed:
                    return seg
                dicom = seg.metadata.get("dicom", {})
                type_entry = dicom.get("type", {})
                if type_entry.get("code") == snomed:
                    return seg
        return None

    def label_for(self, name: str) -> int | None:
        """Get the label value for a segment name."""
        seg = self.segment(name=name)
        return seg.label_value if seg else None

    def name_for(self, label_value: int) -> str | None:
        """Get the name for a label value."""
        seg = self.segment(label_value=label_value)
        return seg.name if seg else None

    @property
    def names(self) -> list[str | None]:
        """List all segment names."""
        return [s.name for s in self.segments]

    @property
    def label_values(self) -> list:
        """List all label values (int or list[int])."""
        return [s.label_value for s in self.segments]

    @property
    def raw(self) -> dict:
        """Raw extension dict."""
        return self._data

    def __repr__(self) -> str:
        return f"SegAccessor({len(self.segments)} segments)"


class DicomAccessor:
    """Accessor for the DICOM extension."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def version(self) -> str | None:
        return self._data.get("version")

    @property
    def tags(self) -> dict:
        return self._data.get("tags", {})

    @property
    def raw(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        n = len(self.tags)
        return f"DicomAccessor({n} tags)"


class DwmriAccessor:
    """Accessor for the DWI MRI extension."""

    def __init__(self, data: dict):
        self._data = data

    @property
    def version(self) -> str | None:
        return self._data.get("version")

    @property
    def b_value(self) -> float | None:
        return self._data.get("b_value")

    @property
    def gradient_frame(self) -> str | None:
        return self._data.get("gradient_frame")

    @property
    def acquisition(self) -> dict | None:
        return self._data.get("acquisition")

    @property
    def raw(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        return f"DwmriAccessor(b={self.b_value})"


# Map extension name → accessor class
_ACCESSORS = {
    "seg": SegAccessor,
    "dicom": DicomAccessor,
    "dwmri": DwmriAccessor,
}


class Extensions:
    """Namespace for accessing volume extensions.

    Known extensions (seg, dicom, dwmri) return typed accessors.
    Unknown extensions return raw dicts via __getitem__.
    """

    def __init__(self, data: dict[str, Any] | None):
        self._data = data or {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        ext = self._data.get(name)
        if ext is None:
            return None
        accessor_cls = _ACCESSORS.get(name)
        if accessor_cls:
            return accessor_cls(ext)
        return ext

    def __getitem__(self, name: str) -> Any:
        ext = self._data.get(name)
        if ext is None:
            raise KeyError(name)
        accessor_cls = _ACCESSORS.get(name)
        if accessor_cls:
            return accessor_cls(ext)
        return ext

    def __contains__(self, name: str) -> bool:
        return name in self._data

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def __repr__(self) -> str:
        return f"Extensions({self.keys()})"
