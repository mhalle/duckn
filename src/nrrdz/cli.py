"""Click CLI for nrrdz."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import click

from .convert import nrrd_to_zarr, zarr_to_nrrd
from .models import NrrdMetadata


@click.group()
def cli() -> None:
    """nrrdz: convert between NRRD and nrrdz Zarr v3 stores."""


@cli.command("to-zarr")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--chunks", default=None, help="Chunk shape, e.g. 64,64,32")
@click.option(
    "--compressor",
    type=click.Choice(["zstd", "gzip", "none"]),
    default="zstd",
    help="Compression codec (default: zstd)",
)
@click.option("--level", type=int, default=3, help="Compression level (default: 3)")
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def to_zarr(
    input_path: str,
    output_path: str,
    chunks: str | None,
    compressor: str,
    level: int,
    overwrite: bool,
) -> None:
    """Convert an NRRD file to a nrrdz Zarr v3 store."""
    parsed_chunks = None
    if chunks is not None:
        parsed_chunks = tuple(int(c) for c in chunks.split(","))

    nrrd_to_zarr(
        input_path,
        output_path,
        chunks=parsed_chunks,
        compressor=compressor,
        level=level,
        overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("to-nrrd")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option(
    "--encoding",
    type=click.Choice(["gzip", "raw", "bzip2"]),
    default="gzip",
    help="NRRD encoding (default: gzip)",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def to_nrrd(
    input_path: str,
    output_path: str,
    encoding: str,
    overwrite: bool,
) -> None:
    """Convert a nrrdz Zarr v3 store to an NRRD file."""
    zarr_to_nrrd(
        input_path,
        output_path,
        encoding=encoding,
        overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("roundtrip")
@click.argument("input_path", type=click.Path(exists=True))
@click.option(
    "--compressor",
    type=click.Choice(["zstd", "gzip", "none"]),
    default="zstd",
    help="Zarr compression codec (default: zstd)",
)
@click.option("--level", type=int, default=3, help="Compression level (default: 3)")
@click.option(
    "--encoding",
    type=click.Choice(["gzip", "raw", "bzip2"]),
    default="gzip",
    help="NRRD output encoding (default: gzip)",
)
@click.option(
    "-o", "--output",
    type=click.Path(),
    default=None,
    help="Output NRRD path (default: overwrite input)",
)
def roundtrip(
    input_path: str,
    compressor: str,
    level: int,
    encoding: str,
    output: str | None,
) -> None:
    """Round-trip an NRRD file through nrrdz Zarr and back.

    Converts INPUT_PATH to a temporary Zarr store, then back to NRRD.
    Compares data and headers and reports any differences.
    """
    import math

    import nrrd as nrrd_lib
    import numpy as np

    from .convert import _NRRD_SPEC_FIELDS

    input_p = Path(input_path)
    tmp = Path(tempfile.mkdtemp(prefix="nrrdz_rt_"))
    zarr_path = tmp / (input_p.stem + ".zarr")
    rt_path = tmp / (input_p.stem + "_rt.nrrd")

    try:
        # Forward
        click.echo(f"NRRD -> Zarr  ({input_p.name})")
        nrrd_to_zarr(input_p, zarr_path, compressor=compressor, level=level)

        # Back
        click.echo(f"Zarr -> NRRD")
        zarr_to_nrrd(zarr_path, rt_path, encoding=encoding)

        # Compare
        click.echo("Comparing ...")
        data_orig, header_orig = nrrd_lib.read(str(input_p), index_order="C")
        data_rt, header_rt = nrrd_lib.read(str(rt_path), index_order="C")

        errors: list[str] = []

        # Data
        if data_orig.shape != data_rt.shape:
            errors.append(f"data shape: {data_orig.shape} vs {data_rt.shape}")
        elif data_orig.dtype != data_rt.dtype:
            errors.append(f"data dtype: {data_orig.dtype} vs {data_rt.dtype}")
        elif not np.array_equal(data_orig, data_rt):
            diff = np.max(np.abs(
                data_orig.astype(np.float64) - data_rt.astype(np.float64)
            ))
            errors.append(f"data values differ (max diff: {diff})")
        else:
            click.echo(f"  data: OK ({data_orig.dtype}, {data_orig.shape})")

        # Collect fields to compare: spec fields + key/value pairs
        fields: list[str] = [
            "space", "space dimension", "space origin", "space directions",
            "kinds", "centerings", "space units", "labels",
            "measurement frame", "thicknesses", "content", "sample units",
        ]
        for k in header_orig:
            if k not in _NRRD_SPEC_FIELDS and k not in fields:
                fields.append(k)

        for field in fields:
            val_orig = header_orig.get(field)
            val_rt = header_rt.get(field)
            if val_orig is None and val_rt is None:
                continue

            ok = False
            if val_orig is None or val_rt is None:
                pass  # mismatch
            elif isinstance(val_orig, np.ndarray) or isinstance(val_rt, np.ndarray):
                try:
                    a = np.asarray(val_orig, dtype=np.float64)
                    b = np.asarray(val_rt, dtype=np.float64)
                    ok = a.shape == b.shape and np.allclose(
                        a, b, equal_nan=True, atol=1e-10
                    )
                except (ValueError, TypeError):
                    ok = str(val_orig) == str(val_rt)
            elif isinstance(val_orig, list) and isinstance(val_rt, list):
                try:
                    a = np.asarray(val_orig, dtype=np.float64)
                    b = np.asarray(val_rt, dtype=np.float64)
                    ok = a.shape == b.shape and np.allclose(
                        a, b, equal_nan=True, atol=1e-10
                    )
                except (ValueError, TypeError):
                    def _n(v):
                        if v is None or str(v) in ("???", "", "none"):
                            return ""
                        return str(v)
                    ok = (
                        len(val_orig) == len(val_rt)
                        and [_n(x) for x in val_orig] == [_n(x) for x in val_rt]
                    )
            else:
                ok = str(val_orig) == str(val_rt)

            if ok:
                click.echo(f"  {field}: OK")
            else:
                errors.append(f"{field}: MISMATCH")
                click.echo(f"  {field}: MISMATCH")
                click.echo(f"    orig: {val_orig}")
                click.echo(f"    rt:   {val_rt}")

        # Count key/value pairs
        kv_count = sum(
            1 for k in header_orig if k not in _NRRD_SPEC_FIELDS
        )
        if kv_count:
            kv_ok = all(
                str(header_orig.get(k)) == str(header_rt.get(k))
                for k in header_orig
                if k not in _NRRD_SPEC_FIELDS
            )
            if kv_ok:
                click.echo(f"  key/value pairs: OK ({kv_count} pairs)")
            # individual mismatches already reported above

        if errors:
            click.echo(click.style(f"\nFAIL — {len(errors)} error(s)", fg="red"))
            sys.exit(1)
        else:
            click.echo(click.style("\nPASS", fg="green"))

        # Copy output if requested
        if output is not None:
            out_p = Path(output)
            shutil.copy2(rt_path, out_p)
            click.echo(f"Wrote {out_p}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@cli.command("info")
@click.argument("input_path", type=click.Path(exists=True))
def info(input_path: str) -> None:
    """Print nrrdz metadata as JSON."""
    path = Path(input_path)

    if path.suffix == ".nrrd" or path.suffix == ".nhdr":
        import nrrd as nrrd_lib

        _data, header = nrrd_lib.read(str(path), index_order="C")
        # Print raw header as JSON-serializable dict
        out: dict = {}
        for k, v in header.items():
            try:
                json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                import numpy as np

                if isinstance(v, np.ndarray):
                    out[k] = v.tolist()
                else:
                    out[k] = str(v)
        click.echo(json.dumps(out, indent=2))
    else:
        # Assume Zarr store
        from .zarr_io import get_zarr_attrs

        attrs = get_zarr_attrs(input_path)
        click.echo(json.dumps(attrs, indent=2))
