"""In-memory representation of a parsed memory map."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import Loc


@dataclass
class Region:
    id: str
    name: str
    start: int
    size: int | None  # None: extends to the end of the parent / address space
    children: list[Region]
    loc: Loc
    field_locs: dict[str, Loc] = field(default_factory=dict)
    # Exclusive end address, filled in by semantic analysis.
    end: int | None = None

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
