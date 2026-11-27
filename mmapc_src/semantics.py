"""Semantic phase: address-space layout checks on a loaded MemoryMap.

Also resolves each region's exclusive `end` address (open-ended regions, i.e. those
without `size`, extend to the end of their parent or of the address space), and the
`start` of children that omit it: such a child is placed right after its preceding
sibling in source order, or at its parent's start if it is the first child.
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
        self.set_paths(self.mm.regions, ())
        self.layout(self.mm.regions, 0, space_end, None)
        self.unique(self.mm.regions)

    def set_paths(self, regions: list[Region], prefix: tuple[str, ...]) -> None:
        for r in regions:
            r.path = (*prefix, r.id)
            self.set_paths(r.children, r.path)

    def layout(self, regions: list[Region], lo: int, hi: int, parent: Region | None) -> None:
        container = (
            f"parent '{parent.qualname}' {self.span(lo, hi)}"
            if parent
            else f"the {self.mm.address_bits}-bit address space"
        )
        cursor: int | None = lo  # end of the previous sibling, in source order
        prev: Region | None = None
        for r in regions:
            how = ""
            if r.implicit_start:
                if cursor is None:
                    # The previous sibling could not be placed (already reported).
                    prev = r
                    continue
                r.start = cursor
                how = f" (implicitly, after '{prev.id}')" if prev else " (implicitly)"
            prev, cursor = r, None
            if not lo <= r.start < hi:
                self.diags.error(
                    "out-of-bounds",
                    f"region '{r.qualname}' starts at {self.hex(r.start)}{how}, "
                    f"outside {container}",
                    r.at("start"),
                )
                continue
            if r.size is None:
                r.end = hi
            elif r.size == 0:
                self.diags.error("zero-size", f"region '{r.qualname}' has size 0", r.at("size"))
                continue
            else:
                r.end = r.start + r.size
                if r.end > hi:
                    self.diags.error(
                        "out-of-bounds",
                        f"region '{r.qualname}' {self.span(r.start, r.end)}{how} extends past the "
                        f"end of {container}",
                        r.at("size"),
                    )
            # Don't place a following implicit sibling after a region that already overflows.
            cursor = r.end if r.end <= hi else None
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
                    f"region '{r.qualname}' {self.span(r.start, r.end)} overlaps '{reach.id}'",
                    r.at("start"),
                    [(reach.loc, note)],
                )
            if r.end > reach.end:
                reach = r

    def unique(self, regions: list[Region]) -> None:
        """Ids (and, as a warning, names) must be unique among siblings. The same id may
        appear under different parents: the hierarchy tells them apart."""
        by_id: dict[str, Region] = {}
        by_name: dict[str, Region] = {}
        for r in regions:
            if r.id in by_id:
                self.diags.error(
                    "duplicate-id",
                    f"duplicate region id '{r.qualname}' (ids must be unique among siblings)",
                    r.at("id"),
                    [(by_id[r.id].at("id"), "first defined here")],
                )
            else:
                by_id[r.id] = r
            if r.name in by_name:
                self.diags.warning(
                    "duplicate-name",
                    f"region '{r.qualname}' reuses name '{r.name}' of a sibling",
                    r.at("name"),
                    [(by_name[r.name].at("name"), f"also used by '{by_name[r.name].id}'")],
                )
            else:
                by_name[r.name] = r
            self.unique(r.children)


def analyze(mm: MemoryMap, diags: Diagnostics) -> None:
    _Analyzer(mm, diags).run()
