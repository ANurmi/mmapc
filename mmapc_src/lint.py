"""Lint entry point: load + analyze, without producing any output artifacts."""

from __future__ import annotations

from pathlib import Path

from .diagnostics import Diagnostics
from .loader import SourceLoader
from .model import MemoryMap
from .semantics import analyze


def lint(top: Path | str) -> tuple[MemoryMap | None, Diagnostics]:
    diags = Diagnostics()
    mm = SourceLoader(diags).load(Path(top))
    # Semantic checks only run on a schema-clean map, to avoid cascading errors
    # caused by regions that were dropped during loading.
    if mm is not None and not diags.errors:
        analyze(mm, diags)
    return mm, diags
