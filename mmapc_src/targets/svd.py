"""CMSIS-SVD target: a single `<name>.svd` device description (schema version 1.3).

SVD describes a device as a flat list of peripherals, each with a base address and address
blocks (and, normally, registers, which mmapc does not model). The memory map is mapped
onto it like this:

- every leaf region (one without children) becomes a `<peripheral>` named by
  `MemoryMap.unique_name` in upper case (`uart`, or `SUB_A_UART` if the id is ambiguous),
  with its `name` as `description` and one `addressBlock` covering the whole region
  (`usage` registers). Regions with children don't become peripherals, since they would
  overlap their own children; instead each leaf's `groupName` is its parent's name, so
  CMSIS tools group the peripherals of one subsystem together. Top-level leaves have no
  group;
- a leaf with the same id and size as an earlier one (e.g. the same subsystem file
  included under two parents) is emitted as `derivedFrom` that first one, giving only its
  own name, base address, and the description/group where they differ;
- peripherals are listed in address order, so a base always precedes its derivatives.

The device `name` is the design name (`-n`), `width` is 32 for address spaces up to 32 bits
and 64 above, and `addressUnitBits` is 8.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath

from ..diagnostics import Diagnostics
from ..model import MemoryMap, Region
from .options import TargetOptions

SCHEMA_VERSION = "1.3"
DEVICE_VERSION = "1.0"

# SVD `identifierType`, minus the empty string.
_IDENT_RE = re.compile(r"[_A-Za-z0-9]+")


def _text(text: str) -> str:
    return " ".join(text.split())


def _sub(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = text
    return el


def generate(mm: MemoryMap, diags: Diagnostics, opts: TargetOptions) -> dict[PurePosixPath, str]:
    if not _IDENT_RE.fullmatch(opts.name):
        diags.error(
            "svd-bad-name",
            f"device name '{opts.name}' is not a valid SVD identifier ([_A-Za-z0-9]+, see --name)",
            None,
        )

    ambiguous = mm.ambiguous_ids()

    def pname(r: Region) -> str:
        return mm.unique_name(r, "_", ambiguous).upper()

    # Leaves in address order, each with its parent (None at the top level).
    leaves: list[tuple[Region, Region | None]] = []

    def collect(regions: list[Region], parent: Region | None) -> None:
        for r in sorted(regions, key=lambda r: r.start):
            if r.children:
                collect(r.children, r)
            else:
                leaves.append((r, parent))

    collect(mm.regions, None)
    if not leaves:
        diags.error("svd-empty", "the memory map has no regions to describe as peripherals", None)

    by_name: dict[str, Region] = {}
    for r, _ in leaves:
        name = pname(r)
        if name in by_name:
            other = by_name[name]
            diags.error(
                "svd-name-clash",
                f"regions '{other.qualname}' and '{r.qualname}' both map to SVD peripheral "
                f"'{name}'",
                r.at("id"),
                [(other.at("id"), f"'{other.qualname}' defined here")],
            )
        else:
            by_name[name] = r
    if diags.errors:
        return {}

    width = (mm.address_bits + 3) // 4

    def hexa(value: int) -> str:
        return f"0x{value:0{width}X}"

    device = ET.Element("device", {"schemaVersion": SCHEMA_VERSION})
    _sub(device, "name", opts.name)
    _sub(device, "version", DEVICE_VERSION)
    _sub(device, "description", f"{opts.name} memory map")
    _sub(device, "addressUnitBits", "8")
    _sub(device, "width", "32" if mm.address_bits <= 32 else "64")
    peripherals = ET.SubElement(device, "peripherals")

    # (id, size) -> (first leaf with that id and size, its parent)
    bases: dict[tuple[str, int], tuple[Region, Region | None]] = {}
    for r, parent in leaves:
        size = r.end - r.start
        group = pname(parent) if parent else None
        base = bases.setdefault((r.id, size), (r, parent))
        el = ET.SubElement(peripherals, "peripheral")
        if base[0] is not r:
            base_r, base_parent = base
            el.set("derivedFrom", pname(base_r))
            _sub(el, "name", pname(r))
            if _text(r.name) != _text(base_r.name):
                _sub(el, "description", _text(r.name))
            if group and group != (pname(base_parent) if base_parent else None):
                _sub(el, "groupName", group)
            _sub(el, "baseAddress", hexa(r.start))
            continue
        _sub(el, "name", pname(r))
        _sub(el, "description", _text(r.name))
        if group:
            _sub(el, "groupName", group)
        _sub(el, "baseAddress", hexa(r.start))
        block = ET.SubElement(el, "addressBlock")
        _sub(block, "offset", "0x0")
        _sub(block, "size", f"0x{size:X}")
        _sub(block, "usage", "registers")

    ET.indent(device, space="  ")
    top = next(iter(mm.files), None)
    source = (top.name if top else "the memory map").replace("--", "- -")  # no "--" in comments
    text = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<!-- Generated by mmapc from {source}. Do not edit. -->\n"
        + ET.tostring(device, encoding="unicode")
        + "\n"
    )
    return {PurePosixPath(f"{opts.name}.svd"): text}
