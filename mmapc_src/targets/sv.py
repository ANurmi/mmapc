"""SystemVerilog target: a single `<name>_address_map_pkg` package.

For each region the package has:
- an `addr_rule_t` localparam `<Id>Addr = '{start_addr, end_addr}`, following the PULP
  AXI rule naming. `end_addr` is exclusive (start + size). A region reaching the top of
  the address space has an end of 2**AddrWidth, which truncates to `end_addr = 0`;
- if it has children, a packed struct type `<id>_map_t`
  {start_addr, end_addr, num, <child>...}
  and a localparam `<Id>Map`, where a leaf child is an `addr_rule_t` and a parent child
  is its own `<child>_map_t`.
The top-level `addr_map_t` / `AddressMap` holds `num` and the top-level regions.
Ids need only be unique among siblings. Struct fields are named by plain id (the struct
nesting follows the hierarchy), but the package-wide localparam and type names of a region
whose id is used more than once are qualified with its path: `periph.uart` ->
`PeriphUartAddr`, and `periph_uart_map_t` if it has children.
Regions and fields keep source (definition) order; types and maps are emitted bottom-up
so every type is declared before use.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..diagnostics import Diagnostics
from ..model import MemoryMap, Region
from .options import TargetOptions

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")

# IEEE 1800-2017 Annex B reserved keywords.
SV_KEYWORDS = frozenset(
    """
    accept_on alias always always_comb always_ff always_latch and assert assign assume
    automatic before begin bind bins binsof bit break buf bufif0 bufif1 byte case casex
    casez cell chandle checker class clocking cmos config const constraint context
    continue cover covergroup coverpoint cross deassign default defparam design disable
    dist do edge else end endcase endchecker endclass endclocking endconfig endfunction
    endgenerate endgroup endinterface endmodule endpackage endprimitive endprogram
    endproperty endspecify endsequence endtable endtask enum event eventually expect
    export extends extern final first_match for force foreach forever fork forkjoin
    function generate genvar global highz0 highz1 if iff ifnone ignore_bins
    illegal_bins implements implies import incdir include initial inout input inside
    instance int integer interconnect interface intersect join join_any join_none
    large let liblist library local localparam logic longint macromodule matches
    medium modport module nand negedge nettype new nexttime nmos nor noshowcancelled
    not notif0 notif1 null or output package packed parameter pmos posedge primitive
    priority program property protected pull0 pull1 pulldown pullup
    pulsestyle_ondetect pulsestyle_onevent pure rand randc randcase randsequence rcmos
    real realtime ref reg reject_on release repeat restrict return rnmos rpmos rtran
    rtranif0 rtranif1 s_always s_eventually s_nexttime s_until s_until_with scalared
    sequence shortint shortreal showcancelled signed small soft solve specify
    specparam static string strong strong0 strong1 struct super supply0 supply1
    sync_accept_on sync_reject_on table tagged task this throughout time timeprecision
    timeunit tran tranif0 tranif1 tri tri0 tri1 triand trior trireg type typedef union
    unique unique0 unsigned until until_with untyped use uwire var vectored virtual
    void wait wait_order wand weak weak0 weak1 while wildcard wire with within wor
    xnor xor
    """.split()
)

# Field names every `<id>_map_t` already has.
_MAP_FIELDS = ("start_addr", "end_addr", "num")


def camel(rid: str) -> str:
    """`i2c0` -> `I2c0`, `dma_ctrl` -> `DmaCtrl`."""
    return "".join(p[:1].upper() + p[1:] for p in rid.split("_") if p)


def sv_hex(value: int, bits: int) -> str:
    """`32'h0003_3000`: zero-padded to the address width, `_` every 4 digits."""
    digits = f"{value:0{(bits + 3) // 4}X}"
    groups = []
    while digits:
        groups.insert(0, digits[-4:])
        digits = digits[:-4]
    return f"{bits}'h{'_'.join(groups)}"


def _comment(text: str) -> str:
    return " ".join(text.splitlines())


class _Names:
    """Package-wide names of each region; see the module docstring."""

    def __init__(self, mm: MemoryMap) -> None:
        self.mm = mm
        self.ambiguous = mm.ambiguous_ids()

    def base(self, r: Region) -> str:
        return self.mm.unique_name(r, "_", self.ambiguous)

    def addr(self, r: Region) -> str:
        return f"{camel(self.base(r))}Addr"

    def map(self, r: Region) -> str:
        return f"{camel(self.base(r))}Map"

    def type(self, r: Region) -> str:
        return f"{self.base(r)}_map_t"


def _check_names(mm: MemoryMap, pkg: str, names: _Names, diags: Diagnostics) -> None:
    if not _IDENT_RE.fullmatch(pkg) or pkg in SV_KEYWORDS:
        diags.error(
            "sv-bad-name",
            f"package name '{pkg}' is not a valid SystemVerilog identifier (see --name)",
            None,
        )
    reserved_types = {"addr_rule_t", "addr_map_t"}
    params: dict[str, Region] = {}
    types: dict[str, Region] = {}
    for r in mm.walk():
        if r.id in SV_KEYWORDS:
            diags.error(
                "sv-keyword",
                f"region id '{r.id}' ('{r.qualname}') is a SystemVerilog keyword and cannot "
                "be a struct field",
                r.at("id"),
            )
        for child in r.children:
            if child.id in _MAP_FIELDS:
                diags.error(
                    "sv-name-clash",
                    f"region id '{child.id}' clashes with the '{child.id}' field of "
                    f"'{names.type(r)}'",
                    child.at("id"),
                )
        pnames = [names.addr(r)] + ([names.map(r)] if r.children else [])
        if "AddressMap" in pnames:
            diags.error(
                "sv-name-clash",
                f"region '{r.qualname}' would generate 'AddressMap', which is reserved",
                r.at("id"),
            )
        for name in pnames:
            if name in params:
                other = params[name]
                diags.error(
                    "sv-name-clash",
                    f"regions '{other.qualname}' and '{r.qualname}' both generate localparam "
                    f"'{name}'",
                    r.at("id"),
                    [(other.at("id"), f"'{other.qualname}' defined here")],
                )
            else:
                params[name] = r
        if r.children:
            tname = names.type(r)
            if tname in reserved_types or tname in types:
                diags.error(
                    "sv-name-clash",
                    f"region '{r.qualname}' would generate type '{tname}', which is already used",
                    r.at("id"),
                )
            types[tname] = r


def _struct(name: str, fields: list[tuple[str, str]]) -> list[str]:
    type_w = max(len(t) for t, _ in fields)
    body = [f"    {t:<{type_w}} {n};" for t, n in fields]
    return ["  typedef struct packed {", *body, f"  }} {name};", ""]


def _assign(type_name: str, param: str, items: list[tuple[str, str]]) -> list[str]:
    key_w = max(len(k) for k, _ in items) + 1
    body = [f"    {k + ':':<{key_w}} {v}" for k, v in items]
    body = [line + "," for line in body[:-1]] + body[-1:]
    return [f"  localparam {type_name} {param} = '{{", *body, "  };", ""]


def generate(mm: MemoryMap, diags: Diagnostics, opts: TargetOptions) -> dict[PurePosixPath, str]:
    pkg = f"{opts.name}_address_map_pkg"
    names = _Names(mm)
    _check_names(mm, pkg, names, diags)
    if diags.errors:
        return {}

    bits = mm.address_bits
    addr_t = "logic [AddrWidth-1:0]"

    def field_type(r: Region) -> str:
        return names.type(r) if r.children else "addr_rule_t"

    def field_value(r: Region) -> str:
        return names.map(r) if r.children else names.addr(r)

    # Bottom-up (post-order) list of regions with children, in source order.
    parents: list[Region] = []

    def collect(regions: list[Region]) -> None:
        for r in regions:
            collect(r.children)
            if r.children:
                parents.append(r)

    collect(mm.regions)

    source = mm.loc.path.name
    lines = [
        f"// Generated by mmapc from {source}.",
        "// DO NOT EDIT: edit the memory map sources and regenerate instead.",
        "",
        f"package {pkg};",
        "",
        f"  localparam int unsigned AddrWidth = {bits};",
        "",
    ]
    lines += _struct("addr_rule_t", [(addr_t, "start_addr"), (addr_t, "end_addr")])
    for p in parents:
        fields = [(addr_t, "start_addr"), (addr_t, "end_addr"), ("int unsigned", "num")]
        fields += [(field_type(c), c.id) for c in p.children]
        lines += _struct(names.type(p), fields)
    lines += _struct(
        "addr_map_t", [("int unsigned", "num")] + [(field_type(r), r.id) for r in mm.regions]
    )

    regions = list(mm.walk())
    if regions:
        name_w = max(len(names.addr(r)) for r in regions)
        space_end = 1 << bits
        for r in regions:
            # Exclusive end; 2**AddrWidth does not fit and truncates to 0.
            end = r.end % space_end
            rule = f"'{{start_addr: {sv_hex(r.start, bits)}, end_addr: {sv_hex(end, bits)}}}"
            note = " (end_addr 0: extends to the top of the address space)" if end == 0 else ""
            lines.append(
                f"  localparam addr_rule_t {names.addr(r):<{name_w}} = {rule};"
                f"  // {_comment(r.name)}{note}"
            )
        lines.append("")

    for p in parents:
        addr = names.addr(p)
        items = [
            ("start_addr", f"{addr}.start_addr"),
            ("end_addr", f"{addr}.end_addr"),
            ("num", str(len(p.children))),
        ]
        items += [(c.id, field_value(c)) for c in p.children]
        lines += _assign(names.type(p), names.map(p), items)
    lines += _assign(
        "addr_map_t",
        "AddressMap",
        [("num", str(len(mm.regions)))] + [(r.id, field_value(r)) for r in mm.regions],
    )

    lines += [f"endpackage : {pkg}", ""]
    return {PurePosixPath(f"{pkg}.sv"): "\n".join(lines)}
