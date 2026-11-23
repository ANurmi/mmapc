"""Options shared by all output targets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TargetOptions:
    # Design name used to name artifacts (e.g. `<name>_address_map_pkg`).
    name: str
    # Target-specific directives file (`-d/--directives`); only the `doc` target reads it.
    directives: Path | None = None
