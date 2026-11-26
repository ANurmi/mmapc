"""Syntactic phase: YAML -> MemoryMap, resolving includes and validating the schema.

Works on the YAML node graph (yaml.compose) rather than constructed Python objects so that
every diagnostic carries a file:line:col location and duplicate keys can be detected.
Scalar values are interpreted from their raw text, so YAML 1.1 quirks (e.g. `yes` -> True,
`0o` octals) never leak into the memory map.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .diagnostics import Diagnostics, Loc
from .model import MemoryMap, Region

_NUM_RE = re.compile(
    r"(?P<num>0[xX][0-9a-fA-F](?:_?[0-9a-fA-F])*"
    r"|0[bB][01](?:_?[01])*"
    r"|[0-9](?:_?[0-9])*)"
    r"(?P<suffix>[KMG]?)"
)
_MULTIPLIER = {"": 1, "K": 1 << 10, "M": 1 << 20, "G": 1 << 30}
_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

MAX_ADDRESS_BITS = 64
# Address width of a top file that is a bare `regions:` list (no `memory:` section).
DEFAULT_ADDRESS_BITS = 32

_REGION_KEYS = {"id", "name", "start", "size", "children"}
# `start` may be omitted on children (see `_region`), so it is checked separately.
_REGION_REQUIRED = {"id", "name"}


def parse_int(text: str) -> int | None:
    """Parse `123`, `0x1F`, `0b101`, optionally with `_` separators and a K/M/G suffix."""
    m = _NUM_RE.fullmatch(text.strip())
    if not m:
        return None
    digits = m["num"].replace("_", "").lower()
    if digits.startswith("0x"):
        value = int(digits[2:], 16)
    elif digits.startswith("0b"):
        value = int(digits[2:], 2)
    else:
        value = int(digits, 10)
    return value * _MULTIPLIER[m["suffix"]]


_KIND = {MappingNode: "a mapping", SequenceNode: "a list", ScalarNode: "a scalar"}


def _kind(node: Node) -> str:
    if isinstance(node, ScalarNode) and node.tag == "tag:yaml.org,2002:null":
        return "nothing"
    return _KIND.get(type(node), "an unknown node")


class SourceLoader:
    def __init__(self, diags: Diagnostics) -> None:
        self.diags = diags
        self._include_stack: list[Path] = []
        # Source file -> files it includes (in order, deduplicated); top file first.
        self._files: dict[Path, list[Path]] = {}

    # -- helpers -----------------------------------------------------------------------

    def _loc(self, node: Node, path: Path) -> Loc:
        return Loc(path, node.start_mark.line + 1, node.start_mark.column + 1)

    def _compose(self, path: Path, from_loc: Loc | None) -> Node | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            self.diags.error("io", f"cannot read '{path}': {e.strerror or e}", from_loc)
            return None
        try:
            root = yaml.compose(text, Loader=yaml.SafeLoader)
        except yaml.MarkedYAMLError as e:
            mark = e.problem_mark or e.context_mark
            loc = Loc(path, mark.line + 1, mark.column + 1) if mark else Loc(path, 1, 1)
            self.diags.error("yaml", f"{e.problem or e.context or 'invalid YAML'}", loc)
            return None
        except yaml.YAMLError as e:
            self.diags.error("yaml", str(e), Loc(path, 1, 1))
            return None
        if root is None:
            self.diags.error("schema", "file is empty", Loc(path, 1, 1))
        return root

    def _mapping(
        self,
        node: Node,
        path: Path,
        what: str,
        allowed: set[str],
        required: set[str],
    ) -> dict[str, tuple[Node, Node]] | None:
        """Validate a mapping's shape; returns key -> (key_node, value_node)."""
        if not isinstance(node, MappingNode):
            self.diags.error(
                "schema", f"{what} must be a mapping, got {_kind(node)}", self._loc(node, path)
            )
            return None
        out: dict[str, tuple[Node, Node]] = {}
        ok = True
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                loc = self._loc(key_node, path)
                self.diags.error("schema", f"{what}: keys must be scalars", loc)
                ok = False
                continue
            key = key_node.value
            if key in out:
                self.diags.error(
                    "duplicate-key",
                    f"{what}: duplicate key '{key}'",
                    self._loc(key_node, path),
                    [(self._loc(out[key][0], path), "first given here")],
                )
                ok = False
                continue
            if key not in allowed:
                expected = ", ".join(sorted(allowed))
                self.diags.error(
                    "unknown-key",
                    f"{what}: unknown key '{key}' (expected one of: {expected})",
                    self._loc(key_node, path),
                )
                ok = False
                continue
            out[key] = (key_node, value_node)
        for key in sorted(required - out.keys()):
            self.diags.error(
                "missing-key", f"{what}: missing required key '{key}'", self._loc(node, path)
            )
            ok = False
        return out if ok else None

    def _string(self, node: Node, path: Path, what: str) -> str | None:
        if not isinstance(node, ScalarNode) or node.tag == "tag:yaml.org,2002:null":
            self.diags.error(
                "schema", f"{what} must be a string, got {_kind(node)}", self._loc(node, path)
            )
            return None
        if not node.value.strip():
            self.diags.error("schema", f"{what} must not be empty", self._loc(node, path))
            return None
        return node.value

    def _int(self, node: Node, path: Path, what: str) -> int | None:
        if not isinstance(node, ScalarNode) or node.tag == "tag:yaml.org,2002:null":
            self.diags.error(
                "schema", f"{what} must be an integer, got {_kind(node)}", self._loc(node, path)
            )
            return None
        value = parse_int(node.value)
        if value is None:
            self.diags.error(
                "bad-number",
                f"{what}: '{node.value}' is not a valid number "
                "(expected decimal, 0x hex or 0b binary, optional K/M/G suffix)",
                self._loc(node, path),
            )
        return value

    # -- grammar -----------------------------------------------------------------------

    def load(self, path: Path, address_bits: int | None = None) -> MemoryMap | None:
        """Load a top file. It is either a full map (`memory: { address_bits, regions }`) or
        a bare `regions:` file, i.e. one normally included from another file. A bare file
        takes its address width from `address_bits` (default `DEFAULT_ADDRESS_BITS`), and its
        regions are parsed like children, so they may leave out `start` (the first one is
        then placed at 0). `address_bits` is ignored for a full map, with a warning if it
        disagrees with the source."""
        path = path.resolve()
        self._include_stack = [path]
        self._files = {path: []}
        root = self._compose(path, None)
        if root is None:
            return None
        top = self._mapping(root, path, "top-level", {"memory", "regions"}, set())
        if top is None:
            return None
        if len(top) != 1:
            if top:
                msg = "top-level: give either 'memory' (a full map) or 'regions', not both"
                self.diags.error("top-kind", msg, self._loc(root, path))
            else:
                msg = "top-level: missing 'memory' (a full map) or 'regions' (a bare region list)"
                self.diags.error("missing-key", msg, self._loc(root, path))
            return None

        if "regions" in top:
            bits = DEFAULT_ADDRESS_BITS if address_bits is None else address_bits
            if not 1 <= bits <= MAX_ADDRESS_BITS:
                self.diags.error(
                    "schema",
                    f"address_bits must be between 1 and {MAX_ADDRESS_BITS}, got {bits}",
                    self._loc(root, path),
                )
                return None
            regions = self._regions(top["regions"][1], path, "regions", nested=True)
            return MemoryMap(bits, regions, self._loc(root, path), files=self._files)

        mem_node = top["memory"][1]
        mem = self._mapping(
            mem_node, path, "memory", {"address_bits", "regions"}, {"address_bits", "regions"}
        )
        if mem is None:
            return None

        bits_node = mem["address_bits"][1]
        bits = self._int(bits_node, path, "address_bits")
        if bits is not None and not 1 <= bits <= MAX_ADDRESS_BITS:
            self.diags.error(
                "schema",
                f"address_bits must be between 1 and {MAX_ADDRESS_BITS}, got {bits}",
                self._loc(bits_node, path),
            )
            bits = None
        if bits is not None and address_bits is not None and address_bits != bits:
            self.diags.warning(
                "address-bits-ignored",
                f"address width {address_bits} given on the command line is ignored; "
                f"the source sets address_bits: {bits}",
                self._loc(bits_node, path),
            )

        regions = self._regions(mem["regions"][1], path, "memory.regions", nested=False)
        if bits is None:
            return None
        return MemoryMap(bits, regions, self._loc(mem_node, path), files=self._files)

    def _regions(self, node: Node, path: Path, what: str, nested: bool) -> list[Region]:
        """Parse a region list. `nested` is True for `children` lists (and files included
        into them), where regions may leave out `start`."""
        if not isinstance(node, SequenceNode):
            self.diags.error(
                "schema", f"{what} must be a list, got {_kind(node)}", self._loc(node, path)
            )
            return []
        out: list[Region] = []
        for item in node.value:
            if isinstance(item, MappingNode) and any(
                isinstance(k, ScalarNode) and k.value == "include" for k, _ in item.value
            ):
                out.extend(self._include(item, path, nested))
            else:
                region = self._region(item, path, nested)
                if region is not None:
                    out.append(region)
        return out

    def _include(self, node: MappingNode, path: Path, nested: bool) -> list[Region]:
        fields = self._mapping(node, path, "include", {"include"}, {"include"})
        if fields is None:
            return []
        value_node = fields["include"][1]
        target = self._string(value_node, path, "include")
        if target is None:
            return []
        loc = self._loc(value_node, path)
        inc_path = (path.parent / target).resolve()
        if inc_path in self._include_stack:
            chain = " -> ".join(p.name for p in [*self._include_stack, inc_path])
            self.diags.error("include-cycle", f"include cycle: {chain}", loc)
            return []
        root = self._compose(inc_path, loc)
        if root is None:
            return []
        fields = self._mapping(root, inc_path, "included file", {"regions"}, {"regions"})
        if fields is None:
            return []
        if inc_path not in self._files[path]:
            self._files[path].append(inc_path)
        self._files.setdefault(inc_path, [])
        self._include_stack.append(inc_path)
        try:
            return self._regions(fields["regions"][1], inc_path, "regions", nested)
        finally:
            self._include_stack.pop()

    def _region(self, node: Node, path: Path, nested: bool) -> Region | None:
        """Parse one region. A child (`nested`) may omit `start` if it gives a `size`; its
        start is then resolved during semantic analysis (see `Region.start`)."""
        required = _REGION_REQUIRED if nested else _REGION_REQUIRED | {"start"}
        fields = self._mapping(node, path, "region", _REGION_KEYS, required)
        if fields is None:
            # Still descend into children so their errors are reported in the same run.
            if isinstance(node, MappingNode):
                for k, v in node.value:
                    if isinstance(k, ScalarNode) and k.value == "children":
                        self._regions(v, path, "children", nested=True)
            return None

        ok = True
        rid = self._string(fields["id"][1], path, "id")
        if rid is not None and not _ID_RE.fullmatch(rid):
            self.diags.error(
                "bad-id",
                f"id '{rid}' is not a valid identifier ([A-Za-z_][A-Za-z0-9_]*)",
                self._loc(fields["id"][1], path),
            )
            rid = None
        name = self._string(fields["name"][1], path, "name")
        start = None
        if "start" in fields:
            start = self._int(fields["start"][1], path, "start")
            ok = ok and start is not None
        size = None
        if "size" in fields:
            size = self._int(fields["size"][1], path, "size")
            ok = ok and size is not None
        elif "start" not in fields:
            self.diags.error(
                "missing-size",
                "region: a region without 'start' must have a 'size' "
                "so its address can be resolved",
                self._loc(node, path),
            )
            ok = False
        children = (
            self._regions(fields["children"][1], path, "children", nested=True)
            if "children" in fields
            else []
        )
        if not ok or rid is None or name is None:
            return None
        return Region(
            id=rid,
            name=name,
            start=start,
            implicit_start=start is None,
            size=size,
            children=children,
            loc=self._loc(node, path),
            field_locs={k: self._loc(v, path) for k, (_, v) in fields.items()},
        )
