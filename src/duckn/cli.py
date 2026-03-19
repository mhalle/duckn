"""Click CLI for duckn."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import click

from .convert import nrrd_to_zarr, nrrd_to_zarr_zerocopy, zarr_to_nrrd, zarr_to_nrrd_zerocopy
from .models import DucknMetadata


@click.group()
def cli() -> None:
    """duckn: convert between NRRD and duckn Zarr v3 stores."""


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
@click.option("--zerocopy", is_flag=True, help="Use zero-copy mode (raw/gzip only)")
def to_zarr(
    input_path: str,
    output_path: str,
    chunks: str | None,
    compressor: str,
    level: int,
    overwrite: bool,
    zerocopy: bool,
) -> None:
    """Convert an NRRD file to a duckn Zarr v3 store."""
    if zerocopy:
        nrrd_to_zarr_zerocopy(
            input_path,
            output_path,
            overwrite=overwrite,
        )
        click.echo(f"Wrote {output_path} (zero-copy)")
    else:
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
@click.option("--zerocopy", is_flag=True, help="Use zero-copy mode (requires zero-copy store)")
def to_nrrd(
    input_path: str,
    output_path: str,
    encoding: str,
    overwrite: bool,
    zerocopy: bool,
) -> None:
    """Convert a duckn Zarr v3 store to an NRRD file."""
    if zerocopy:
        zarr_to_nrrd_zerocopy(
            input_path,
            output_path,
            overwrite=overwrite,
        )
        click.echo(f"Wrote {output_path} (zero-copy)")
    else:
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
@click.option("--zerocopy", is_flag=True, help="Use zero-copy for both directions")
def roundtrip(
    input_path: str,
    compressor: str,
    level: int,
    encoding: str,
    output: str | None,
    zerocopy: bool,
) -> None:
    """Round-trip an NRRD file through duckn Zarr and back.

    Converts INPUT_PATH to a temporary Zarr store, then back to NRRD.
    Compares data and headers and reports any differences.
    """
    import math

    import nrrd as nrrd_lib
    import numpy as np

    from .convert import _NRRD_SPEC_FIELDS

    input_p = Path(input_path)
    tmp = Path(tempfile.mkdtemp(prefix="duckn_rt_"))
    zarr_path = tmp / (input_p.stem + ".zarr")
    rt_path = tmp / (input_p.stem + "_rt.nrrd")

    try:
        if zerocopy:
            # Zero-copy forward
            click.echo(f"NRRD -> Zarr  ({input_p.name}) [zero-copy]")
            nrrd_to_zarr_zerocopy(input_p, zarr_path)

            # Zero-copy back
            click.echo(f"Zarr -> NRRD [zero-copy]")
            zarr_to_nrrd_zerocopy(zarr_path, rt_path)
        else:
            # Regular forward
            click.echo(f"NRRD -> Zarr  ({input_p.name})")
            nrrd_to_zarr(input_p, zarr_path, compressor=compressor, level=level)

            # Regular back
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
            "measurement frame", "thicknesses", "sample units",
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


@cli.command("from-nifti")
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
def from_nifti(
    input_path: str,
    output_path: str,
    chunks: str | None,
    compressor: str,
    level: int,
    overwrite: bool,
) -> None:
    """Convert a NIfTI file to a duckn Zarr v3 store.

    INPUT_PATH is a .nii or .nii.gz file.
    """
    from .nifti_convert import nifti_to_zarr

    parsed_chunks = None
    if chunks is not None:
        parsed_chunks = tuple(int(c) for c in chunks.split(","))

    nifti_to_zarr(
        input_path,
        output_path,
        chunks=parsed_chunks,
        compressor=compressor,
        level=level,
        overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("to-nifti")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def to_nifti_cmd(
    input_path: str,
    output_path: str,
    overwrite: bool,
) -> None:
    """Convert a duckn Zarr v3 store to a NIfTI file.

    OUTPUT_PATH should end in .nii or .nii.gz.
    """
    from .nifti_convert import zarr_to_nifti

    zarr_to_nifti(
        input_path,
        output_path,
        overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("from-dicom")
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
@click.option(
    "--anonymized/--no-anonymized",
    default=None,
    help="Override anonymization flag (auto-detect if omitted)",
)
@click.option("--no-tags", is_flag=True, help="Skip DICOM tag extraction")
def from_dicom(
    input_path: str,
    output_path: str,
    chunks: str | None,
    compressor: str,
    level: int,
    overwrite: bool,
    anonymized: bool | None,
    no_tags: bool,
) -> None:
    """Convert DICOM file(s) to a duckn Zarr v3 store.

    INPUT_PATH is a directory of single-frame .dcm files (one series)
    or a single enhanced multi-frame DICOM file.
    """
    from .dicom_convert import dicom_to_zarr

    parsed_chunks = None
    if chunks is not None:
        parsed_chunks = tuple(int(c) for c in chunks.split(","))

    dicom_to_zarr(
        input_path,
        output_path,
        chunks=parsed_chunks,
        compressor=compressor,
        level=level,
        overwrite=overwrite,
        anonymized=anonymized,
        tags=not no_tags,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("info")
@click.argument("input_path", type=click.Path(exists=True))
def info(input_path: str) -> None:
    """Print duckn metadata as JSON."""
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


@cli.command("header")
@click.argument("input_path", type=click.Path(exists=True))
def header(input_path: str) -> None:
    """Print the validated duckn metadata as JSON.

    Accepts either an NRRD file or a duckn Zarr store. For NRRD files the
    header is converted to duckn metadata first. Output is the model
    serialized with exclude_none (absent-means-unknown convention).
    """
    path = Path(input_path)

    if path.suffix in (".nrrd", ".nhdr"):
        import nrrd as nrrd_lib

        from .convert import _header_to_metadata

        _data, hdr = nrrd_lib.read(str(path), index_order="C")
        ndim = int(hdr["dimension"])
        meta, _dim_names, _extra = _header_to_metadata(hdr, ndim)
    else:
        from .zarr_io import read_duckn_metadata

        meta = read_duckn_metadata(input_path)

    click.echo(json.dumps(meta.model_dump(exclude_none=True), indent=2))


@cli.command("to-bids")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path(), required=False)
def to_bids(input_path: str, output_path: str | None) -> None:
    """Generate a BIDS JSON sidecar from a duckn store or DICOM series.

    If OUTPUT_PATH is omitted, prints to stdout.
    """
    path = Path(input_path)

    is_zarr = (
        path.suffix in (".zarr", ".zip")
        or (path.is_dir() and (path / "zarr.json").exists())
    )

    if is_zarr:
        from .zarr_io import read_duckn_metadata

        meta = read_duckn_metadata(input_path)
    elif path.is_dir() or path.suffix == ".dcm":
        # DICOM input — convert to duckn first (in memory)
        from .dicom_convert import dicom_to_zarr

        with tempfile.TemporaryDirectory() as tmp:
            tmp_zarr = Path(tmp) / "tmp.zarr"
            dicom_to_zarr(input_path, str(tmp_zarr))
            from .zarr_io import read_duckn_metadata

            meta = read_duckn_metadata(str(tmp_zarr))
    else:
        from .zarr_io import read_duckn_metadata

        meta = read_duckn_metadata(input_path)

    from .bids import duckn_to_bids_sidecar

    sidecar = duckn_to_bids_sidecar(meta)

    if output_path:
        Path(output_path).write_text(json.dumps(sidecar, indent=2))
        click.echo(f"Wrote {output_path}")
    else:
        click.echo(json.dumps(sidecar, indent=2))
