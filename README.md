# mmapc

A YAML-based memory map compiler.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

## Usage

```
mmapc [-l | -t {c,sv,doc,md}] [-o OUTPUT_DIR] [-n NAME] [-d FILE] [-q] [--werror] source
```

Every run lints the sources first. If there are errors, mmapc prints them as `file:line:col` messages, exits with status 1 and writes nothing.

```sh
uv run mmapc -l src/top.yml                  # lint and print the resolved memory map
uv run mmapc -t c src/top.yml                # C headers: build/top.h, build/core.h, ...
uv run mmapc -t sv -n soc src/top.yml        # SystemVerilog: build/soc_address_map_pkg.sv
uv run mmapc -t doc src/top.yml              # memmap drawing config: build/top_memmap.yaml
uv run mmapc -t md src/top.yml               # Markdown tables: build/top_memory_map.md
```

| Option | Meaning |
| --- | --- |
| `-l`, `--lint-only` | Only check the sources and print the map. This is also what happens when `-t` isn't given. |
| `-t`, `--target {c,sv,doc,md}` | Generate artifacts instead of printing the map. Can't be combined with `-l`. |
| `-o`, `--output-dir DIR` | Where generated files go (default: `build`). |
| `-n`, `--name NAME` | Design name used to name artifacts (default: the source file's name without extension). |
| `-d`, `--directives FILE` | memmap settings for the `doc` target (see below). Only allowed with `-t doc`. |
| `-a`, `--address-bits BITS` | Address width when the source has only `regions:` (default: 32). Ignored, with a warning if it differs, when the source sets `address_bits`. |
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
      - { id: spi,  name: SPI,  size: 4K }   # no start: placed right after 'uart'
```

A file with only `regions:`, like `peripherals.yml` above, can also be the source given to `mmapc`. This lets you check or generate artifacts for one part of the map by itself, e.g. `mmapc -a 32 src/peripherals.yml`. The address width comes from `-a` (default 32). Its regions may leave out `start`, as children can; the first one then starts at 0.

- A child region can leave out `start` if it gives a `size`. It's then placed right after the previous sibling in source order, or at the parent's start if it's the first child. Top-level regions always need a `start`.

- `id` must be a valid identifier and unique among its siblings. Regions under different parents may share an id, so you can include the same subsystem file under several parents. The hierarchy tells them apart, e.g. `sub_a.uart` and `sub_b.uart`. `name` is a free-form label.
- Generated names keep the plain id when no other region uses it. An id used more than once is qualified with its path in flat namespaces: `SUB_A_UART_BASE` in C, `SubAUartAddr` in SystemVerilog and `sub_a_uart` in memmap. Adding a second region with an existing id therefore renames the generated names of both.
- Numbers can be decimal, `0x` hex or `0b` binary, optionally with `_` separators and a `K`/`M`/`G` suffix (powers of 1024).
- Lint checks that regions don't overlap their siblings and stay within their parent and the address space. It also rejects sizes of zero, unknown or duplicate keys, and include cycles.

## Generated output

- **`c`**: one header per source file, with `#include`s mirroring the source includes. Each region gets a `<ID>_BASE` macro, e.g. `#define UART_BASE 0x00033000UL`.
- **`sv`**: a single `<NAME>_address_map_pkg` package. Each region gets an `addr_rule_t` localparam `{start_addr, end_addr}`, and regions with children also get nested `<id>_map_t` structs. These all combine into the top-level `AddressMap`. `end_addr` is exclusive. For a region that reaches the top of the address space, it wraps to `0`.

- **`doc`**: a single `<NAME>_memmap.yaml` config for [memmap](https://github.com/iharhl/memmap), which draws datasheet-style memory map images. Every region gets an explicit `start` and `size`. By default the drawing has the title `<NAME> memory map` and zooms into every region with children. Render it with:

  ```sh
  memmap render build/top_memmap.yaml -o top.svg   # or .png/.pdf with memmap[raster]
  ```

  memmap is a dev dependency, so `uv run memmap ...` works after `uv sync`.

- **`md`**: a single `<NAME>_memory_map.md` with GitHub-flavored Markdown tables (name, id, start, inclusive end, size): one for the top-level regions, then a section with a table of children for each region that has them. Siblings are in address order, as in `mmapc -l`.

### memmap directives (`-d`)

Settings that only matter for the drawing go in a separate YAML file, so the memory map sources stay tool-neutral:

```yaml
figure: { title: zeroHETI address map, footer: ZH-MM-1, legend: true }
view:                                   # e.g. which regions to zoom into, labels, scale
  expand: [periph]
  defaults: { labels: { size: true } }
style: { palette: datasheet }
memory: { check_alignment: true }       # only check_alignment, fill_gaps
regions:                                # keyed by mmapc region path (periph.uart), or by id if unique
  bootrom: { color: "#ffe0b0", note: Read-only }
  ext:     { name: "External\nAddress Space" }   # only name, color, note, kind, sectors
```

```sh
uv run mmapc -t doc -n zeroheti -d src/doc.yml src/top.yml   # src/doc.yml is a commented example
```

`figure`, `view` and `style` are merged over the defaults: mappings merge key by key, and anything else (including lists like `view.expand`) replaces the default. Their contents are passed through as-is and memmap checks them; run `memmap schema` for every key. `memory` and `regions` can't change where regions are, so the drawing always matches the sources. mmapc reports unknown keys and regions in the directives file as errors and writes nothing. It also reports an error when a plain id matches several regions.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
