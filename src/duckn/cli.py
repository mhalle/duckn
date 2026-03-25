"""Click CLI for duckn."""

from __future__ import annotations

import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

import click

# Silence httpx request logging
logging.getLogger("httpx").setLevel(logging.WARNING)

from .convert import nrrd_to_zarr, nrrd_to_zarr_zerocopy, zarr_to_nrrd, zarr_to_nrrd_zerocopy
from .models import DucknMetadata


def _open_store(input_path: str) -> Any:
    """Open any supported input as a Zarr store.

    Supports:
    - .zmp files (via ZMPStore)
    - .zarr.zip files (via ZipStore)
    - Zarr directory stores
    - Returns None for non-Zarr inputs (NRRD, DICOM, etc.)
    """
    path = Path(input_path)

    if path.suffix == ".zmp":
        from zarr_zmp import ZMPStore
        return ZMPStore.from_file(str(path))

    if str(path).endswith(".zarr.zip"):
        import zarr
        return zarr.storage.ZipStore(str(path), mode="r")

    if path.is_dir() and (path / "zarr.json").exists():
        import zarr
        return zarr.storage.LocalStore(str(path))

    if path.suffix == ".zarr" and path.is_dir():
        import zarr
        return zarr.storage.LocalStore(str(path))

    return None


@click.group()
def cli() -> None:
    """duckn: imaging format converters and ZMP manifest builders."""


@cli.command("from-nrrd")
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
    """Convert an NRRD file to a duckn Zarr v3 store or ZMP manifest.

    Output format is determined by OUTPUT_PATH extension:
    .zarr/.zarr.zip → Zarr v3 store, .zmp → virtual ZMP manifest.
    """
    out = Path(output_path)

    if out.suffix == ".zmp":
        from .nifti_convert import build_nifti_zmp as _build_nifti_zmp  # noqa: F811

        # NRRD → Zarr first (temp), then build ZMP from the .nii-like layout
        # Actually, for NRRD we convert to Zarr then wrap as ZMP
        # Simpler: convert to temp zarr.zip, then zarr_zip_to_zmp
        tmp = Path(tempfile.mkdtemp(prefix="duckn_zmp_"))
        try:
            zip_path = tmp / "temp.zarr.zip"
            if zerocopy:
                nrrd_to_zarr_zerocopy(input_path, str(zip_path), overwrite=True)
            else:
                parsed_chunks = None
                if chunks is not None:
                    parsed_chunks = tuple(int(c) for c in chunks.split(","))
                nrrd_to_zarr(
                    input_path, str(zip_path),
                    chunks=parsed_chunks, compressor=compressor,
                    level=level, overwrite=True,
                )

            from .zarr_zip_convert import zarr_zip_to_zmp

            zarr_zip_to_zmp(str(zip_path), output_path, overwrite=overwrite)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        click.echo(f"Wrote {output_path}")
    elif zerocopy:
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
    """Convert a NIfTI file to a duckn Zarr v3 store or ZMP manifest.

    INPUT_PATH is a .nii or .nii.gz file.
    Output format is determined by OUTPUT_PATH extension:
    .zarr → Zarr v3 store, .zmp → virtual ZMP manifest.

    For .zmp output with .nii files, the ZMP contains per-slice byte-range
    references into the NIfTI file (no data copied). Requires uncompressed .nii.
    """
    out = Path(output_path)

    if out.suffix == ".zmp":
        from .nifti_convert import build_nifti_zmp

        build_nifti_zmp(input_path, output_path, overwrite=overwrite)
    else:
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
@click.option("--content-hash", is_flag=True, help="Compute git-sha1 retrieval keys (ZMP only)")
@click.option("--inline-data", is_flag=True, help="Store pixel data inline (ZMP only)")
def from_dicom(
    input_path: str,
    output_path: str,
    chunks: str | None,
    compressor: str,
    level: int,
    overwrite: bool,
    anonymized: bool | None,
    no_tags: bool,
    content_hash: bool,
    inline_data: bool,
) -> None:
    """Convert DICOM file(s) to a duckn Zarr v3 store or ZMP manifest.

    INPUT_PATH is a directory of single-frame .dcm files (one series),
    a single enhanced multi-frame DICOM file, or a DICOM Segmentation
    object (BINARY or LABELMAP). DICOM SEG files are automatically
    detected and converted to 4D binary channels or 3D labelmaps.

    Output format is determined by OUTPUT_PATH extension:
    .zarr → Zarr v3 store, .zmp → ZMP Parquet manifest.
    """
    out = Path(output_path)

    if out.suffix == ".zmp":
        from .dicom_convert import build_local_zmp

        build_local_zmp(
            input_path,
            output_path,
            tags=not no_tags,
            content_hash=content_hash,
            inline_data=inline_data,
            overwrite=overwrite,
        )
    else:
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


@cli.command("from-idc")
@click.argument("identifier")
@click.argument("output_path", type=click.Path())
@click.option("--base-url", default=None, help="IDC bucket base URL (default: public S3)")
@click.option("--no-tags", is_flag=True, help="Skip DICOM tag extraction")
@click.option("--content-hash", is_flag=True, help="Compute git-sha1 retrieval keys")
@click.option("--inline-data", is_flag=True, help="Fetch and store pixel data inline")
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def from_idc(
    identifier: str,
    output_path: str,
    base_url: str | None,
    no_tags: bool,
    content_hash: bool,
    inline_data: bool,
    overwrite: bool,
) -> None:
    """Build a ZMP manifest for an IDC DICOM series.

    IDENTIFIER is a CRDC series UUID, a DICOM SeriesInstanceUID, or a
    SOPInstanceUID. If it contains dots it is treated as a DICOM UID and
    resolved via idc-index; otherwise it is used as a CRDC UUID directly.
    """
    from .idc_zmp import build_idc_zmp

    series_uuid = _resolve_idc_identifier(identifier)

    kwargs: dict = dict(
        series_uuid=series_uuid,
        output_path=output_path,
        tags=not no_tags,
        content_hash=content_hash,
        inline_data=inline_data,
        overwrite=overwrite,
    )
    if base_url is not None:
        kwargs["base_url"] = base_url

    build_idc_zmp(**kwargs)
    click.echo(f"Wrote {output_path}")


def _resolve_idc_identifier(identifier: str) -> str:
    """Resolve a DICOM UID or CRDC UUID to a crdc_series_uuid.

    CRDC UUIDs look like ``8-4-4-4-12`` hex (e.g.
    ``bfa2aab6-85de-4f92-b311-e6c8a52b9299``). Anything else is
    treated as a DICOM UID and resolved via idc-index.
    """
    import re

    if re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        identifier,
        re.IGNORECASE,
    ):
        return identifier

    try:
        from idc_index import IDCClient
    except ImportError:
        raise click.ClickException(
            "idc-index is required for DICOM UID lookup. "
            "Install it with: pip install idc-index"
        )

    client = IDCClient()

    # Try SeriesInstanceUID first
    result = client.sql_query(
        "SELECT DISTINCT crdc_series_uuid "
        "FROM index "
        f"WHERE SeriesInstanceUID = '{identifier}'"
    )
    if len(result) == 1:
        return result["crdc_series_uuid"].iloc[0]
    if len(result) > 1:
        raise click.ClickException(
            f"Multiple series found for SeriesInstanceUID {identifier}"
        )

    # Try SOPInstanceUID
    result = client.sql_query(
        "SELECT DISTINCT crdc_series_uuid "
        "FROM index "
        f"WHERE SOPInstanceUID = '{identifier}'"
    )
    if len(result) == 1:
        return result["crdc_series_uuid"].iloc[0]
    if len(result) > 1:
        raise click.ClickException(
            f"Multiple series found for SOPInstanceUID {identifier}"
        )

    raise click.ClickException(
        f"No IDC series found for identifier: {identifier}"
    )


@cli.command("from-dicomweb")
@click.argument("dicomweb_url", type=str)
@click.argument("study_uid", type=str)
@click.argument("series_uid", type=str)
@click.argument("output_path", type=click.Path())
@click.option("--no-tags", is_flag=True, help="Skip DICOM tag extraction")
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def from_dicomweb(
    dicomweb_url: str,
    study_uid: str,
    series_uid: str,
    output_path: str,
    no_tags: bool,
    overwrite: bool,
) -> None:
    """Build a ZMP manifest from a DICOMweb server.

    Uses a single WADO-RS metadata request to build a virtual ZMP with
    per-frame WADO-RS URLs. No pixel data is fetched.

    \b
    Example:
      duckn from-dicomweb https://server/dicomWeb 1.2.840... 1.2.840... out.zmp
    """
    from .idc_zmp import build_dicomweb_zmp

    build_dicomweb_zmp(
        dicomweb_url, study_uid, series_uid, output_path,
        tags=not no_tags, overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("to-dicom")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def to_dicom(
    input_path: str,
    output_path: str,
    overwrite: bool,
) -> None:
    """Convert a duckn 3D Zarr store to an Enhanced Multi-frame DICOM file.

    For imaging data (CT, MR, PET). Use to-dicom-seg for segmentations.
    """
    from .dicom_convert import zarr_to_dicom

    zarr_to_dicom(input_path, output_path, overwrite=overwrite)
    click.echo(f"Wrote {output_path}")


@cli.command("to-dicom-seg")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def to_dicom_seg(
    input_path: str,
    output_path: str,
    overwrite: bool,
) -> None:
    """Convert a duckn 3D labelmap to a DICOM LABELMAP Segmentation (Sup 243).

    Input must be a 3D integer labelmap. Use seg-to-labelmap first if
    your data is a 4D binary segmentation.
    """
    from .dicom_convert import zarr_to_dicom_seg

    zarr_to_dicom_seg(input_path, output_path, overwrite=overwrite)
    click.echo(f"Wrote {output_path}")


@cli.command("seg-to-labelmap")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--compressor", type=click.Choice(["zstd", "gzip", "none"]), default="zstd")
@click.option("--level", type=int, default=3)
@click.option("--overwrite", is_flag=True)
def seg_to_labelmap_cmd(
    input_path: str,
    output_path: str,
    compressor: str,
    level: int,
    overwrite: bool,
) -> None:
    """Convert a 4D binary segmentation to a 3D integer labelmap.

    Input is a 4D duckn store (one binary channel per segment).
    Output is a 3D store where each voxel value is a segment number.
    Accepts Zarr stores and ZMP manifests.
    """
    from .seg_convert import seg_4d_to_labelmap

    store = _open_store(input_path)
    seg_4d_to_labelmap(
        store if store is not None else input_path,
        output_path,
        compressor=compressor,
        level=level,
        overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("labelmap-to-seg")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path())
@click.option("--compressor", type=click.Choice(["zstd", "gzip", "none"]), default="zstd")
@click.option("--level", type=int, default=3)
@click.option("--overwrite", is_flag=True)
def labelmap_to_seg_cmd(
    input_path: str,
    output_path: str,
    compressor: str,
    level: int,
    overwrite: bool,
) -> None:
    """Convert a 3D integer labelmap to a 4D binary segmentation.

    Each non-zero label becomes a binary channel. Output has one
    layer per segment with chunk shape (1, nz, rows, cols).
    Accepts Zarr stores and ZMP manifests.
    """
    from .seg_convert import labelmap_to_seg_4d

    store = _open_store(input_path)
    labelmap_to_seg_4d(
        store if store is not None else input_path,
        output_path,
        compressor=compressor,
        level=level,
        overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")


@cli.command("info")
@click.argument("input_path", type=click.Path(exists=True))
def info(input_path: str) -> None:
    """Print duckn metadata as JSON.

    Accepts NRRD files, Zarr stores, or ZMP manifests.
    """
    path = Path(input_path)

    if path.suffix in (".nrrd", ".nhdr"):
        import nrrd as nrrd_lib

        _data, header = nrrd_lib.read(str(path), index_order="C")
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
        store = _open_store(input_path)
        if store is not None:
            import zarr
            arr = zarr.open_array(store=store, mode="r")
            click.echo(json.dumps(dict(arr.attrs), indent=2))
        else:
            from .zarr_io import get_zarr_attrs
            attrs = get_zarr_attrs(input_path)
            click.echo(json.dumps(attrs, indent=2))


@cli.command("header")
@click.argument("input_path", type=click.Path(exists=True))
def header(input_path: str) -> None:
    """Print the validated duckn metadata as JSON.

    Accepts NRRD files, Zarr stores, or ZMP manifests.
    """
    path = Path(input_path)

    if path.suffix in (".nrrd", ".nhdr"):
        import nrrd as nrrd_lib

        from .convert import _header_to_metadata

        _data, hdr = nrrd_lib.read(str(path), index_order="C")
        ndim = int(hdr["dimension"])
        meta, _dim_names = _header_to_metadata(hdr, ndim)
    else:
        from .zarr_io import read_duckn_metadata

        store = _open_store(input_path)
        meta = read_duckn_metadata(store if store is not None else input_path)

    click.echo(json.dumps(meta.model_dump(exclude_none=True), indent=2))


@cli.command("to-bids")
@click.argument("input_path", type=click.Path(exists=True))
@click.argument("output_path", type=click.Path(), required=False)
def to_bids(input_path: str, output_path: str | None) -> None:
    """Generate a BIDS JSON sidecar from a duckn store, ZMP, or DICOM series.

    If OUTPUT_PATH is omitted, prints to stdout.
    """
    path = Path(input_path)

    store = _open_store(input_path)
    if store is not None:
        from .zarr_io import read_duckn_metadata

        meta = read_duckn_metadata(store)
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


@cli.command("from-zarr-zip")
@click.argument("source", type=str)
@click.argument("output_path", type=click.Path())
@click.option("--prefix", default="", help="Path prefix inside the zip (e.g. 'data.zarr/')")
@click.option("--hydrate", is_flag=True, help="Embed chunk data inline (default: virtual byte ranges)")
@click.option("--no-duckn", is_flag=True, help="Skip duckn metadata injection")
@click.option("--overwrite", is_flag=True, help="Overwrite existing output")
def from_zarr_zip(
    source: str,
    output_path: str,
    prefix: str,
    hydrate: bool,
    no_duckn: bool,
    overwrite: bool,
) -> None:
    """Convert a Zarr v2/v3 zip store to a duckn ZMP manifest.

    SOURCE is a local path or HTTP/HTTPS URL to a .zarr.zip file.
    Supports both Zarr v2 and v3, including OME-NGFF multi-resolution pyramids.

    \b
    Examples:
      duckn from-zarr-zip data.zarr.zip data.zmp
      duckn from-zarr-zip https://s3.../data.zarr.zip data.zmp
      duckn from-zarr-zip data.zarr.zip data.zmp --hydrate
      duckn from-zarr-zip big.zarr.zip out.zmp --prefix="nested.zarr/"
    """
    from .zarr_zip_convert import zarr_zip_to_zmp

    zarr_zip_to_zmp(
        source,
        output_path,
        prefix=prefix,
        hydrate=hydrate,
        duckn=not no_duckn,
        overwrite=overwrite,
    )
    click.echo(f"Wrote {output_path}")
