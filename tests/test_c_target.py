import shutil
import subprocess
from pathlib import Path, PurePosixPath
from textwrap import dedent

import pytest

from mmapc_src import lint
from mmapc_src.cli import main
from mmapc_src.diagnostics import Diagnostics
from mmapc_src.targets import c
from mmapc_src.targets.options import TargetOptions

REPO_SRC = Path(__file__).resolve().parent.parent / "src"
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


def generate(top: Path) -> tuple[dict[PurePosixPath, str], Diagnostics]:
    mm, diags = lint(top)
    assert diags.errors == [], [d.render() for d in diags.items]
    gen = Diagnostics()
    return c.generate(mm, gen, TargetOptions(name="top")), gen


def write(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dedent(text))
    return tmp_path / "top.yml"


def top(regions: str, bits: int = 32) -> str:
    body = "\n".join("    " + line for line in dedent(regions).splitlines())
    return f"memory:\n  address_bits: {bits}\n  regions:\n{body}\n"


def test_repo_one_header_per_source():
    out, diags = generate(REPO_SRC / "top.yml")
    assert diags.items == []
    assert sorted(map(str, out)) == ["core.h", "peripherals.h", "top.h"]
    top_h = out[PurePosixPath("top.h")]
    assert '#include "peripherals.h"\n#include "core.h"' in top_h
    assert "#ifndef MMAPC_TOP_H" in top_h
    assert "#define EXT_BASE     0x00110000UL  /* External Address Space */" in top_h
    periph = out[PurePosixPath("peripherals.h")]
    assert "#define UART_BASE   0x00033000UL  /* UART */" in periph
    assert "#define MTIMER_BASE 0x00033100UL  /* Machine Timer */" in periph
    assert "_SIZE" not in "".join(out.values())
    assert "UART" not in out[PurePosixPath("core.h")]


@pytest.mark.skipif(CC is None, reason="no C compiler")
def test_repo_headers_compile_with_correct_values(tmp_path):
    assert main(["-q", "-t", "c", "-o", str(tmp_path), str(REPO_SRC / "top.yml")]) == 0
    src = tmp_path / "check.c"
    src.write_text(
        dedent(
            """\
            #include "top.h"
            #include "top.h" /* include guards */
            _Static_assert(CORE_BASE == 0u, "core");
            _Static_assert(DMEM_BASE == 0x20000u, "dmem");
            _Static_assert(SPI_BASE == 0x35000u, "spi");
            _Static_assert(MBX_BASE == 0x108000u, "mbx");
            _Static_assert(EXT_BASE == 0x110000u, "ext");
            """
        )
    )
    subprocess.run(
        [CC, "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", "-fsyntax-only",
         f"-I{tmp_path}", str(src)],
        check=True,
    )  # fmt: skip


def test_subdirectory_includes_are_relative(tmp_path):
    out, _ = generate(
        write(
            tmp_path,
            {
                "top.yml": top("- include: sub/a.yml\n"),
                "sub/a.yml": "regions:\n  - include: b.yml\n",
                "sub/b.yml": "regions:\n  - { id: b, name: B, start: 0, size: 4 }\n",
            },
        )
    )
    assert sorted(map(str, out)) == ["sub/a.h", "sub/b.h", "top.h"]
    assert '#include "sub/a.h"' in out[PurePosixPath("top.h")]
    assert '#include "b.h"' in out[PurePosixPath("sub/a.h")]
    assert "#ifndef MMAPC_SUB_B_H" in out[PurePosixPath("sub/b.h")]


def test_wide_values_use_ull(tmp_path):
    out, _ = generate(
        write(
            tmp_path, {"top.yml": top("- { id: hi, name: Hi, start: 0x100000000, size: 4K }", 40)}
        )
    )
    text = out[PurePosixPath("top.h")]
    assert "#define HI_BASE 0x0100000000ULL  /* Hi */" in text


def test_comment_terminator_in_name_is_escaped(tmp_path):
    out, _ = generate(
        write(tmp_path, {"top.yml": top("- { id: a, name: 'A */ B', start: 0, size: 4 }")})
    )
    assert "A * / B" in out[PurePosixPath("top.h")]


def test_case_insensitive_id_clash(tmp_path):
    out, diags = generate(
        write(
            tmp_path,
            {
                "top.yml": top(
                    """\
                    - { id: uart, name: A, start: 0, size: 4 }
                    - { id: UART, name: B, start: 4, size: 4 }
                    """
                )
            },
        )
    )
    assert out == {}
    assert diags.codes() == ["c-name-clash"]


def test_cli_writes_headers(tmp_path, capsys):
    out_dir = tmp_path / "gen"
    assert main(["-t", "c", "-o", str(out_dir), str(REPO_SRC / "top.yml")]) == 0
    assert sorted(p.name for p in out_dir.iterdir()) == ["core.h", "peripherals.h", "top.h"]
    captured = capsys.readouterr()
    assert captured.out == ""  # no map printout when generating artifacts
    assert captured.err.count("mmapc: wrote") == 3


def test_cli_quiet_target_is_silent(tmp_path, capsys):
    assert main(["-q", "-t", "c", "-o", str(tmp_path), str(REPO_SRC / "top.yml")]) == 0
    assert capsys.readouterr() == ("", "")


def test_cli_lint_only_and_target_are_exclusive(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["-l", "-t", "c", str(REPO_SRC / "top.yml")])
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_cli_unknown_target(capsys):
    with pytest.raises(SystemExit):
        main(["-t", "rust", str(REPO_SRC / "top.yml")])


def test_cli_no_output_on_lint_error(tmp_path):
    bad = write(tmp_path, {"top.yml": top("- { id: a, name: A, start: 0, size: 0 }")})
    out_dir = tmp_path / "gen"
    assert main(["-t", "c", "-o", str(out_dir), str(bad)]) == 1
    assert not out_dir.exists()


def test_cli_lint_only_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["-l", "-q", str(REPO_SRC / "top.yml")]) == 0
    assert list(tmp_path.iterdir()) == []
