"""Output targets (`--target/-t`).

A target is a function `generate(mm, diags, opts) -> dict[PurePosixPath, str]` that maps output
paths (relative to the output directory) to file contents. It runs only on a lint-clean,
analyzed map, and must not write files itself; it reports target-specific problems
through `diags` instead.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

from ..diagnostics import Diagnostics
from ..model import MemoryMap
from . import c, doc, ipxact, md, sv, svd
from .options import TargetOptions

Generator = Callable[[MemoryMap, Diagnostics, TargetOptions], dict[PurePosixPath, str]]

TARGETS: dict[str, Generator] = {
    "c": c.generate,
    "doc": doc.generate,
    "ipxact": ipxact.generate,
    "md": md.generate,
    "sv": sv.generate,
    "svd": svd.generate,
}
