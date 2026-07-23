"""The core batch-selection algorithm. Pure function: no DB session, no
`datetime.now()` -- `today` is always an injected parameter so the pacing
math (weekly_target) is deterministic and testable.
"""

import math
from datetime import date

from app.ingestion.bccwj_frequency_loader import FrequencyInfo
from app.selection.classify import classify_word_kanji, has_future_kanji, orphan_kanji_count
from app.selection.types import (
    KanjiClass,
    SelectedWord,
    SelectionConfig,
    SelectionResult,
    SelectionWarning,
    VocabCandidate,
)


def compute_weekly_target(
    today: date, study_end_date: date, remaining_words: int, daily_minimum: int
) -> tuple[int, int]:
    """Returns (weekly_target, pacing_floor). remaining_weeks is clamped to a
    minimum of 1 at/after study_end_date -- an explicit, documented decision
    (the spec doesn't say what happens once the schedule has run out), so a
    late-running import never divides by zero or produces a negative target.
    """
    pacing_floor = daily_minimum * 7
    days_remaining = (study_end_date - today).days
    remaining_weeks = max(1, math.ceil(days_remaining / 7))
    dynamic_target = math.ceil(remaining_words / remaining_weeks)
    return max(pacing_floor, dynamic_target), pacing_floor


def _sort_key(candidate: VocabCandidate, orphan_count: int, frequency_lookup: dict[str, FrequencyInfo]):
    freq = frequency_lookup.get(candidate.kanji_form)
    # Spec's tie-break: fewer orphan kanji, then more common / shorter word.
    # core_rank is ascending-more-common; missing frequency data falls back
    # to word length only (graceful degradation, not a hard requirement).
    rank = freq.core_rank if freq and freq.core_rank is not None else math.inf
    return (orphan_count, rank, len(candidate.kanji_form), candidate.id)


def select_batch(
    candidates: list[VocabCandidate],
    coverage: set[str],
    schedule: dict[str, int],
    target_kanji: set[str],
    batch_n: int,
    config: SelectionConfig,
    today: date,
    frequency_lookup: dict[str, FrequencyInfo] | None = None,
) -> SelectionResult:
    frequency_lookup = frequency_lookup or {}
    known_kanji = coverage | target_kanji

    weekly_target, pacing_floor = compute_weekly_target(
        today, config.study_end_date, remaining_words=len(candidates), daily_minimum=config.daily_minimum
    )

    result = SelectionResult(batch_number=batch_n, weekly_target_used=weekly_target, pacing_floor=pacing_floor)

    # Skip-ahead guard: eligible pool has zero Future kanji, no exceptions.
    eligible: list[tuple[VocabCandidate, dict[str, KanjiClass]]] = []
    for c in candidates:
        classes = classify_word_kanji(c, known_kanji, schedule, batch_n)
        if has_future_kanji(classes):
            continue
        eligible.append((c, classes))

    target_linked = [
        (c, classes) for c, classes in eligible if c.kanji_chars & target_kanji
    ]
    filler = [(c, classes) for c, classes in eligible if not (c.kanji_chars & target_kanji)]

    # Preference order: fewer orphan kanji, then more common / shorter word.
    target_linked.sort(key=lambda pair: _sort_key(pair[0], orphan_kanji_count(pair[1]), frequency_lookup))
    filler.sort(key=lambda pair: _sort_key(pair[0], orphan_kanji_count(pair[1]), frequency_lookup))

    selected_ids: set[int] = set()
    selected_words: list[SelectedWord] = []
    target_coverage: dict[str, list[int]] = {k: [] for k in target_kanji}

    def _select(candidate: VocabCandidate, classes: dict[str, KanjiClass], is_target_linked: bool) -> None:
        covers = frozenset(k for k in candidate.kanji_chars if k in target_kanji)
        needs_reading = bool(covers) and orphan_kanji_count(classes) == 0
        selected_words.append(
            SelectedWord(
                vocab_id=candidate.id,
                is_target_linked=is_target_linked,
                needs_kanji_reading=needs_reading,
                covers_target_kanji=covers,
            )
        )
        selected_ids.add(candidate.id)
        for k in covers:
            target_coverage[k].append(candidate.id)

    # Set-cover pass: guarantee every target kanji gets at least one word.
    uncovered = set(target_kanji)
    for kanji in sorted(target_kanji):
        if kanji not in uncovered:
            continue
        best = next(
            (c for c, classes in target_linked if c.id not in selected_ids and kanji in c.kanji_chars),
            None,
        )
        if best is None:
            result.warnings.append(
                SelectionWarning(
                    kind="no_eligible_covering_word",
                    detail=f"No eligible vocab word covers target kanji {kanji!r} for batch {batch_n}.",
                    kanji=kanji,
                )
            )
            continue
        classes = next(classes for c, classes in target_linked if c.id == best.id)
        _select(best, classes, is_target_linked=True)
        uncovered -= best.kanji_chars & target_kanji

    # Reinforcement: keep adding target-linked words until quota or pool exhausted.
    for c, classes in target_linked:
        if len(selected_words) >= weekly_target:
            break
        if c.id in selected_ids:
            continue
        _select(c, classes, is_target_linked=True)

    # Filler: top up remaining quota from non-target-linked eligible words.
    for c, classes in filler:
        if len(selected_words) >= weekly_target:
            break
        if c.id in selected_ids:
            continue
        _select(c, classes, is_target_linked=False)

    result.selected = selected_words
    result.target_kanji_coverage = target_coverage
    return result
