"""Semantic phase: address-space layout checks on a loaded MemoryMap.

Also resolves each region's exclusive `end` address (open-ended regions, i.e. those
without `size`, extend to the end of their parent or of the address space).
"""

from __future__ import annotations

from .diagnostics import Diagnostics
from .model import MemoryMap, Region


class _Analyzer:
    def __init__(self, mm: MemoryMap, diags: Diagnostics) -> None:
        self.mm = mm
        self.diags = diags
        self.width = (mm.address_bits + 3) // 4

    def hex(self, value: int) -> str:
        return f"0x{value:0{self.width}X}"

    def span(self, lo: int, hi: int) -> str:
        return f"[{self.hex(lo)}, {self.hex(hi)})"

    def run(self) -> None:
        space_end = 1 << self.mm.address_bits
        self.layout(self.mm.regions, 0, space_end, None)
        self.unique()

    def layout(self, regions: list[Region], lo: int, hi: int, parent: Region | None) -> None:
        container = (
            f"parent '{parent.id}' {self.span(lo, hi)}"
            if parent
            else f"the {self.mm.address_bits}-bit address space"
        )
        for r in regions:
            if not lo <= r.start < hi:
                self.diags.error(
                    "out-of-bounds",
                    f"region '{r.id}' starts at {self.hex(r.start)}, outside {container}",
                    r.at("start"),
                )
                continue
            if r.size is None:
                r.end = hi
            elif r.size == 0:
                self.diags.error("zero-size", f"region '{r.id}' has size 0", r.at("size"))
                continue
            else:
                r.end = r.start + r.size
                if r.end > hi:
                    self.diags.error(
                        "out-of-bounds",
                        f"region '{r.id}' {self.span(r.start, r.end)} extends past the end "
                        f"of {container}",
                        r.at("size"),
                    )
            self.layout(r.children, r.start, r.end, r)
        self.overlaps(regions)

    def overlaps(self, regions: list[Region]) -> None:
        placed = sorted((r for r in regions if r.end is not None), key=lambda r: r.start)
        if not placed:
            return
        reach = placed[0]  # sibling with the highest end seen so far
        for r in placed[1:]:
            if r.start < reach.end:
                note = f"'{reach.id}' occupies {self.span(reach.start, reach.end)}"
                if reach.size is None:
                    note += " (no size given, so it extends to the end of its container)"
                self.diags.error(
                    "overlap",
                    f"region '{r.id}' {self.span(r.start, r.end)} overlaps '{reach.id}'",
                    r.at("start"),
                    [(reach.loc, note)],
                )
            if r.end > reach.end:
                reach = r

    def unique(self) -> None:
        by_id: dict[str, Region] = {}
        by_name: dict[str, Region] = {}
        for r in self.mm.walk():
            if r.id in by_id:
                self.diags.error(
                    "duplicate-id",
                    f"duplicate region id '{r.id}'",
                    r.at("id"),
                    [(by_id[r.id].at("id"), "first defined here")],
                )
            else:
                by_id[r.id] = r
            if r.name in by_name:
                self.diags.warning(
                    "duplicate-name",
                    f"region '{r.id}' reuses name '{r.name}'",
                    r.at("name"),
                    [(by_name[r.name].at("name"), f"also used by '{by_name[r.name].id}'")],
                )
            else:
                by_name[r.name] = r


def analyze(mm: MemoryMap, diags: Diagnostics) -> None:
    _Analyzer(mm, diags).run()
