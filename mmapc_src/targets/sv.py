"""SystemVerilog target: a single `<name>_address_map_pkg` package.

For each region the package has:
- an `addr_rule_t` localparam `<Id>Addr = '{base, last}` (`last` is the inclusive last
  address; open-ended regions run to the top of the address space, so an exclusive end
  could not be represented in AddrWidth bits);
- if it has children, a packed struct type `<id>_map_t` {base, last, num, <child>...}
  and a localparam `<Id>Map`, where a leaf child is an `addr_rule_t` and a parent child
  is its own `<child>_map_t`.
The top-level `addr_map_t` / `AddressMap` holds `num` and the top-level regions.
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
_MAP_FIELDS = ("base", "last", "num")


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


def _check_names(mm: MemoryMap, pkg: str, diags: Diagnostics) -> None:
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
                f"region id '{r.id}' is a SystemVerilog keyword and cannot be a struct field",
                r.at("id"),
            )
        for child in r.children:
            if child.id in _MAP_FIELDS:
                diags.error(
                    "sv-name-clash",
                    f"region id '{child.id}' clashes with the '{child.id}' field of '{r.id}_map_t'",
                    child.at("id"),
                )
        names = [f"{camel(r.id)}Addr"] + ([f"{camel(r.id)}Map"] if r.children else [])
        if "AddressMap" in names:
            diags.error(
                "sv-name-clash",
                f"region id '{r.id}' would generate 'AddressMap', which is reserved",
                r.at("id"),
            )
        for name in names:
            if name in params:
                other = params[name]
                diags.error(
                    "sv-name-clash",
                    f"region ids '{other.id}' and '{r.id}' both generate localparam '{name}'",
                    r.at("id"),
                    [(other.at("id"), f"'{other.id}' defined here")],
                )
            else:
                params[name] = r
        if r.children:
            tname = f"{r.id}_map_t"
            if tname in reserved_types or tname in types:
                diags.error(
                    "sv-name-clash",
                    f"region id '{r.id}' would generate type '{tname}', which is already used",
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
    _check_names(mm, pkg, diags)
    if diags.errors:
        return {}

    bits = mm.address_bits
    addr_t = "logic [AddrWidth-1:0]"

    def field_type(r: Region) -> str:
        return f"{r.id}_map_t" if r.children else "addr_rule_t"

    def field_value(r: Region) -> str:
        return f"{camel(r.id)}Map" if r.children else f"{camel(r.id)}Addr"

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
    lines += _struct("addr_rule_t", [(addr_t, "base"), (addr_t, "last")])
    for p in parents:
        fields = [(addr_t, "base"), (addr_t, "last"), ("int unsigned", "num")]
        fields += [(field_type(c), c.id) for c in p.children]
        lines += _struct(f"{p.id}_map_t", fields)
    lines += _struct(
        "addr_map_t", [("int unsigned", "num")] + [(field_type(r), r.id) for r in mm.regions]
    )

    regions = list(mm.walk())
    if regions:
        name_w = max(len(f"{camel(r.id)}Addr") for r in regions)
        for r in regions:
            rule = f"'{{base: {sv_hex(r.start, bits)}, last: {sv_hex(r.end - 1, bits)}}}"
            lines.append(
                f"  localparam addr_rule_t {camel(r.id) + 'Addr':<{name_w}} = {rule};"
                f"  // {_comment(r.name)}"
            )
        lines.append("")

    for p in parents:
        addr = f"{camel(p.id)}Addr"
        items = [("base", f"{addr}.base"), ("last", f"{addr}.last"), ("num", str(len(p.children)))]
        items += [(c.id, field_value(c)) for c in p.children]
        lines += _assign(f"{p.id}_map_t", f"{camel(p.id)}Map", items)
    lines += _assign(
        "addr_map_t",
        "AddressMap",
        [("num", str(len(mm.regions)))] + [(r.id, field_value(r)) for r in mm.regions],
    )

    lines += [f"endpackage : {pkg}", ""]
    return {PurePosixPath(f"{pkg}.sv"): "\n".join(lines)}
