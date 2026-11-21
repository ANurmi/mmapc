# mmapc

A YAML-based memory map compiler.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

## Usage

```
mmapc [-l | -t {c,sv}] [-o OUTPUT_DIR] [-n NAME] [-q] [--werror] source
```

Every run lints the sources first. If there are errors, mmapc prints them as `file:line:col` messages, exits with status 1 and writes nothing.

```sh
uv run mmapc -l src/top.yml                  # lint and print the resolved memory map
uv run mmapc -t c src/top.yml                # C headers: build/top.h, build/core.h, ...
uv run mmapc -t sv -n soc src/top.yml        # SystemVerilog: build/soc_address_map_pkg.sv
```

| Option | Meaning |
| --- | --- |
| `-l`, `--lint-only` | Only check the sources and print the map. This is also what happens when `-t` isn't given. |
| `-t`, `--target {c,sv}` | Generate artifacts instead of printing the map. Can't be combined with `-l`. |
| `-o`, `--output-dir DIR` | Where generated files go (default: `build`). |
| `-n`, `--name NAME` | Design name used to name artifacts (default: the source file's name without extension). |
| `-q`, `--quiet` | Don't print the map or the list of written files. |
| `--werror` | Treat warnings as errors. |

## Source format

```yaml
# top.yml
memory:
  address_bits: 32
  regions:
    - { id: bootrom, name: BootROM, start: 0x00030000, size: 12K }
    - { id: ext,     name: External, start: 0x00110000 }   # no size: runs to the end of its parent or of the address space
    - include: peripherals.yml                             # path relative to this file

# peripherals.yml
regions:
  - id: periph
    name: Peripherals
    start: 0x00033000
    size: 512K
    children:
      - { id: uart, name: UART, start: 0x00033000, size: 8 }
```

- `id` must be a valid identifier and unique across the whole map. `name` is a free-form label.
- Numbers can be decimal, `0x` hex or `0b` binary, optionally with `_` separators and a `K`/`M`/`G` suffix (powers of 1024).
- Lint checks that regions don't overlap their siblings and stay within their parent and the address space. It also rejects sizes of zero, unknown or duplicate keys, and include cycles.

## Generated output

- **`c`**: one header per source file, with `#include`s mirroring the source includes. Each region gets a `<ID>_BASE` macro, e.g. `#define UART_BASE 0x00033000UL`.
- **`sv`**: a single `<NAME>_address_map_pkg` package. Each region gets an `addr_rule_t` localparam `{start_addr, end_addr}`, and regions with children also get nested `<id>_map_t` structs. These all combine into the top-level `AddressMap`. `end_addr` is exclusive. For a region that reaches the top of the address space, it wraps to `0`.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
