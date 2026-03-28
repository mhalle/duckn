"""Query interface for ZMP manifests.

Provides a lightweight wrapper around zarr-zmp's Manifest with
path glob filtering, inline parquet extraction, and optional
DuckDB SQL access.

Usage::

    from duckn.query import open_zmp

    zmp = open_zmp('patient.zmp')

    # List top-level entries
    for e in zmp.entries():
        print(e.path)

    # Drill into hierarchy
    for e in zmp.entries('/2001/standard'):
        print(e.path, e.addressing)

    # Glob search
    for e in zmp.entries('**/*.parquet'):
        df = e.as_parquet()

    # Single entry
    ct = zmp.entry('/2001/standard/ct')
    print(ct.is_mount, ct.resolve)

    # SQL on manifest
    zmp.sql("SELECT path, size FROM manifest WHERE size > 1000")
"""

from __future__ import annotations

import fnmatch
import io
import json
from pathlib import PurePosixPath
from typing import Any, Iterator

from zarr_zmp import Manifest

PARQUET_MIME = "application/vnd.apache.parquet"
ZMP_MIME = "application/vnd.apache.parquet+zmp"


class Entry:
    """A single entry from a ZMP manifest."""

    __slots__ = ("_entry", "_path")

    def __init__(self, manifest_entry: Any, path: str):
        self._entry = manifest_entry
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    @property
    def addressing(self) -> str:
        return self._entry.addressing

    @property
    def text(self) -> str | None:
        return self._entry.text

    @property
    def data(self) -> bytes | None:
        return self._entry.data if hasattr(self._entry, "data") else None

    @property
    def resolve(self) -> dict | None:
        if self._entry.resolve:
            return json.loads(self._entry.resolve)
        return None

    @property
    def base_resolve(self) -> dict | None:
        if self._entry.base_resolve:
            return json.loads(self._entry.base_resolve)
        return None

    @property
    def size(self) -> int | None:
        return self._entry.size

    @property
    def checksum(self) -> str | None:
        return self._entry.checksum

    @property
    def content_type(self) -> str | None:
        return self._entry.content_type

    @property
    def content_encoding(self) -> str | None:
        return self._entry.content_encoding

    @property
    def metadata(self) -> str | None:
        return self._entry.metadata

    @property
    def is_mount(self) -> bool:
        return "M" in (self._entry.addressing or "")

    @property
    def is_folder(self) -> bool:
        return "F" in (self._entry.addressing or "")

    @property
    def has_data(self) -> bool:
        return "D" in (self._entry.addressing or "")

    @property
    def has_text(self) -> bool:
        return "T" in (self._entry.addressing or "")

    @property
    def has_resolve(self) -> bool:
        return "R" in (self._entry.addressing or "")

    @property
    def is_parquet(self) -> bool:
        ct = self._entry.content_type or ""
        if ct == PARQUET_MIME:
            return True
        return self._path.endswith(".parquet") and self.has_data

    @property
    def is_zmp(self) -> bool:
        ct = self._entry.content_type or ""
        if ct == ZMP_MIME:
            return True
        return self._path.endswith(".zmp") and self.is_mount

    def as_json(self) -> dict:
        """Parse text content as JSON."""
        if self.text is None:
            raise ValueError(f"Entry {self._path} has no text content")
        return json.loads(self.text)

    def as_parquet(self) -> Any:
        """Read inline data as a pyarrow Table."""
        import pyarrow.parquet as pq

        data = self.data
        if data is None:
            raise ValueError(f"Entry {self._path} has no inline data")
        return pq.read_table(io.BytesIO(data))

    def sql(self, query: str) -> Any:
        """Run a DuckDB SQL query on inline parquet data.

        The data is available as the table ``entry``.
        Requires duckdb and pyarrow.

        Returns a DuckDB result (call ``.fetchdf()`` or ``.fetchall()``).
        """
        import duckdb

        entry = self.as_parquet()  # noqa: F841 — referenced in SQL
        conn = duckdb.connect()
        return conn.execute(query)

    def __repr__(self) -> str:
        return f"Entry({self._path!r}, addressing={self.addressing!r})"


class ZMP:
    """Query interface for a ZMP manifest."""

    def __init__(self, manifest: Manifest, path: str):
        self._manifest = manifest
        self._path = path
        # Build path index on first access
        self._all_paths: list[str] | None = None

    def _get_all_paths(self) -> list[str]:
        if self._all_paths is None:
            self._all_paths = [
                p for p in self._manifest.list_paths()
                if p and not p.endswith("/zarr.json")
            ]
        return self._all_paths

    def _children_of(self, prefix: str) -> list[str]:
        """List immediate children of a path prefix."""
        if prefix == "/":
            prefix = ""

        seen: set[str] = set()
        result: list[str] = []

        for p in self._manifest.list_paths():
            if not p or p == prefix:
                continue

            # Must start with prefix
            if prefix and not p.startswith(prefix + "/"):
                continue

            # Get the relative part
            rel = p[len(prefix):].lstrip("/")
            if not rel:
                continue

            # Immediate child = first path component
            child = rel.split("/")[0]

            # Skip zarr.json — it's infrastructure, not a user-visible entry
            if child == "zarr.json":
                continue

            if child not in seen:
                seen.add(child)
                full = f"{prefix}/{child}" if prefix else f"/{child}"
                result.append(full)

        return sorted(result)

    def entries(self, pattern: str | None = None, **kwargs: Any) -> list[Entry]:
        """List entries matching a pattern.

        With no arguments or ``"/"``, lists immediate children of the root.
        A path without wildcards lists immediate children of that path.
        Wildcards ``*`` (one level) and ``**`` (recursive) expand as in
        filesystem globs.

        Keyword filters:

        - ``mount=True`` — only mount entries
        - ``has_data=True`` — only entries with inline data
        - ``has_text=True`` — only entries with text content
        - ``has_resolve=True`` — only entries with resolve references
        - ``addressing="RMF"`` — exact addressing match
        """
        if pattern is None or pattern == "/":
            paths = self._children_of("/")
        elif "*" in pattern or "?" in pattern or "[" in pattern:
            paths = self._glob(pattern)
        else:
            # No wildcards — list children of this path
            paths = self._children_of(pattern)

        entries = []
        for p in paths:
            e = self._manifest.get_entry(p)
            if e is None:
                # Might be a virtual folder (no manifest entry, but children exist)
                # Create a synthetic folder entry
                entries.append(_SyntheticEntry(p))
                continue
            entry = Entry(e, p)
            if not self._matches_filters(entry, kwargs):
                continue
            entries.append(entry)

        return entries

    def entry(self, path: str) -> Entry:
        """Get a single entry by exact path. Raises KeyError if not found."""
        e = self._manifest.get_entry(path)
        if e is None:
            raise KeyError(f"Entry not found: {path}")
        return Entry(e, path)

    def exists(self, path: str) -> bool:
        """Check if an entry exists at the given path."""
        return self._manifest.get_entry(path) is not None

    def sql(self, query: str) -> Any:
        """Run a DuckDB SQL query on the manifest.

        The manifest is available as the table ``manifest``.
        Requires duckdb to be installed.

        Returns a DuckDB result (call ``.fetchdf()`` or ``.fetchall()``).
        """
        import duckdb

        conn = duckdb.connect()
        return conn.execute(
            query.replace("manifest", f"read_parquet('{self._path}')")
        )

    def _glob(self, pattern: str) -> list[str]:
        """Match paths against a glob pattern."""
        all_paths = self._get_all_paths()

        # Normalize pattern
        if not pattern.startswith("/"):
            pattern = "/" + pattern

        results = []
        for p in all_paths:
            if _glob_match(p, pattern):
                results.append(p)

        return sorted(results)

    @staticmethod
    def _matches_filters(entry: Entry, filters: dict[str, Any]) -> bool:
        if "mount" in filters and entry.is_mount != filters["mount"]:
            return False
        if "has_data" in filters and entry.has_data != filters["has_data"]:
            return False
        if "has_text" in filters and entry.has_text != filters["has_text"]:
            return False
        if "has_resolve" in filters and entry.has_resolve != filters["has_resolve"]:
            return False
        if "addressing" in filters and entry.addressing != filters["addressing"]:
            return False
        return True


class _SyntheticEntry:
    """A virtual folder entry for paths that exist only as parents."""

    def __init__(self, path: str):
        self.path = path
        self.addressing = "F"
        self.is_mount = False
        self.is_folder = True
        self.has_data = False
        self.has_text = False
        self.has_resolve = False

    def __repr__(self) -> str:
        return f"Entry({self.path!r}, addressing='F')"


def _glob_match(path: str, pattern: str) -> bool:
    """Match a path against a glob pattern supporting ** for recursive."""
    if "**" in pattern:
        # Split on ** and match each segment
        parts = pattern.split("**")
        if len(parts) == 2:
            prefix, suffix = parts
            prefix = prefix.rstrip("/")
            suffix = suffix.lstrip("/")
            if prefix and not path.startswith(prefix):
                return False
            if suffix:
                remaining = path[len(prefix):].lstrip("/")
                return fnmatch.fnmatch(remaining, suffix) or any(
                    fnmatch.fnmatch(remaining[i:], suffix)
                    for i in range(len(remaining))
                    if i == 0 or remaining[i - 1] == "/"
                )
            return path.startswith(prefix) if prefix else True
    return fnmatch.fnmatch(path, pattern)


def open_zmp(path: str | Any) -> ZMP:
    """Open a ZMP manifest for querying.

    Parameters
    ----------
    path : filesystem path to a .zmp file, or a Manifest object

    Returns
    -------
    ZMP query interface
    """
    if isinstance(path, Manifest):
        return ZMP(path, "")
    path_str = str(path)
    return ZMP(Manifest(path_str), path_str)
