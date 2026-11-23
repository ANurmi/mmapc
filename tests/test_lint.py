from pathlib import Path
from textwrap import dedent, indent

import pytest

from mmapc_src import lint
from mmapc_src.cli import main
from mmapc_src.loader import parse_int
from mmapc_src.render import format_size, render_map

REPO_SRC = Path(__file__).resolve().parent.parent / "src"


def write(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (tmp_path / name).write_text(dedent(text))
    return tmp_path / "top.yml"


def codes(tmp_path: Path, files: dict[str, str]) -> list[str]:
    _, diags = lint(write(tmp_path, files))
    return diags.codes()


def top(regions: str, bits: int = 32) -> str:
    return f"memory:\n  address_bits: {bits}\n  regions:\n" + indent(dedent(regions), "    ")


# -- numbers -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,value",
    [("8", 8), ("0x100", 256), ("0b101", 5), ("4K", 4096), ("1M", 1 << 20), ("0x1_000", 4096)],
)
def test_parse_int_ok(text, value):
    assert parse_int(text) == value


@pytest.mark.parametrize("text", ["", "4k", "1.5K", "0x", "yes", "-1", "4KB", "0xG"])
def test_parse_int_bad(text):
    assert parse_int(text) is None


# -- repository sources ------------------------------------------------------------------


def test_repo_sources_are_clean():
    mm, diags = lint(REPO_SRC / "top.yml")
    assert diags.items == [], [d.render() for d in diags.items]
    ext = next(r for r in mm.walk() if r.id == "ext")
    assert ext.end == 1 << 32


def test_cli_exit_codes(tmp_path):
    assert main(["-l", str(REPO_SRC / "top.yml")]) == 0
    bad = write(tmp_path, {"top.yml": top("    - { id: a, name: A, start: 0, size: 0 }\n")})
    assert main(["-l", str(bad)]) == 1


def test_cli_werror(tmp_path):
    path = write(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: a, name: X, start: 0, size: 1 }
                - { id: b, name: X, start: 1, size: 1 }
            """
            )
        },
    )
    assert main(["-l", str(path)]) == 0
    assert main(["-l", "--werror", str(path)]) == 1


# -- syntax / schema ---------------------------------------------------------------------


def test_yaml_syntax_error_has_location(tmp_path):
    _, diags = lint(write(tmp_path, {"top.yml": "memory: [\n"}))
    assert diags.codes() == ["yaml"]
    assert diags.items[0].loc.path.name == "top.yml"


def test_empty_file(tmp_path):
    assert codes(tmp_path, {"top.yml": ""}) == ["schema"]


def test_missing_memory_keys(tmp_path):
    assert codes(tmp_path, {"top.yml": "memory: { regions: [] }\n"}) == ["missing-key"]


@pytest.mark.parametrize("bits", [0, 65])
def test_address_bits_range(tmp_path, bits):
    assert codes(tmp_path, {"top.yml": top("    []\n", bits=bits)}) == ["schema"]


def test_unknown_and_missing_region_keys(tmp_path):
    result = codes(tmp_path, {"top.yml": top("    - { id: a, start: 0, sise: 4 }\n")})
    assert sorted(result) == ["missing-key", "unknown-key"]


def test_duplicate_key(tmp_path):
    result = codes(tmp_path, {"top.yml": top("    - { id: a, id: b, name: A, start: 0 }\n")})
    assert result == ["duplicate-key"]


def test_bad_number_and_id(tmp_path):
    result = codes(tmp_path, {"top.yml": top("    - { id: 1bad, name: A, start: 0, size: 4KB }\n")})
    assert sorted(result) == ["bad-id", "bad-number"]


def test_errors_in_children_reported_with_broken_parent(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - id: p
                  start: 0
                  children:
                    - { id: c, name: C, start: zero }
            """
            )
        },
    )
    assert sorted(result) == ["bad-number", "missing-key"]


def test_semantics_skipped_after_schema_errors(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: a, name: A, start: 0, size: 8 }
                - { id: a, name: B, start: 4, size: bogus }
            """
            )
        },
    )
    assert result == ["bad-number"]


# -- includes ----------------------------------------------------------------------------


def test_include_missing_file(tmp_path):
    assert codes(tmp_path, {"top.yml": top("    - include: nope.yml\n")}) == ["io"]


def test_include_cycle(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top("    - include: a.yml\n"),
            "a.yml": "regions:\n  - include: b.yml\n",
            "b.yml": "regions:\n  - include: a.yml\n",
        },
    )
    assert result == ["include-cycle"]


def test_include_with_extra_keys(tmp_path):
    result = codes(
        tmp_path,
        {"top.yml": top("    - { include: a.yml, id: x }\n"), "a.yml": "regions: []\n"},
    )
    assert result == ["unknown-key"]


def test_included_file_location_in_diagnostic(tmp_path):
    _, diags = lint(
        write(
            tmp_path,
            {
                "top.yml": top("    - include: a.yml\n"),
                "a.yml": "regions:\n  - { id: x, name: X, start: 0, size: 0 }\n",
            },
        )
    )
    assert diags.codes() == ["zero-size"]
    assert diags.items[0].loc.path.name == "a.yml"
    assert diags.items[0].loc.line == 2


# -- semantics ---------------------------------------------------------------------------


def test_overlap(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: a, name: A, start: 0x0, size: 0x100 }
                - { id: b, name: B, start: 0x80, size: 0x100 }
            """
            )
        },
    )
    assert result == ["overlap"]


def test_overlap_with_containing_sibling(tmp_path):
    # c overlaps a even though b (between them in start order) ends earlier.
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: a, name: A, start: 0x0,  size: 0x1000 }
                - { id: b, name: B, start: 0x10, size: 0x10 }
                - { id: c, name: C, start: 0x40, size: 0x10 }
            """
            )
        },
    )
    assert result == ["overlap", "overlap"]


def test_adjacent_regions_do_not_overlap(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: a, name: A, start: 0x0,   size: 0x100 }
                - { id: b, name: B, start: 0x100, size: 0x100 }
            """
            )
        },
    )
    assert result == []


def test_open_ended_region_overlaps_later_sibling(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: ext, name: Ext, start: 0x1000 }
                - { id: b,   name: B,   start: 0x2000, size: 4 }
            """
            )
        },
    )
    assert result == ["overlap"]


def test_open_ended_child_extends_to_parent_end(tmp_path):
    mm, diags = lint(
        write(
            tmp_path,
            {
                "top.yml": top(
                    """\
                    - id: p
                      name: P
                      start: 0x1000
                      size: 0x1000
                      children:
                        - { id: c, name: C, start: 0x1800 }
                """
                )
            },
        )
    )
    assert diags.items == []
    assert next(r for r in mm.walk() if r.id == "c").end == 0x2000


def test_child_outside_parent(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - id: p
                  name: P
                  start: 0x1000
                  size: 0x100
                  children:
                    - { id: before, name: B, start: 0x0800, size: 4 }
                    - { id: past,   name: C, start: 0x10F0, size: 0x20 }
            """
            )
        },
    )
    assert result == ["out-of-bounds", "out-of-bounds"]


def test_exceeds_address_space(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: a, name: A, start: 0xFFF0, size: 0x20 }
                - { id: b, name: B, start: 0x10000, size: 1 }
            """,
                bits=16,
            )
        },
    )
    assert result == ["out-of-bounds", "out-of-bounds"]


def test_zero_size(tmp_path):
    assert codes(tmp_path, {"top.yml": top("    - { id: a, name: A, start: 0, size: 0 }\n")}) == [
        "zero-size"
    ]


# -- implicit start ----------------------------------------------------------------------


def starts(mm) -> dict[str, tuple[int, int]]:
    return {r.id: (r.start, r.end) for r in mm.walk()}


def test_implicit_start_packs_children(tmp_path):
    mm, diags = lint(
        write(
            tmp_path,
            {
                "top.yml": top(
                    """\
                    - id: p
                      name: P
                      start: 0x1000
                      size: 0x1000
                      children:
                        - { id: a, name: A, size: 0x10 }
                        - { id: b, name: B, size: 0x20 }
                        - { id: c, name: C, start: 0x1800, size: 0x10 }
                        - { id: d, name: D, size: 0x10 }
                        - id: e
                          name: E
                          size: 0x100
                          children:
                            - { id: f, name: F, size: 4 }
                """
                )
            },
        )
    )
    assert diags.items == [], [d.render() for d in diags.items]
    got = starts(mm)
    assert got["a"] == (0x1000, 0x1010)  # first child: parent's start
    assert got["b"] == (0x1010, 0x1030)  # right after the previous sibling
    assert got["d"] == (0x1810, 0x1820)  # after an explicitly placed sibling
    assert got["e"] == (0x1820, 0x1920)
    assert got["f"] == (0x1820, 0x1824)  # nested implicit child
    assert all(not r.implicit_start for r in mm.walk() if r.id in ("p", "c"))


def test_implicit_start_through_include(tmp_path):
    mm, diags = lint(
        write(
            tmp_path,
            {
                "top.yml": top(
                    """\
                    - id: p
                      name: P
                      start: 0x100
                      size: 0x100
                      children:
                        - { id: a, name: A, size: 8 }
                        - include: kids.yml
                """
                ),
                "kids.yml": "regions:\n  - { id: b, name: B, size: 8 }\n",
            },
        )
    )
    assert diags.items == [], [d.render() for d in diags.items]
    assert starts(mm)["b"] == (0x108, 0x110)


def test_implicit_start_requires_size(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - id: p
                  name: P
                  start: 0
                  children:
                    - { id: c, name: C }
            """
            )
        },
    )
    assert result == ["missing-size"]


def test_top_level_region_requires_start(tmp_path):
    result = codes(tmp_path, {"top.yml": top("    - { id: a, name: A, size: 4 }\n")})
    assert result == ["missing-key"]
    # Also when spliced into the top level from an included file.
    result = codes(
        tmp_path,
        {
            "top.yml": top("    - include: b.yml\n"),
            "b.yml": "regions:\n  - { id: b, name: B, size: 4 }\n",
        },
    )
    assert result == ["missing-key"]


def test_implicit_start_overflows_parent(tmp_path):
    # 'b' overflows; 'c' is not placed after it, so there is no follow-on error.
    _, diags = lint(
        write(
            tmp_path,
            {
                "top.yml": top(
                    """\
                    - id: p
                      name: P
                      start: 0
                      size: 0x10
                      children:
                        - { id: a, name: A, size: 8 }
                        - { id: b, name: B, size: 9 }
                        - { id: c, name: C, size: 1 }
                """
                )
            },
        )
    )
    assert diags.codes() == ["out-of-bounds"]
    assert "implicitly, after 'a'" in diags.items[0].render()


def test_implicit_start_after_open_ended_sibling(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - id: p
                  name: P
                  start: 0
                  size: 0x10
                  children:
                    - { id: a, name: A, start: 4 }
                    - { id: b, name: B, size: 4 }
            """
            )
        },
    )
    assert result == ["out-of-bounds"]


def test_implicit_start_overlaps_explicit_sibling(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - id: p
                  name: P
                  start: 0
                  size: 0x100
                  children:
                    - { id: a, name: A, start: 0x10, size: 4 }
                    - { id: b, name: B, size: 0x10 }
                    - { id: c, name: C, start: 0x18, size: 4 }
            """
            )
        },
    )
    assert result == ["overlap"]


def test_duplicate_id_across_includes(tmp_path):
    result = codes(
        tmp_path,
        {
            "top.yml": top(
                """\
                - { id: a, name: A, start: 0, size: 4 }
                - include: b.yml
            """
            ),
            "b.yml": "regions:\n  - { id: a, name: B, start: 4, size: 4 }\n",
        },
    )
    assert result == ["duplicate-id"]


def test_duplicate_name_is_warning(tmp_path):
    _, diags = lint(
        write(
            tmp_path,
            {
                "top.yml": top(
                    """\
                    - { id: a, name: UART, start: 0, size: 4 }
                    - { id: b, name: UART, start: 4, size: 4 }
                """
                )
            },
        )
    )
    assert diags.codes() == ["duplicate-name"]
    assert diags.errors == []


# -- map printout ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n,text",
    [
        (36, "36"),
        (1023, "1023"),
        (4096, "4K"),
        (192 * 1024, "192K"),
        (1 << 20, "1M"),
        (1025, "0x401"),
        (0xFFEF0000, "0xFFEF0000"),
    ],
)
def test_format_size(n, text):
    assert format_size(n) == text


def test_cli_prints_map_by_default(capsys):
    assert main(["-l", str(REPO_SRC / "top.yml")]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Memory map (32-bit address space)"
    assert out[1].split() == ["ID", "NAME", "START", "END", "SIZE"]
    # Tree connectors: siblings in address order, children nested under their parent.
    name_col = out[1].index("NAME")
    tree = [line[:name_col].rstrip() for line in out[2:6]]
    assert tree == ["├── core", "│   ├── dbg", "│   ├── imem", "│   └── dmem"]
    assert out[2].split()[-3:] == ["0x00000000", "0x0002FFFF", "192K"]
    assert out[-1].startswith("└── ext ")
    assert out[-1].split()[-3:] == ["0x00110000", "0xFFFFFFFF", "0xFFEF0000"]
    # Address columns line up regardless of depth.
    assert len({line.index("0x") for line in out[2:]}) == 1


def test_render_last_child_branches(tmp_path):
    mm, diags = lint(
        write(
            tmp_path,
            {
                "top.yml": top(
                    """\
                    - id: a
                      name: A
                      start: 0
                      size: 0x100
                      children:
                        - id: b
                          name: B
                          start: 0
                          size: 0x10
                          children:
                            - { id: c, name: C, start: 0, size: 4 }
                    - { id: d, name: D, start: 0x100, size: 4 }
                """
                )
            },
        )
    )
    assert diags.items == []
    out = render_map(mm).splitlines()
    name_col = out[1].index("NAME")
    tree = [line[:name_col].rstrip() for line in out[2:]]
    # c sits under the last child of a non-last root, so its prefix is "│" then blank.
    assert tree == ["├── a", "│   └── b", "│       └── c", "└── d"]


@pytest.mark.parametrize("flag", ["-q", "--quiet"])
def test_cli_quiet(capsys, flag):
    assert main(["-l", flag, str(REPO_SRC / "top.yml")]) == 0
    assert capsys.readouterr().out == ""


def test_cli_no_map_on_errors(tmp_path, capsys):
    bad = write(tmp_path, {"top.yml": top("    - { id: a, name: A, start: 0, size: 0 }\n")})
    assert main(["-l", str(bad)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "zero-size" in captured.err


@pytest.mark.parametrize("argv", [[], ["-l"], ["--lint-only"]])
def test_cli_lint_only_flag(argv, capsys):
    assert main([*argv, str(REPO_SRC / "top.yml")]) == 0
    assert capsys.readouterr().out.startswith("Memory map")
