from app.selection.types import KanjiClass, VocabCandidate


def classify_kanji_char(
    char: str, known_kanji: set[str], schedule: dict[str, int], batch_n: int
) -> KanjiClass:
    """known_kanji must already be coverage ∪ this week's target kanji (the
    spec's "Known" definition) -- callers build that union once per batch,
    not per character.

    Note on an edge case the spec's three buckets don't explicitly cover: a
    kanji present in the schedule with batch_number <= batch_n that is
    *not* in known_kanji (i.e. a past week's target that was never actually
    covered). This cannot happen under the normal workflow, since
    finalization unconditionally adds a week's full target set to coverage
    (see spec step 8) before the next batch is ever drafted -- so any
    batch_number <= batch_n kanji is guaranteed to be Known by the time
    batch_n is drafted. If it somehow occurs anyway, this classifies it as
    ORPHAN (not FUTURE) since blocking word selection over a data
    inconsistency would be a worse failure mode than allowing it through
    without a reading card.
    """
    if char in known_kanji:
        return KanjiClass.KNOWN
    if schedule.get(char, batch_n) > batch_n:
        return KanjiClass.FUTURE
    return KanjiClass.ORPHAN


def classify_word_kanji(
    candidate: VocabCandidate, known_kanji: set[str], schedule: dict[str, int], batch_n: int
) -> dict[str, KanjiClass]:
    return {
        char: classify_kanji_char(char, known_kanji, schedule, batch_n)
        for char in candidate.kanji_chars
    }


def has_future_kanji(classes: dict[str, KanjiClass]) -> bool:
    return any(c is KanjiClass.FUTURE for c in classes.values())


def orphan_kanji_count(classes: dict[str, KanjiClass]) -> int:
    return sum(1 for c in classes.values() if c is KanjiClass.ORPHAN)
