# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`mmapc` is a memory map compiler written in Python and managed with uv. It reads a declarative YAML description of an SoC address map; the map for this repo's 32-bit SoC lives in `src/`. The CLI is `mmapc [-l | -t {c,sv,doc}] [-o DIR] [-n NAME] [-d FILE] [-q] [--werror] source`. It always lints the sources. Without `-t` it prints the parsed map. With `-t/--target` it generates artifacts into `-o/--output-dir` (default `build/`) instead of printing the map. `-n/--name` sets the design name used to name artifacts, and defaults to the source file stem. `-d/--directives FILE` is only allowed with `-t doc` (the CLI rejects it otherwise). `-l/--lint-only` and `-t` are mutually exclusive. Running with neither does the same as `-l`.

The project (distribution) and CLI command are both named `mmapc`, but the importable package is `mmapc_src/`, at the repo root. It's named differently so it doesn't clash with the project directory, and it isn't under `src/` because `src/` holds the memory map sources. For this reason `pyproject.toml` sets `module-root = ""` and `module-name = "mmapc_src"` for the `uv_build` backend.

## Commands

```sh
uv sync                                  # create .venv with dev deps (pytest, ruff, pyslang, memmap)
uv run mmapc -l src/top.yml              # lint only + print the resolved map (stdout); exit 1 on errors
uv run mmapc -l -q src/top.yml           # lint only, no map printout
uv run mmapc -l --werror src/top.yml     # also fail on warnings
uv run mmapc -t c -o build src/top.yml   # generate C headers into build/
uv run mmapc -t sv -n zeroheti src/top.yml   # build/zeroheti_address_map_pkg.sv
uv run mmapc -t doc -d src/doc.yml src/top.yml   # build/top_memmap.yaml; src/doc.yml = example directives
uv run memmap render build/top_memmap.yaml -o top.svg
uv run pytest                            # all tests
uv run pytest tests/test_lint.py::test_overlap   # single test
uv run ruff check . && uv run ruff format --check .
```

## Compiler architecture

The pipeline is `lint.lint(path)` → `loader.SourceLoader` (syntax phase) → `semantics.analyze` (semantic phase). Every problem goes into a shared `diagnostics.Diagnostics`.

- **Loader** (`mmapc_src/loader.py`) works on the PyYAML *node graph* (`yaml.compose`), not on constructed Python objects. This keeps a `file:line:col` for every diagnostic, lets it catch duplicate keys, and means it parses scalars from their raw text (`parse_int`), so YAML 1.1 type guessing never applies. The loader also resolves `include:` entries (relative to the including file, with cycle detection), rejects unknown and missing keys, and builds `model.MemoryMap` / `model.Region`. When a region is invalid, the loader drops it but still descends into its children so their errors get reported in the same run.
- **Semantics** (`mmapc_src/semantics.py`) only runs when the loader reported no errors, so dropped regions can't cause a cascade of follow-on errors. It sets `Region.end`, the exclusive end address. A region without `size` extends to the end of its parent, or to `2**address_bits` at the top level. Code generators added later should use `end` rather than `size`.
- `render.render_map` prints the analyzed map as a `tree(1)`-style hierarchy (siblings in address order) with aligned NAME/START/END/SIZE columns (END inclusive). The CLI prints it to stdout only when there are no errors, because it depends on `Region.end`, and never when `-t` is given. Diagnostics go to stderr.
- **Targets** (`mmapc_src/targets/`): `TARGETS` maps each `-t` name to a `generate(mm, diags, opts: TargetOptions) -> {relative PurePosixPath: text}` function. Generators run only on a lint-clean, analyzed map, never write files themselves (the CLI does that), and report target-specific problems as diagnostics. If any are errors, nothing is written. To add a target, add a module and register it in `TARGETS`.
- **C target** (`targets/c.py`): writes one header per source file, mirroring the source tree relative to the common directory of all sources (`top.yml` → `top.h`). Each header `#include`s the headers for the files its source includes, using relative paths. Each region gets only a flat `<ID>_BASE` macro holding its base address (`UL`, or `ULL` above 32 bits), with no size or end macros. The macro goes in the header of the file the region is written in; it uses `MemoryMap.files`, the include graph recorded by the loader. Ids that collide after upper-casing raise `c-name-clash`.
- **SystemVerilog target** (`targets/sv.py`): writes one `<name>_address_map_pkg.sv`, modeled on the zeroHETI `zeroheti_address_map_pkg.sv`. It contains `AddrWidth`, `addr_rule_t {start_addr, end_addr}` (named after the PULP AXI rule fields), a `<id>_map_t {start_addr, end_addr, num, <children>}` struct for every region with children, an `addr_map_t` for the top level, a `<CamelId>Addr` localparam for every region, and `<CamelId>Map` / `AddressMap` localparams. Regions keep source order; types and maps are emitted bottom-up, so each one is declared before it's used. **`end_addr` is exclusive** (start + size), as in the example. A region that reaches the top of the address space has an end of `2**AddrWidth`, which doesn't fit in `AddrWidth` bits, so it truncates to `end_addr = 0` and the line gets a comment saying so. The generator rejects ids that are SV keywords, collide after camel-casing, or are named `start_addr`/`end_addr`/`num` under a parent. Tests elaborate the generated package with pyslang and check values with `$error`.
- **Doc target** (`targets/doc.py`): writes one `<name>_memmap.yaml`, a config for [memmap](https://github.com/iharhl/memmap) (a drawing tool; dev dependency pinned to a git tag, since it isn't on PyPI). Regions get explicit hex `start` and `size` from `end - start`, siblings in address order, sizes as `K`/`M`/`G` when exact. Defaults: `figure.title = "<name> memory map"`, `view.expand` = every region with children. The optional directives file (`TargetOptions.directives`) is read with the loader's node helpers, so its errors carry `file:line:col`: `figure`/`view`/`style` are deep-merged over the defaults (lists replace) and passed through unchecked; `memory` only allows `check_alignment`/`fill_gaps`; `regions` maps a region id to `name`/`color`/`note`/`kind`/`sectors`. Geometry keys are rejected so the drawing can't disagree with the sources. Unknown ids raise `doc-unknown-region`. Tests run `memmap check/describe/render --json` and compare the resolved ranges with mmapc's; they skip if `memmap` isn't on PATH.
- Each diagnostic has a stable `code` (e.g. `overlap`, `duplicate-id`, `bad-number`). Tests assert on these codes, so keep them stable and add new ones for new checks.

## Memory map source format

- The top file has `memory: { address_bits, regions }`. An included file has only `regions:`. An `- include: <file>.yml` entry can appear in any regions or `children` list, and the included regions are spliced in at that point.
- A region is `{ id, name, start, size?, children? }`. A child region (one in a `children` list, including regions spliced in there by an `include:`) can leave out `start` if it has a `size`. Otherwise the loader reports `missing-size`. Semantics then places it right after its preceding sibling in source order, or at the parent's start if it is the first child, and sets `Region.implicit_start`. Top-level regions always need `start`. `Region.start` is `None` until analysis has run. `id` must match `[A-Za-z_][A-Za-z0-9_]*` and be unique across the whole map. `name` is the human-readable label; a duplicate name is only a warning.
- Numbers can be decimal, `0x` hex or `0b` binary, with optional `_` separators and an optional `K`/`M`/`G` (binary) suffix.
- Lint enforces these rules: size > 0; regions stay inside the address space and inside their parent; siblings don't overlap (touching end-to-start is fine). A region without a size covers everything up to its container's end, so it must be the highest sibling.
- `src/doc.yml` holds example memmap directives for `-t doc`; it is not a map source and nothing includes it.
- In `src/`, the low address space is packed contiguously: core `0x00000`–`0x2FFFF`, BootROM at `0x30000`, peripherals from `0x33000`. If you resize one of these, rerun lint.
