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


def _diagnose_no_eligible_cause(
    kanji: str,
    pool: list[VocabCandidate],
    known_kanji: set[str],
    schedule: dict[str, int],
    batch_n: int,
) -> tuple[str, str | None]:
    """Explains why `kanji` has zero eligible covering words, for a
    `no_eligible_covering_word` warning. `pool` should be the full
    candidate set (any status) when available, so this can tell a genuine
    source-list gap apart from words that exist but were filtered out.
    """
    containing = [c for c in pool if kanji in c.kanji_chars]
    if not containing:
        return "no_vocab_in_source", None

    blocking_future_kanji: set[str] = set()
    unblocked: list[VocabCandidate] = []
    for c in containing:
        classes = classify_word_kanji(c, known_kanji, schedule, batch_n)
        if has_future_kanji(classes):
            blocking_future_kanji |= {ch for ch, cls in classes.items() if cls is KanjiClass.FUTURE}
        else:
            unblocked.append(c)

    if not unblocked:
        blocking_kanji = min(blocking_future_kanji, key=lambda k: (schedule.get(k, math.inf), k))
        return "blocked_by_future_kanji", blocking_kanji

    # Any unblocked seen_in_class candidate here would already have been
    # claimed by the fallback pass above before this diagnosis ever runs,
    # so what's left is always some other non-available status (assigned
    # to another batch, excluded, etc).
    return "other_status_exclusion", None


def select_batch(
    candidates: list[VocabCandidate],
    coverage: set[str],
    schedule: dict[str, int],
    target_kanji: set[str],
    batch_n: int,
    config: SelectionConfig,
    today: date,
    frequency_lookup: dict[str, FrequencyInfo] | None = None,
    full_candidate_pool: list[VocabCandidate] | None = None,
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

    # Seen-in-class fallback pool: last resort for a target kanji that has
    # no fresh eligible word. Still subject to the skip-ahead guard -- a
    # future-kanji word is never allowed through, fallback or not.
    fallback_pool: list[tuple[VocabCandidate, dict[str, KanjiClass]]] = []
    if full_candidate_pool is not None:
        for c in full_candidate_pool:
            if c.status != "seen_in_class":
                continue
            classes = classify_word_kanji(c, known_kanji, schedule, batch_n)
            if has_future_kanji(classes):
                continue
            fallback_pool.append((c, classes))
        fallback_pool.sort(key=lambda pair: _sort_key(pair[0], orphan_kanji_count(pair[1]), frequency_lookup))

    selected_ids: set[int] = set()
    selected_words: list[SelectedWord] = []
    target_coverage: dict[str, list[int]] = {k: [] for k in target_kanji}
    new_word_count = 0  # excludes seen-in-class fallback selections from the weekly quota

    def _select(
        candidate: VocabCandidate,
        classes: dict[str, KanjiClass],
        is_target_linked: bool,
        used_seen_in_class_fallback: bool = False,
    ) -> None:
        nonlocal new_word_count
        covers = frozenset(k for k in candidate.kanji_chars if k in target_kanji)
        needs_reading = bool(covers) and orphan_kanji_count(classes) == 0
        selected_words.append(
            SelectedWord(
                vocab_id=candidate.id,
                is_target_linked=is_target_linked,
                needs_kanji_reading=needs_reading,
                covers_target_kanji=covers,
                used_seen_in_class_fallback=used_seen_in_class_fallback,
            )
        )
        selected_ids.add(candidate.id)
        if not used_seen_in_class_fallback:
            new_word_count += 1
        for k in covers:
            target_coverage[k].append(candidate.id)

    # Set-cover pass: guarantee every target kanji gets at least one word.
    uncovered = set(target_kanji)
    diagnostic_pool = full_candidate_pool if full_candidate_pool is not None else candidates
    for kanji in sorted(target_kanji):
        if kanji not in uncovered:
            continue
        best = next(
            (c for c, classes in target_linked if c.id not in selected_ids and kanji in c.kanji_chars),
            None,
        )
        if best is not None:
            classes = next(classes for c, classes in target_linked if c.id == best.id)
            _select(best, classes, is_target_linked=True)
            uncovered -= best.kanji_chars & target_kanji
            continue

        fallback = next(
            (
                (c, classes)
                for c, classes in fallback_pool
                if c.id not in selected_ids and kanji in c.kanji_chars
            ),
            None,
        )
        if fallback is not None:
            f_candidate, f_classes = fallback
            _select(f_candidate, f_classes, is_target_linked=True, used_seen_in_class_fallback=True)
            uncovered -= f_candidate.kanji_chars & target_kanji
            result.warnings.append(
                SelectionWarning(
                    kind="covered_by_seen_in_class_fallback",
                    detail=(
                        f"Target kanji {kanji!r} has no fresh eligible word; covered using seen-in-class "
                        f"word {f_candidate.kanji_form!r} (vocab_id={f_candidate.id}) instead."
                    ),
                    kanji=kanji,
                )
            )
            continue

        cause, blocking_kanji = _diagnose_no_eligible_cause(kanji, diagnostic_pool, known_kanji, schedule, batch_n)
        detail = f"No eligible vocab word covers target kanji {kanji!r} for batch {batch_n}."
        if cause == "blocked_by_future_kanji" and blocking_kanji is not None:
            unblock_batch = schedule.get(blocking_kanji)
            detail += f" Blocked by future kanji {blocking_kanji!r}" + (
                f" (scheduled for batch {unblock_batch})." if unblock_batch is not None else "."
            )
        result.warnings.append(
            SelectionWarning(
                kind="no_eligible_covering_word",
                detail=detail,
                kanji=kanji,
                cause=cause,
                blocking_kanji=blocking_kanji,
            )
        )

    # Reinforcement: keep adding target-linked words until quota or pool exhausted.
    for c, classes in target_linked:
        if new_word_count >= weekly_target:
            break
        if c.id in selected_ids:
            continue
        _select(c, classes, is_target_linked=True)

    # Filler: top up remaining quota from non-target-linked eligible words.
    for c, classes in filler:
        if new_word_count >= weekly_target:
            break
        if c.id in selected_ids:
            continue
        _select(c, classes, is_target_linked=False)

    result.selected = selected_words
    result.target_kanji_coverage = target_coverage
    return result
