"""Options shared by all output targets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TargetOptions:
    # Design name used to name artifacts (e.g. `<name>_address_map_pkg`).
    name: str
