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


_SNOMED_KEYS = ("SCT", "SNOMEDCT", "SNOMED")


class SegmentView:
    """Read-only view of a single segment (seg extension 0.8).

    A segment lists the voxel values that belong to it. The 0.7 notions of a
    leaf and a group, and the ``label_value``, ``members``, ``background``,
    ``disjoint`` and ``exhaustive`` properties that went with them, have no
    counterpart: an older file is migrated before it is viewed.
    """

    def __init__(self, data: dict):
        self._data = data

    @property
    def name(self) -> str | None:
        return self._data.get("name")

    @property
    def id(self) -> str | None:
        return self._data.get("id")

    @property
    def label_values(self) -> list[int]:
        """The voxel values of this segment's layer that belong to it."""
        lv = self._data.get("label_values")
        return list(lv) if isinstance(lv, list) else []

    @property
    def role(self) -> str | None:
        """``"background"``, ``"unknown"``, or None for a structure."""
        return self._data.get("role")

    @property
    def layer(self) -> int:
        """The layer holding this segment's values; an absent ``layer`` is 0."""
        return self._data.get("layer") or 0

    @property
    def color(self) -> str | None:
        """The recommended display color, a CSS color string."""
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
        role = f", role={self.role!r}" if self.role else ""
        return f"Segment({self.name!r}, labels={self.label_values}{role})"


class SegAccessor:
    """Accessor for the segmentation extension.

    An older file is migrated to the current version once, here, so every
    answer comes from one shape. Construction never raises: a dict that
    cannot be read is kept as it is, and ``model`` says why. Lookups by value
    are keyed by ``(layer, value)``, since a value may belong to several
    segments of a layer; the *topmost* — the last in ``segments`` order —
    answers where one answer is wanted.
    """

    def __init__(self, data: dict, *, axes=None, shape=None, dtype=None, fill_value=None):
        self._raw = data
        self._context = dict(axes=axes, shape=shape, dtype=dtype, fill_value=fill_value)
        self._data = data
        self._migration: list = []
        if isinstance(data, dict):
            from .seg_read import migrate_seg_extension

            try:
                self._data, self._migration = migrate_seg_extension(data)
            except (ValueError, KeyError, TypeError, AttributeError):
                # a shape the migration cannot read is kept raw - whatever the reason
                # it could not - and `.model` reports it as the error it is
                self._data = data
        self._read = None

    def _do_read(self):
        if self._read is None:
            from .seg_read import read_seg_extension

            try:
                self._read = read_seg_extension(self._raw, **self._context)
            except Exception as exc:  # re-raised by `model`, every time it is asked
                self._read = exc
        return self._read

    @property
    def file_version(self):
        """The version the extension DECLARES on disk (``version`` answers with the
        migrated one, ``0.8`` for every readable older file)."""
        return self._raw.get("version") if isinstance(self._raw, dict) else None

    @property
    def model(self):
        """The extension as a validated ``seg_model.SegmentationExtension``
        (migrated to the current version), built once. Raises what
        ``seg_read.read_seg_extension`` raises when the file is refused."""
        read = self._do_read()
        if isinstance(read, Exception):
            raise read
        return read[0]

    @property
    def diagnostics(self) -> list:
        """What reading the extension reported: migration changes and rule
        violations. A refused file still reports everything (what it was
        refused over is on the error ``model`` raises), or nothing if it is
        not the shape of a seg extension at all."""
        read = self._do_read()
        if isinstance(read, Exception):
            return list(getattr(read, "all_diagnostics", []))
        return list(read[1])

    def background_value(self, layer: int = 0) -> int | None:
        """The layer's background value, or None when it has no background."""
        from .seg_model import background_value

        return background_value(self.model, layer)

    def color_map(self, *, layer: int = 0) -> dict[int, str]:
        """``{value: CSS color}`` for a layer: each value takes the color of the
        topmost segment listing it that has one."""
        from .seg_model import color_map

        return color_map(self.model, layer=layer)

    @property
    def version(self) -> str | None:
        return self._data.get("version")

    @property
    def segments(self) -> list[SegmentView]:
        segs = self._data.get("segments", [])
        return [SegmentView(s) for s in segs if isinstance(s, dict)] if isinstance(segs, list) else []

    @property
    def source_representation(self) -> str | None:
        return self._data.get("source_representation")

    @property
    def labeling_schemes(self) -> list[str]:
        raw = self._data.get("labeling_scheme")
        keys = [raw] if isinstance(raw, str) else list(raw or [])
        return list(dict.fromkeys(keys))

    @property
    def metadata(self) -> dict | None:
        return self._data.get("metadata")

    def segments_for(self, label_value: int, *, layer: int = 0) -> list[SegmentView]:
        """Every segment of ``layer`` listing ``label_value``, in ``segments`` order."""
        return [s for s in self.segments if s.layer == layer and label_value in s.label_values]

    def segment(
        self,
        *,
        id: str | None = None,
        name: str | None = None,
        label_value: int | None = None,
        layer: int = 0,
        snomed: str | None = None,
    ) -> SegmentView | None:
        """Find one segment: by id; by name (the first with it); by label value
        (the topmost segment of ``layer`` listing it); or by SNOMED code, in
        ``designations`` or as the DICOM type (the topmost carrying it, in the
        lowest layer that has one).

        Returns a SegmentView, or None if not found.
        """
        if id is not None or name is not None:
            for seg in self.segments:
                if (id is not None and seg.id == id) or (name is not None and seg.name == name):
                    return seg
        if label_value is not None:
            found = self.segments_for(label_value, layer=layer)
            if found:
                return found[-1]
        if snomed is not None:
            best = None
            for seg in self.segments:
                entries = [*seg.designations, seg.dicom.get("type") or {}]
                if any(
                    str(e.get("scheme", "")).upper() in _SNOMED_KEYS and e.get("code") == snomed
                    for e in entries
                ) and (best is None or seg.layer <= best.layer):
                    best = seg
            return best
        return None

    def label_for(self, name: str) -> list[int]:
        """The label values of the first segment with this name; empty if none."""
        seg = self.segment(name=name)
        return seg.label_values if seg is not None else []

    def name_for(self, label_value: int, *, layer: int = 0) -> str | None:
        """The name that answers for ``label_value`` in ``layer``: that of the
        topmost segment listing it that has one, as a color is resolved."""
        for seg in reversed(self.segments_for(label_value, layer=layer)):
            if seg.name is not None:
                return seg.name
        return None

    @property
    def names(self) -> list[str | None]:
        """List all segment names."""
        return [s.name for s in self.segments]

    @property
    def label_values(self) -> list[list[int]]:
        """Each segment's label values, in ``segments`` order."""
        return [s.label_values for s in self.segments]

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
        """The extension dict, migrated to the current version."""
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

    def __init__(self, data: dict[str, Any] | None, *, seg_context: dict[str, Any] | None = None):
        self._data = data or {}
        # What the seg rules need to know about the array (axes, shape, dtype)
        self._seg_context = seg_context or {}

    def _accessor(self, name: str, ext: Any) -> Any:
        accessor_cls = _ACCESSORS.get(name)
        if accessor_cls is SegAccessor:
            return SegAccessor(ext, **self._seg_context)
        return accessor_cls(ext) if accessor_cls else ext

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        ext = self._data.get(name)
        if ext is None:
            return None
        return self._accessor(name, ext)

    def __getitem__(self, name: str) -> Any:
        ext = self._data.get(name)
        if ext is None:
            raise KeyError(name)
        return self._accessor(name, ext)

    def __contains__(self, name: str) -> bool:
        return name in self._data

    def keys(self) -> list[str]:
        return list(self._data.keys())

    def __repr__(self) -> str:
        return f"Extensions({self.keys()})"
