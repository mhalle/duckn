# Archive

**Nothing in this directory is normative.** These are historical records —
plans that were carried out, and decisions that explain why the code looks
the way it does. They are kept because the reasoning is worth preserving,
not because they describe the current system.

For what duckn *is*, see [`../README.md`](../README.md).

Two kinds of document live here, and the distinction matters when reading
one:

**Superseded plans** described an intended design. The work was done, and
the result diverged. Read them as history; do not implement from them.

- [`duckn-converter-prototype.md`](duckn-converter-prototype.md) —
  "Round-Trip Converter: Specification and Action Plan", 2026-03-02. The
  plan for the original NRRD ↔ Zarr converter. Substantially outgrown: it
  specifies a `.duckn` directory extension that was never adopted, a
  single-chunk layout since replaced by ~1 MB automatic chunking, and a
  top-level `duckn.legacy` object the convention does not define. Most of
  its "out of scope" list — extensions, multi-array stores, writing from
  scratch — has since been built. Its markdown never rendered correctly,
  and it has been left as found.

**Decision records** explain a choice that was made and implemented. These
remain accurate about *why*; the specifications remain authoritative about
*what*.

- [`zero-copy-axis-order.md`](zero-copy-axis-order.md) — why every duckn
  store uses `slowest_first` axis order regardless of which conversion
  path produced it, and why the zero-copy path no longer keeps NRRD's
  native `fastest_first` layout. Still describes the current behavior of
  the `axis_order` parameter in `_header_to_metadata` and
  `_metadata_to_header`.
