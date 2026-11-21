"""Diagnostic collection and rendering."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Loc:
    path: Path
    line: int  # 1-based
    col: int  # 1-based

    def __str__(self) -> str:
        try:
            shown = os.path.relpath(self.path)
        except ValueError:  # different drive on Windows
            shown = str(self.path)
        if shown.startswith(".."):
            shown = str(self.path)
        return f"{shown}:{self.line}:{self.col}"


@dataclass
class Diagnostic:
    severity: Severity
    code: str
    message: str
    loc: Loc | None
    notes: list[tuple[Loc, str]] = field(default_factory=list)

    def render(self) -> str:
        prefix = f"{self.loc}: " if self.loc else ""
        lines = [f"{prefix}{self.severity.value}[{self.code}]: {self.message}"]
        lines += [f"{loc}: note: {msg}" for loc, msg in self.notes]
        return "\n".join(lines)


@dataclass
class Diagnostics:
    items: list[Diagnostic] = field(default_factory=list)

    def error(self, code: str, message: str, loc: Loc | None, notes=()) -> None:
        self.items.append(Diagnostic(Severity.ERROR, code, message, loc, list(notes)))

    def warning(self, code: str, message: str, loc: Loc | None, notes=()) -> None:
        self.items.append(Diagnostic(Severity.WARNING, code, message, loc, list(notes)))

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.WARNING]

    def codes(self) -> list[str]:
        return [d.code for d in self.items]
