from pathlib import Path, PurePosixPath
from textwrap import dedent

import pytest

from mmapc_src import lint
from mmapc_src.cli import main
from mmapc_src.diagnostics import Diagnostics
from mmapc_src.targets import sv
from mmapc_src.targets.options import TargetOptions

REPO_SRC = Path(__file__).resolve().parent.parent / "src"


def generate(top: Path, name: str = "soc") -> tuple[dict[PurePosixPath, str], Diagnostics]:
    mm, diags = lint(top)
    assert diags.errors == [], [d.render() for d in diags.items]
    gen = Diagnostics()
    return sv.generate(mm, gen, TargetOptions(name=name)), gen


def write(tmp_path: Path, regions: str, bits: int = 32) -> Path:
    body = "\n".join("    " + line for line in dedent(regions).splitlines())
    path = tmp_path / "top.yml"
    path.write_text(f"memory:\n  address_bits: {bits}\n  regions:\n{body}\n")
    return path


def elaborate(sources: list[str]) -> list[str]:
    """Parse + elaborate with slang; returns error messages."""
    pytest.importorskip("pyslang")
    from pyslang.ast import Compilation
    from pyslang.pyslang import DiagnosticEngine, DiagnosticSeverity
    from pyslang.syntax import SyntaxTree

    comp = Compilation()
    for text in sources:
        comp.addSyntaxTree(SyntaxTree.fromText(text))
    engine = DiagnosticEngine(comp.sourceManager)
    errors = (DiagnosticSeverity.Error, DiagnosticSeverity.Fatal)
    return [
        engine.formatMessage(d)
        for d in comp.getAllDiagnostics()
        if engine.getSeverity(d.code, d.location) in errors
    ]


# -- helpers -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rid,expected", [("uart", "Uart"), ("i2c0", "I2c0"), ("dma_ctrl", "DmaCtrl"), ("A_b", "AB")]
)
def test_camel(rid, expected):
    assert sv.camel(rid) == expected


@pytest.mark.parametrize(
    "value,bits,expected",
    [(0x33000, 32, "32'h0003_3000"), (0, 16, "16'h0000"), (0x1F, 20, "20'h0_001F")],
)
def test_sv_hex(value, bits, expected):
    assert sv.sv_hex(value, bits) == expected


# -- repository sources ------------------------------------------------------------------


def test_repo_package_structure():
    out, diags = generate(REPO_SRC / "top.yml", name="zeroheti")
    assert diags.items == []
    assert list(out) == [PurePosixPath("zeroheti_address_map_pkg.sv")]
    text = out[PurePosixPath("zeroheti_address_map_pkg.sv")]
    assert "package zeroheti_address_map_pkg;" in text
    assert "endpackage : zeroheti_address_map_pkg" in text
    assert "  localparam int unsigned AddrWidth = 32;" in text
    # Child types precede the top-level map type, in source order.
    assert text.index("} periph_map_t;") < text.index("} core_map_t;") < text.index("} addr_map_t;")
    # PULP-style rule fields; `end_addr` is exclusive.
    assert "    logic [AddrWidth-1:0] start_addr;\n    logic [AddrWidth-1:0] end_addr;\n" in text
    assert "base" not in text and "last" not in text
    assert (
        "  localparam addr_rule_t UartAddr    = "
        "'{start_addr: 32'h0003_3000, end_addr: 32'h0003_3008};  // UART"
    ) in text
    # Open-ended region reaching the top of the 32-bit space: end truncates to 0.
    assert (
        "'{start_addr: 32'h0011_0000, end_addr: 32'h0000_0000};  // External Address Space "
        "(end_addr 0: extends to the top of the address space)"
    ) in text
    assert "    start_addr: PeriphAddr.start_addr,\n    end_addr:   PeriphAddr.end_addr,\n" in text
    assert "    num:        8,\n" in text
    assert "    num:     6,\n" in text


def test_repo_package_elaborates_with_correct_values():
    out, _ = generate(REPO_SRC / "top.yml", name="zeroheti")
    checker = dedent(
        """\
        module check;
          import zeroheti_address_map_pkg::*;
          if (AddressMap.num != 6) $error("num");
          if (AddressMap.periph.num != 8) $error("periph num");
          if (AddressMap.periph.spi.start_addr != 32'h0003_5000) $error("spi start");
          if (AddressMap.periph.spi.end_addr != 32'h0003_5100) $error("spi end");
          if (AddressMap.periph.end_addr != 32'h000B_3000) $error("periph end");
          if (AddressMap.core.start_addr != 0 || AddressMap.core.end_addr != 32'h0003_0000)
            $error("core");
          if (AddressMap.ext.end_addr != 0) $error("ext");
          if (CoreMap.dmem != DmemAddr) $error("dmem");
          if ($bits(addr_rule_t) != 2 * AddrWidth) $error("rule width");
        endmodule
        """
    )
    assert elaborate([*out.values(), checker]) == []


def test_elaboration_catches_wrong_value():
    # Guards against the elaboration check silently passing.
    out, _ = generate(REPO_SRC / "top.yml")
    checker = "module check; import soc_address_map_pkg::*;\n"
    checker += 'if (AddressMap.num != 7) $error("num");\nendmodule\n'
    assert elaborate([*out.values(), checker]) != []


# -- nesting and widths ------------------------------------------------------------------


def test_deep_nesting_elaborates(tmp_path):
    out, diags = generate(
        write(
            tmp_path,
            """\
            - id: a
              name: A
              start: 0
              size: 0x1000
              children:
                - id: b
                  name: B
                  start: 0
                  size: 0x100
                  children:
                    - { id: c, name: C, start: 0x10, size: 0x10 }
                - { id: d, name: D, start: 0x800, size: 4 }
            """,
            bits=16,
        )
    )
    assert diags.items == []
    text = next(iter(out.values()))
    assert text.index("} b_map_t;") < text.index("} a_map_t;")
    assert "    b_map_t               b;" in text
    assert "    b:          BMap," in text
    checker = dedent(
        """\
        module check;
          import soc_address_map_pkg::*;
          if (AddressMap.a.b.c.start_addr != 16'h0010) $error("c start");
          if (AddressMap.a.b.c.end_addr != 16'h0020) $error("c end");
          if (AddressMap.a.d.end_addr != 16'h0804) $error("d end");
          if (AddressMap.a.num != 2 || AddressMap.a.b.num != 1) $error("num");
        endmodule
        """
    )
    assert elaborate([*out.values(), checker]) == []


def test_flat_map_without_parents(tmp_path):
    out, _ = generate(write(tmp_path, "- { id: rom, name: ROM, start: 0, size: 4K }"))
    text = next(iter(out.values()))
    assert "_map_t;" not in text.replace("addr_map_t;", "")
    assert elaborate(list(out.values())) == []


# -- name checks -------------------------------------------------------------------------


def test_keyword_id(tmp_path):
    _, diags = generate(write(tmp_path, "- { id: logic, name: L, start: 0, size: 4 }"))
    assert diags.codes() == ["sv-keyword"]


def test_child_named_like_map_field(tmp_path):
    out, diags = generate(
        write(
            tmp_path,
            """\
            - id: p
              name: P
              start: 0
              size: 0x100
              children:
                - { id: end_addr, name: B, start: 0, size: 4 }
            """,
        )
    )
    assert out == {}
    assert diags.codes() == ["sv-name-clash"]


def test_camel_case_param_clash(tmp_path):
    _, diags = generate(
        write(
            tmp_path,
            """\
            - { id: dma_ctrl, name: A, start: 0, size: 4 }
            - { id: dmaCtrl,  name: B, start: 4, size: 4 }
            """,
        )
    )
    assert diags.codes() == ["sv-name-clash"]


def test_reserved_type_clash(tmp_path):
    _, diags = generate(
        write(
            tmp_path,
            """\
            - id: addr
              name: A
              start: 0
              size: 0x100
              children:
                - { id: x, name: X, start: 0, size: 4 }
            """,
        )
    )
    assert "sv-name-clash" in diags.codes()


def test_bad_package_name(tmp_path):
    _, diags = generate(write(tmp_path, "- { id: a, name: A, start: 0, size: 4 }"), name="my-soc")
    assert diags.codes() == ["sv-bad-name"]


# -- CLI ---------------------------------------------------------------------------------


def test_cli_default_name_is_source_stem(tmp_path):
    assert main(["-q", "-t", "sv", "-o", str(tmp_path), str(REPO_SRC / "top.yml")]) == 0
    assert [p.name for p in tmp_path.iterdir()] == ["top_address_map_pkg.sv"]


@pytest.mark.parametrize("flag", ["-n", "--name"])
def test_cli_name(tmp_path, flag):
    argv = ["-q", "-t", "sv", flag, "zeroheti", "-o", str(tmp_path), str(REPO_SRC / "top.yml")]
    assert main(argv) == 0
    assert [p.name for p in tmp_path.iterdir()] == ["zeroheti_address_map_pkg.sv"]


def test_cli_sv_and_lint_only_are_exclusive():
    with pytest.raises(SystemExit) as exc:
        main(["-l", "-t", "sv", str(REPO_SRC / "top.yml")])
    assert exc.value.code == 2


def test_open_ended_child_ending_below_top_keeps_exclusive_end(tmp_path):
    out, _ = generate(
        write(
            tmp_path,
            """\
            - id: p
              name: P
              start: 0x1000
              size: 0x1000
              children:
                - { id: rest, name: Rest, start: 0x1800 }
            """,
            bits=16,
        )
    )
    text = next(iter(out.values()))
    assert "'{start_addr: 16'h1800, end_addr: 16'h2000};  // Rest\n" in text
