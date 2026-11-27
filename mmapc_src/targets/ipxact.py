"""IP-XACT target: a single `<name>.ipxact.xml` component (IEEE 1685-2022).

The component holds one memory map, named after the design, that keeps the region
hierarchy:

- each top-level region becomes an `addressBlock` with its absolute `baseAddress`;
- each child region becomes a `registerFile` inside its parent, at an `addressOffset`
  relative to the parent's start, nesting to any depth. mmapc has no registers, so the
  register files hold only other register files (the schema allows them to be empty);
- names are plain region ids (IP-XACT names need only be unique among siblings, as mmapc
  ids are), descriptions are the region names, and `range` is the size in bytes
  (`addressUnitBits` 8). An `addressBlock` also needs a data `width`: 32 for address spaces
  up to 32 bits, 64 above.

Regions with children whose content is identical (the same children, by id, offset and
range, recursively, and the same range) get a common `typeIdentifier`, marking them as
instances of one type, e.g. a subsystem included twice. It is named like the shared SV
struct types: the id if all of them have the same id, else the first one's
`MemoryMap.unique_name`; types that would get the same name fall back to the latter.

The component's VLNV is `mmapc:mmapc:<name>:1.0`; `<name>` (`-n`) must be an `xs:Name`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from ..diagnostics import Diagnostics
from ..model import MemoryMap, Region
from .options import TargetOptions

NS = "http://www.accellera.org/XMLSchema/IPXACT/1685-2022"
SCHEMA_LOCATION = f"{NS} {NS}/index.xsd"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"
VENDOR = "mmapc"
LIBRARY = "mmapc"
VERSION = "1.0"

# xs:Name without the `:` (reserved for namespaces).
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")

# Content of a region with children: (range, ((child id, offset, child shape | range), ...)).
_Shape = tuple[int, tuple[tuple[str, int, "_Shape | int"], ...]]


def _q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, _q(tag))
    el.text = text
    return el


def _text(text: str) -> str:
    return " ".join(text.split())


class _Types:
    """`typeIdentifier` of each region with children whose content another one shares."""

    def __init__(self, mm: MemoryMap) -> None:
        self.mm = mm
        self._shape: dict[int, _Shape] = {}  # id(region) -> shape
        parents: list[Region] = []

        def collect(regions: list[Region]) -> None:
            for r in regions:
                collect(r.children)
                if r.children:
                    self._shape[id(r)] = (
                        r.end - r.start,
                        tuple(
                            (c.id, c.start - r.start, self._shape.get(id(c), c.end - c.start))
                            for c in r.children
                        ),
                    )
                    parents.append(r)

        collect(mm.regions)
        groups: dict[_Shape, list[Region]] = {}
        for r in parents:
            groups.setdefault(self._shape[id(r)], []).append(r)
        shared = [g for g in groups.values() if len(g) > 1]
        ambiguous = mm.ambiguous_ids()
        preferred = [
            g[0].id if all(r.id == g[0].id for r in g) else mm.unique_name(g[0], "_", ambiguous)
            for g in shared
        ]
        self._name: dict[_Shape, str] = {}
        for g, name in zip(shared, preferred, strict=True):
            if preferred.count(name) > 1:
                name = mm.unique_name(g[0], "_", ambiguous)
            self._name[self._shape[id(g[0])]] = name

    def of(self, r: Region) -> str | None:
        shape = self._shape.get(id(r))
        return self._name.get(shape) if shape is not None else None


def generate(mm: MemoryMap, diags: Diagnostics, opts: TargetOptions) -> dict[PurePosixPath, str]:
    if not _NAME_RE.fullmatch(opts.name):
        diags.error(
            "ipxact-bad-name",
            f"component name '{opts.name}' is not a valid IP-XACT name (xs:Name, see --name)",
            None,
        )
        return {}

    types = _Types(mm)
    digits = (mm.address_bits + 3) // 4

    def addr(value: int) -> str:
        return f"'h{value:0{digits}X}"

    def size(value: int) -> str:
        return f"'h{value:X}"

    def name_group(el: ET.Element, r: Region) -> None:
        _sub(el, "name", r.id)
        _sub(el, "description", _text(r.name))

    def type_id(el: ET.Element, r: Region) -> None:
        t = types.of(r)
        if t is not None:
            _sub(el, "typeIdentifier", t)

    def register_files(parent_el: ET.Element, parent: Region) -> None:
        for c in sorted(parent.children, key=lambda c: c.start):
            el = ET.SubElement(parent_el, _q("registerFile"))
            name_group(el, c)
            _sub(el, "addressOffset", size(c.start - parent.start))
            type_id(el, c)
            _sub(el, "range", size(c.end - c.start))
            register_files(el, c)

    ET.register_namespace("ipxact", NS)
    ET.register_namespace("xsi", _XSI)
    comp = ET.Element(_q("component"), {f"{{{_XSI}}}schemaLocation": SCHEMA_LOCATION})
    _sub(comp, "vendor", VENDOR)
    _sub(comp, "library", LIBRARY)
    _sub(comp, "name", opts.name)
    _sub(comp, "version", VERSION)
    _sub(comp, "description", f"{opts.name} memory map")
    maps = ET.SubElement(comp, _q("memoryMaps"))
    mmap = ET.SubElement(maps, _q("memoryMap"))
    _sub(mmap, "name", opts.name)
    for r in sorted(mm.regions, key=lambda r: r.start):
        block = ET.SubElement(mmap, _q("addressBlock"))
        name_group(block, r)
        _sub(block, "baseAddress", addr(r.start))
        type_id(block, r)
        _sub(block, "range", size(r.end - r.start))
        _sub(block, "width", "32" if mm.address_bits <= 32 else "64")
        register_files(block, r)
    _sub(mmap, "addressUnitBits", "8")

    ET.indent(comp, space="  ")
    top = next(iter(mm.files), None)
    source = (top.name if top else "the memory map").replace("--", "- -")  # no "--" in comments
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<!-- Generated by mmapc from {source}. Do not edit. -->\n"
        + ET.tostring(comp, encoding="unicode")
        + "\n"
    )
    return {PurePosixPath(f"{opts.name}.ipxact.xml"): text}
