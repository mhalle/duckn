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
    # Calibrated values are baked into the result — clear value_transforms
    # so vol.data on the result doesn't double-apply.
    new_meta.value_transforms = None
    return Volume(raw=result, metadata=new_meta)
