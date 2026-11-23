"""Documentation target: a config for the `memmap` drawing tool (github.com/iharhl/memmap).

Writes one `<name>_memmap.yaml`. Render it with `memmap render <name>_memmap.yaml -o map.svg`.

The `memory` section is a direct translation of the analyzed map: every region gets an explicit
`start` and `size` (from `Region.end`), so memmap never has to place anything itself. By
default the drawing has a title and zooms into every region with children (`view.expand`).

An optional directives file (`-d/--directives`) carries memmap-specific settings that have no
place in the mmapc sources:

    figure: { title: SoC memory map, footer: DOC-1 }   # merged over the defaults
    view:   { expand: [periph] }                       # merged over the defaults
    style:  { palette: datasheet }
    memory: { check_alignment: true }                  # memmap memory options
    regions:                                           # extra keys per mmapc region id
      bootrom: { color: "#ffe0b0", note: Read-only }

`figure`, `view` and `style` are deep-merged over the generated defaults (mappings merge,
anything else, including lists, is replaced) and passed on unchecked: memmap validates them.
`memory` and `regions` may only add display keys, never change the geometry, so the drawing
always matches the sources.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode

from ..diagnostics import Diagnostics
from ..loader import SourceLoader
from ..model import MemoryMap, Region
from .options import TargetOptions

MEMMAP_VERSION = 1

_DIRECTIVE_KEYS = {"figure", "view", "style", "memory", "regions"}
# memmap `memory` options that don't affect where regions are.
_MEMORY_KEYS = {"check_alignment", "fill_gaps"}
# memmap region keys a directive may add or override; geometry (start, size, end, align,
# children, space) and the id always come from the sources.
_REGION_KEYS = {"name", "color", "note", "kind", "sectors"}


class _Addr(int):
    """An address, written as zero-padded hex."""

    width: int

    def __new__(cls, value: int, width: int) -> _Addr:
        obj = super().__new__(cls, value)
        obj.width = width
        return obj


class _Dumper(yaml.SafeDumper):
    pass


# Multi-line strings (e.g. a `name` with a forced line break) stay on one line, escaped.
_Dumper.add_representer(
    str,
    lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style='"' if "\n" in v else None),
)
_Dumper.add_representer(
    _Addr,
    lambda d, v: d.represent_scalar("tag:yaml.org,2002:int", f"0x{int(v):0{v.width}X}"),
)


def _size(value: int) -> int | str:
    """`12K`/`512K`/`8G` when exact (memmap's suffixes are powers of 1024), else hex or decimal."""
    for suffix, unit in (("G", 1 << 30), ("M", 1 << 20), ("K", 1 << 10)):
        if value % unit == 0 and 1 <= value // unit < 1024:
            return f"{value // unit}{suffix}"
    return _Addr(value, 0) if value >= 1024 else value


def _merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base[k], v) if k in base else v
        return out
    return over


def _construct(node: Node) -> Any:
    loader = yaml.SafeLoader("")
    try:
        return loader.construct_document(node)
    finally:
        loader.dispose()


def _fields(
    sl: SourceLoader, node: Node, path: Path, what: str, allowed: set[str] | None
) -> dict[str, tuple[Node, Node]]:
    """Like `SourceLoader._mapping`, but reports every unknown key and keeps the known ones,
    so one typo doesn't hide the rest of the file's errors. `allowed=None` allows any key."""
    if not isinstance(node, MappingNode):
        sl._mapping(node, path, what, set(), set())  # reports "must be a mapping"
        return {}
    keys = {k.value for k, _ in node.value if isinstance(k, ScalarNode)}
    entries = sl._mapping(node, path, what, keys, set())
    if entries is None:  # duplicate or non-scalar keys
        return {}
    if allowed is None:
        return entries
    out = {}
    for key, (key_node, value_node) in entries.items():
        if key in allowed:
            out[key] = (key_node, value_node)
        else:
            expected = ", ".join(sorted(allowed))
            sl.diags.error(
                "unknown-key",
                f"{what}: unknown key '{key}' (expected one of: {expected})",
                sl._loc(key_node, path),
            )
    return out


def _load_directives(path: Path, mm: MemoryMap, diags: Diagnostics) -> dict[str, Any] | None:
    """Read and check a directives file; returns None (with diagnostics) when it is invalid."""
    n_err = len(diags.errors)
    sl = SourceLoader(diags)
    root = sl._compose(path, None)
    if root is None:
        return None
    ids = {r.id for r in mm.walk()}
    out: dict[str, Any] = {}
    for key, (_, value) in _fields(sl, root, path, "directives", _DIRECTIVE_KEYS).items():
        if key in ("figure", "view", "style"):
            # memmap validates these itself; only the shape is checked here.
            fields = _fields(sl, value, path, f"directives: {key}", None)
            out[key] = {k: _construct(v) for k, (_, v) in fields.items()}
        elif key == "memory":
            fields = _fields(sl, value, path, "directives: memory", _MEMORY_KEYS)
            out[key] = {k: _construct(v) for k, (_, v) in fields.items()}
        else:  # regions
            regions: dict[str, dict[str, Any]] = {}
            for rid, (key_node, rnode) in _fields(
                sl, value, path, "directives: regions", None
            ).items():
                if rid not in ids:
                    diags.error(
                        "doc-unknown-region",
                        f"directives: regions: no region with id '{rid}' in the memory map",
                        sl._loc(key_node, path),
                    )
                    continue
                what = f"directives: regions: {rid}"
                fields = _fields(sl, rnode, path, what, _REGION_KEYS)
                regions[rid] = {k: _construct(v) for k, (_, v) in fields.items()}
            out[key] = regions
    return None if len(diags.errors) > n_err else out


def _region(r: Region, width: int, extra: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "name": r.name,
        "start": _Addr(r.start, width),
        "size": _size(r.end - r.start),
    }
    out.update(extra.get(r.id, {}))
    if r.children:
        kids = sorted(r.children, key=lambda c: c.start)
        out["children"] = [_region(c, width, extra) for c in kids]
    return out


def generate(mm: MemoryMap, diags: Diagnostics, opts: TargetOptions) -> dict[PurePosixPath, str]:
    directives: dict[str, Any] = {}
    if opts.directives is not None:
        loaded = _load_directives(opts.directives, mm, diags)
        if loaded is None:
            return {}
        directives = loaded

    width = (mm.address_bits + 3) // 4
    extra = directives.get("regions", {})
    regions = sorted(mm.regions, key=lambda r: r.start)
    memory: dict[str, Any] = {"address_bits": mm.address_bits}
    memory.update(directives.get("memory", {}))
    memory["regions"] = [_region(r, width, extra) for r in regions]

    # Zoom into every region with children, in address order, top level first.
    expand = [r.id for r in sorted(mm.walk(), key=lambda r: r.start) if r.children]
    view: dict[str, Any] = {"expand": expand} if expand else {}

    config: dict[str, Any] = {
        "version": MEMMAP_VERSION,
        "figure": _merge({"title": f"{opts.name} memory map"}, directives.get("figure", {})),
        "memory": memory,
    }
    view = _merge(view, directives.get("view", {}))
    if view:
        config["view"] = view
    if "style" in directives:
        config["style"] = directives["style"]

    rel = PurePosixPath(f"{opts.name}_memmap.yaml")
    top = next(iter(mm.files), None)
    header = [f"# Generated by mmapc from {top.name if top else 'the memory map'}. Do not edit."]
    if opts.directives is not None:
        header.append(f"# Directives: {opts.directives.name}")
    header.append(f"# Render with: memmap render {rel} -o {opts.name}_memmap.svg")
    body = yaml.dump(
        config, Dumper=_Dumper, sort_keys=False, default_flow_style=None, width=100,
        allow_unicode=True,
    )  # fmt: skip
    return {rel: "\n".join(header) + "\n" + body}
