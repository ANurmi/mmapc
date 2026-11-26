"""Command-line interface."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .diagnostics import Diagnostics
from .lint import lint
from .loader import DEFAULT_ADDRESS_BITS
from .render import render_map
from .targets import TARGETS
from .targets.options import TargetOptions


def _report(diags: Diagnostics) -> None:
    for d in diags.items:
        print(d.render(), file=sys.stderr)
    if diags.items:
        print(f"{len(diags.errors)} error(s), {len(diags.warnings)} warning(s)", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    mm, diags = lint(args.source, args.address_bits)
    _report(diags)
    n_err, n_warn = len(diags.errors), len(diags.warnings)
    # The map is only fully resolved (region ends computed) when there are no errors.
    # It is not shown when generating artifacts.
    if not n_err and not args.quiet and args.target is None:
        print(render_map(mm))
    if n_err or (args.werror and n_warn):
        return 1
    if args.lint_only or args.target is None:
        return 0

    gen_diags = Diagnostics()
    opts = TargetOptions(name=args.name or args.source.stem, directives=args.directives)
    outputs = TARGETS[args.target](mm, gen_diags, opts)
    _report(gen_diags)
    if gen_diags.errors:
        return 1
    for rel, text in outputs.items():
        path = args.output_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if not args.quiet:
            print(f"mmapc: wrote {path}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mmapc", description="mmapc: A YAML-based memory map compiler"
    )
    parser.add_argument(
        "source",
        type=Path,
        help="top-level memory map file (e.g. src/top.yml), or a bare regions file "
        "(e.g. src/core.yml)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "-l",
        "--lint-only",
        action="store_true",
        help="only check sources for syntactic and semantic errors; generate no artifacts",
    )
    mode.add_argument(
        "-t",
        "--target",
        choices=sorted(TARGETS),
        help="generate artifacts for TARGET (c: C headers, one per source file; "
        "sv: SystemVerilog <NAME>_address_map_pkg; "
        "doc: <NAME>_memmap.yaml config for the memmap drawing tool; "
        "md: <NAME>_memory_map.md Markdown tables)",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("build"),
        help="directory for generated artifacts (default: build)",
    )
    parser.add_argument(
        "-n",
        "--name",
        help="design name used to name artifacts, e.g. the SystemVerilog package "
        "<NAME>_address_map_pkg (default: source file stem)",
    )
    parser.add_argument(
        "-d",
        "--directives",
        type=Path,
        metavar="FILE",
        help="YAML file of memmap-specific settings (figure, view, style, memory, "
        "regions) for the doc target",
    )
    parser.add_argument(
        "-a",
        "--address-bits",
        type=int,
        metavar="BITS",
        help="address width for a source with only 'regions:' and no 'memory:' section, "
        f"e.g. a file normally included from another (default: {DEFAULT_ADDRESS_BITS}); "
        "ignored, with a warning if different, when the source sets address_bits",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="do not print the parsed memory map or the list of generated files",
    )
    parser.add_argument("--werror", action="store_true", help="treat warnings as errors")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.directives is not None and args.target != "doc":
        parser.error("argument -d/--directives: only allowed with -t doc")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
