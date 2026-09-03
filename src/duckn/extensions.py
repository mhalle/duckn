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
        """A leaf's voxel value in its layer; None for a group."""
        lv = self._data.get("label_value")
        return lv if isinstance(lv, int) and not isinstance(lv, bool) else None

    @property
    def members(self) -> list[str]:
        """The segment ids a group is the union of; empty for a leaf."""
        return list(self._data.get("members") or [])

    @property
    def is_group(self) -> bool:
        return self._data.get("members") is not None

    @property
    def label_values(self) -> list[int]:
        """The label value this segment owns directly, as a list: one entry for
        a leaf, none for a group. Resolve a group with
        ``SegAccessor.effective_label_values``."""
        lv = self.label_value
        return [lv] if lv is not None else []

    @property
    def background(self) -> bool:
        return bool(self._data.get("background"))

    @property
    def disjoint(self) -> bool:
        return bool(self._data.get("disjoint"))

    @property
    def exhaustive(self) -> bool:
        return bool(self._data.get("exhaustive"))

    @property
    def layer(self) -> int | None:
        return self._data.get("layer")

    @property
    def color(self) -> list[float] | None:
        return self._data.get("color")

    @property
    def designations(self) -> list[dict]:
        return self._data.get("designations") or []

    @property
    def dicom(self) -> dict:
        return self._data.get("dicom") or {}

    @property
    def metadata(self) -> dict:
        return self._data.get("metadata") or {}

    @property
    def raw(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        if self.is_group:
            return f"Segment({self.name!r}, members={self.members})"
        return f"Segment({self.name!r}, label={self.label_value})"


class SegAccessor:
    """Accessor for the segmentation extension."""

    def __init__(self, data: dict):
        self._data = data
        self._model = None

    @property
    def model(self):
        """The extension as a validated ``SegmentationExtension`` (migrated to
        the current version), built once; the graph questions below run on it."""
        if self._model is None:
            from .models import SegmentationExtension

            self._model = SegmentationExtension.model_validate(self._data)
        return self._model

    def effective_label_values(self, segment_id: str) -> set[tuple[int, int]]:
        """A segment's voxel set as ``(layer, label_value)`` pairs, groups resolved."""
        from .models import effective_label_values

        return effective_label_values(self.model, segment_id)

    def members_of(self, segment_id: str) -> list[str]:
        """Every leaf under a segment (itself, if it is a leaf)."""
        from .models import leaves_of

        return leaves_of(self.model, segment_id)

    def parents_of(self, segment_id: str) -> list[str]:
        """The groups that list a segment directly."""
        from .models import parents_of

        return parents_of(self.model, segment_id)

    def background_value(self, layer: int = 0) -> int:
        from .models import background_value

        return background_value(self.model, layer)

    def color_map(self, *, layer: int = 0, inherit: bool = True) -> dict[int, list[float]]:
        """``{label_value: color}`` for a layer's leaves, colors inherited from groups."""
        from .models import color_map

        return color_map(self.model, layer=layer, inherit=inherit)

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
        layer: int = 0,
        snomed: str | None = None,
    ) -> SegmentView | None:
        """Find a segment by name, by label value (the one leaf owning it in
        ``layer``), or by SNOMED code.

        Returns a SegmentView, or None if not found.
        """
        for seg in self.segments:
            if name is not None and seg.name == name:
                return seg
            if (label_value is not None and seg.label_value == label_value
                    and (seg.layer or 0) == layer):
                return seg
            if snomed is not None:
                for des in seg.designations:
                    scheme = str(des.get("scheme", "")).upper()
                    if scheme in ("SCT", "SNOMEDCT", "SNOMED") and des.get("code") == snomed:
                        return seg
                type_entry = seg.dicom.get("type") or {}
                if type_entry.get("scheme", "SCT").upper() in (
                    "SCT",
                    "SNOMEDCT",
                    "SNOMED",
                ) and type_entry.get("code") == snomed:
                    return seg
        return None

    def label_for(self, name: str) -> int | None:
        """Get the (first) integer label value for a segment name."""
        seg = self.segment(name=name)
        if seg is None:
            return None
        labels = seg.label_values
        return labels[0] if labels else None

    def name_for(self, label_value: int) -> str | None:
        """Get the name for a label value."""
        seg = self.segment(label_value=label_value)
        return seg.name if seg else None

    @property
    def names(self) -> list[str | None]:
        """List all segment names."""
        return [s.name for s in self.segments]

    @property
    def label_values(self) -> list[int | None]:
        """Each segment's own label value: an int for a leaf, None for a group."""
        return [s.label_value for s in self.segments]

    @property
    def terminologies(self) -> dict:
        """The coding-system registry, keyed by scheme."""
        return self._data.get("terminologies") or {}

    def concept_url(self, scheme: str, code: str) -> str | None:
        """Resolve a concept URL from the registry's ``url_template``."""
        template = (self.terminologies.get(scheme) or {}).get("url_template")
        if not template:
            return None
        return template.replace("{code}", code)

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
