"""Lint entry point: load + analyze, without producing any output artifacts."""

from __future__ import annotations

from pathlib import Path

from .diagnostics import Diagnostics
from .loader import SourceLoader
from .model import MemoryMap
from .semantics import analyze


def lint(top: Path | str, address_bits: int | None = None) -> tuple[MemoryMap | None, Diagnostics]:
    """`address_bits` sets the address width when `top` is a bare `regions:` file."""
    diags = Diagnostics()
    mm = SourceLoader(diags).load(Path(top), address_bits)
    # Semantic checks only run on a schema-clean map, to avoid cascading errors
    # caused by regions that were dropped during loading.
    if mm is not None and not diags.errors:
        analyze(mm, diags)
    return mm, diags
