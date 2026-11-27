"""In-memory representation of a parsed memory map."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import Loc


@dataclass
class Region:
    id: str
    name: str
    # Base address. A child may omit `start` in the source; the loader then leaves this
    # None and semantic analysis fills it in (`implicit_start` records that it did), so it
    # is always set on an analyzed map.
    start: int | None
    size: int | None  # None: extends to the end of the parent / address space
    children: list[Region]
    loc: Loc
    field_locs: dict[str, Loc] = field(default_factory=dict)
    # Exclusive end address, filled in by semantic analysis.
    end: int | None = None
    # True when `start` was not given in the source and was resolved automatically.
    implicit_start: bool = False
    # Ids from the top level down to this region, e.g. ("periph", "uart"). Ids are only
    # unique among siblings, so the path is what identifies a region. Set by semantic
    # analysis.
    path: tuple[str, ...] = ()

    @property
    def qualname(self) -> str:
        """Dotted hierarchical name, e.g. `periph.uart`."""
        return ".".join(self.path or (self.id,))

    def at(self, key: str) -> Loc:
        return self.field_locs.get(key, self.loc)


@dataclass
class MemoryMap:
    address_bits: int
    regions: list[Region]
    loc: Loc
    # Include graph: source file -> files it includes, in order. The top file is first.
    # Each region belongs to the file it is written in (`Region.loc.path`).
    files: dict[Path, list[Path]] = field(default_factory=dict)

    def walk(self) -> Iterator[Region]:
        stack = list(reversed(self.regions))
        while stack:
            r = stack.pop()
            yield r
            stack.extend(reversed(r.children))

    def ambiguous_ids(self) -> set[str]:
        """Ids used by more than one region (necessarily under different parents)."""
        counts = Counter(r.id for r in self.walk())
        return {rid for rid, n in counts.items() if n > 1}

    def unique_name(self, r: Region, sep: str = "_", ambiguous: set[str] | None = None) -> str:
        """A map-wide unique name for `r` for flat namespaces (C macros, SV localparams):
        its id when no other region uses it, else its path joined with `sep`. Pass
        `ambiguous` (from `ambiguous_ids`) to avoid recomputing it per region."""
        if ambiguous is None:
            ambiguous = self.ambiguous_ids()
        return sep.join(r.path) if r.id in ambiguous else r.id
