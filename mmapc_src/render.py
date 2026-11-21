"""Human-readable text rendering of an analyzed memory map."""

from __future__ import annotations

from .model import MemoryMap, Region

_UNITS = (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10))

# Tree connectors, as drawn by `tree(1)`.
_BRANCH, _LAST = "├── ", "└── "
_PIPE, _BLANK = "│   ", "    "


def format_size(n: int) -> str:
    """Exact, compact size: 36, 4K, 192K, 1M; hex when no unit gives a value below 1024."""
    if n < 1024:
        return str(n)
    for suffix, mult in _UNITS:
        if n % mult == 0 and n // mult < 1024:
            return f"{n // mult}{suffix}"
    return f"0x{n:X}"


def render_map(mm: MemoryMap) -> str:
    """Render the map as a tree (siblings in address order) with aligned address columns.

    Requires semantic analysis to have run (uses `Region.end`). END is inclusive.
    """
    width = (mm.address_bits + 3) // 4
    rows: list[tuple[str, Region]] = []  # (tree prefix + id, region)

    def collect(regions: list[Region], indent: str) -> None:
        ordered = sorted(regions, key=lambda r: r.start)
        for i, r in enumerate(ordered):
            last = i == len(ordered) - 1
            rows.append((indent + (_LAST if last else _BRANCH) + r.id, r))
            collect(r.children, indent + (_BLANK if last else _PIPE))

    collect(mm.regions, "")

    def hx(v: int) -> str:
        return f"0x{v:0{width}X}"

    tree_w = max([2] + [len(t) for t, _ in rows])
    name_w = max([4] + [len(r.name) for _, r in rows])
    size_w = max([4] + [len(format_size(r.end - r.start)) for _, r in rows])
    addr_w = width + 2
    lines = [
        f"Memory map ({mm.address_bits}-bit address space)",
        f"{'ID':<{tree_w}}  {'NAME':<{name_w}}  {'START':<{addr_w}}  {'END':<{addr_w}}  "
        f"{'SIZE':>{size_w}}",
    ]
    for tree, r in rows:
        lines.append(
            f"{tree:<{tree_w}}  {r.name:<{name_w}}  {hx(r.start)}  {hx(r.end - 1)}  "
            f"{format_size(r.end - r.start):>{size_w}}".rstrip()
        )
    return "\n".join(lines)
