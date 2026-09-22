"""Cast duckn volumes to a different data type."""

from __future__ import annotations

from copy import deepcopy

import numpy as np

from .volume import Volume


def cast(
    vol: Volume,
    dtype: str | np.dtype,
    *,
    normalize: bool = False,
    clamp: bool = True,
    range: tuple[float, float] | None = None,
) -> Volume:
    """Cast a volume to a different data type.

    Parameters
    ----------
    vol : input Volume
    dtype : target dtype (e.g., "float32", "uint8", "int16")
    normalize : if True, scale data to fill the target dtype's range.
        For float targets, scales to [0, 1].
        For integer targets, scales to [0, dtype_max] for unsigned
        or [dtype_min, dtype_max] for signed.
    clamp : if True (default), clip values to the target dtype's
        valid range before casting. Prevents silent overflow/wrap
        on narrowing casts.
    range : source range (min, max) for normalization.
        If None, uses (data.min(), data.max()).

    Returns
    -------
    Volume with cast data and same metadata
    """
    target = np.dtype(dtype)
    # Cast operates on the calibrated view (vol.data) — users typically
    # want to cast the values they see, not the raw storage. The result
    # is in calibrated space, so strip value_transforms below.
    data = vol.data

    if normalize:
        # Determine source range
        if range is not None:
            src_min, src_max = float(range[0]), float(range[1])
        else:
            src_min, src_max = float(data.min()), float(data.max())

        src_span = src_max - src_min
        if src_span == 0:
            src_span = 1.0

        # Determine destination range
        if np.issubdtype(target, np.floating):
            dst_min, dst_max = 0.0, 1.0
        elif np.issubdtype(target, np.unsignedinteger):
            info = np.iinfo(target)
            dst_min, dst_max = 0.0, float(info.max)
        else:
            info = np.iinfo(target)
            dst_min, dst_max = float(info.min), float(info.max)

        # Scale and clamp
        scaled = (data.astype(np.float64) - src_min) / src_span
        result = scaled * (dst_max - dst_min) + dst_min
        result = np.clip(result, dst_min, dst_max).astype(target)

    elif clamp and np.issubdtype(target, np.integer):
        # Clamp to target range before casting to prevent overflow
        info = np.iinfo(target)
        result = np.clip(data, info.min, info.max).astype(target)

    else:
        result = data.astype(target)

    new_meta = deepcopy(vol.metadata)
    if normalize:
        # Normalizing rescales into the target dtype's range, which changes
        # the *quantity*, not just its encoding — the values are no longer
        # in the source's units, so claiming them would be false
        # (duckn-spec §4.1).
        new_meta.sample_units = None
    new_meta.extensions = _seg_after_cast(
        new_meta.extensions, vol.raw.dtype, result.dtype, rescaled=normalize
    )
    # Calibrated values are baked into the result — clear value_transforms
    # so vol.data on the result doesn't double-apply.
    new_meta.value_transforms = None
    # A fill value is a stored value: it survives a plain cast that can hold it,
    # and means nothing after the values were rescaled.
    fill = None if normalize else vol.fill_value
    if fill is not None and result.dtype.kind in "iu":
        info = np.iinfo(result.dtype)
        if not info.min <= fill <= info.max:
            fill = None
    return Volume(raw=result, metadata=new_meta, fill_value=fill)


def _seg_after_cast(
    extensions: dict | None, source: np.dtype, target: np.dtype, *, rescaled: bool
) -> dict | None:
    """What becomes of a ``seg`` extension when the values are cast (seg spec
    §3.3). A binary labelmap's values are names of segments: it survives a cast
    that holds every listed value unchanged, and nothing else. Whether a
    labelmap is binary or fractional may rest on the data type alone (§3.1),
    so a cast that would change that reading writes the reading down first.
    """
    seg = (extensions or {}).get("seg")
    if not isinstance(seg, dict):
        return extensions
    from .seg_model import seg_is_fractional

    def without_seg() -> dict | None:
        return {k: v for k, v in extensions.items() if k != "seg"} or None

    fractional = seg_is_fractional(seg, source)
    to_integer = np.issubdtype(target, np.integer)
    if fractional:
        # fractions rescale and stay fractions; as integers they are not
        if to_integer and not rescaled:
            return without_seg()
        kept = dict(seg)
    else:
        if rescaled:
            return without_seg()
        if to_integer:
            info = np.iinfo(target)
            # a `members` segment lists nothing of its own: its values are its members'
            listed = [
                v
                for s in seg.get("segments") or [] if isinstance(s, dict)
                for v in (s.get("label_values") if isinstance(s.get("label_values"), list)
                          else [s.get("label_value")])
                if isinstance(v, int) and not isinstance(v, bool)
            ]
            if any(not info.min <= v <= info.max for v in listed):
                return without_seg()  # clamping or wrapping would rename voxels
        kept = dict(seg)
    if "source_representation" not in kept and (
        np.issubdtype(source, np.floating) != np.issubdtype(target, np.floating)
    ):
        kept["source_representation"] = (
            "fractional-labelmap" if fractional else "binary-labelmap"
        )
    return {**extensions, "seg": kept}
