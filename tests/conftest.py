from pathlib import Path
from textwrap import dedent

import pytest

# One subsystem file instantiated twice: `uart`, `dma` and `ctrl` each appear under both
# `sub_a` and `sub_b`, told apart only by the hierarchy. `rom` is unique.
REPLICATED_TOP = """\
memory:
  address_bits: 16
  regions:
    - id: sub_a
      name: Subsystem A
      start: 0x0000
      size: 0x1000
      children:
        - include: sub.yml
    - id: sub_b
      name: Subsystem B
      start: 0x1000
      size: 0x1000
      children:
        - include: sub.yml
    - { id: rom, name: ROM, start: 0x2000, size: 0x100 }
"""

REPLICATED_SUB = """\
regions:
  - { id: uart, name: UART, size: 0x100 }
  - id: dma
    name: DMA
    size: 0x100
    children:
      - { id: ctrl, name: Control, size: 0x10 }
"""


@pytest.fixture
def replicated_map(tmp_path: Path) -> Path:
    """A map whose subsystem file is included under two parents; returns its top file."""
    (tmp_path / "sub.yml").write_text(dedent(REPLICATED_SUB))
    top = tmp_path / "top.yml"
    top.write_text(dedent(REPLICATED_TOP))
    return top
