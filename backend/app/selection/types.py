from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class KanjiClass(Enum):
    KNOWN = "known"
    FUTURE = "future"
    ORPHAN = "orphan"


@dataclass(frozen=True)
class VocabCandidate:
    """Pure-data view of a vocab row, as needed by the selection algorithm.
    Callers (services/batch_service.py) build these from DB rows; this
    module never touches SQLAlchemy.
    """

    id: int
    kanji_form: str
    hiragana_form: str
    kanji_chars: frozenset[str]  # component kanji, precomputed by the caller
    usually_kana: bool = False


@dataclass(frozen=True)
class SelectionConfig:
    daily_minimum: int
    study_end_date: date


@dataclass
class SelectedWord:
    vocab_id: int
    is_target_linked: bool
    needs_kanji_reading: bool
    covers_target_kanji: frozenset[str]


@dataclass
class SelectionWarning:
    kind: str  # e.g. "no_eligible_covering_word"
    detail: str
    kanji: str | None = None


@dataclass
class SelectionResult:
    batch_number: int
    weekly_target_used: int
    pacing_floor: int  # daily_minimum * 7 -- the un-adjusted floor, for the UI's on-pace/behind indicator
    selected: list[SelectedWord] = field(default_factory=list)
    target_kanji_coverage: dict[str, list[int]] = field(default_factory=dict)  # kanji -> covering vocab ids
    warnings: list[SelectionWarning] = field(default_factory=list)
