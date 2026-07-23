from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class ParseWarning:
    row_number: int
    reason: str
    raw: Any = None


@dataclass
class ParseResult(Generic[T]):
    rows: list[T] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)

    def add_warning(self, row_number: int, reason: str, raw: Any = None) -> None:
        self.warnings.append(ParseWarning(row_number=row_number, reason=reason, raw=raw))
