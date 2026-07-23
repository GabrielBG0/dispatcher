from app.selection.classify import (
    classify_kanji_char,
    classify_word_kanji,
    has_future_kanji,
    orphan_kanji_count,
)
from app.selection.types import KanjiClass, VocabCandidate


def test_known_when_in_coverage():
    assert classify_kanji_char("旅", known_kanji={"旅"}, schedule={}, batch_n=5) == KanjiClass.KNOWN


def test_known_when_also_this_weeks_target():
    # caller is responsible for unioning coverage | target_kanji into known_kanji
    assert classify_kanji_char("旅", known_kanji={"旅"}, schedule={"旅": 5}, batch_n=5) == KanjiClass.KNOWN


def test_future_when_scheduled_later_and_not_known():
    assert (
        classify_kanji_char("旅", known_kanji=set(), schedule={"旅": 9}, batch_n=5) == KanjiClass.FUTURE
    )


def test_future_and_known_priority_goes_to_known():
    # a kanji that's both in coverage AND scheduled later resolves to Known.
    assert (
        classify_kanji_char("旅", known_kanji={"旅"}, schedule={"旅": 9}, batch_n=5) == KanjiClass.KNOWN
    )


def test_orphan_when_absent_from_schedule_and_coverage():
    assert classify_kanji_char("Z", known_kanji=set(), schedule={}, batch_n=5) == KanjiClass.ORPHAN


def test_classify_word_kanji_and_helpers():
    candidate = VocabCandidate(id=1, kanji_form="旅行", hiragana_form="りょこう", kanji_chars=frozenset({"旅", "行"}))
    classes = classify_word_kanji(
        candidate, known_kanji={"行"}, schedule={"旅": 9}, batch_n=5
    )
    assert classes == {"旅": KanjiClass.FUTURE, "行": KanjiClass.KNOWN}
    assert has_future_kanji(classes) is True
    assert orphan_kanji_count(classes) == 0


def test_orphan_count_counts_only_orphans():
    candidate = VocabCandidate(id=1, kanji_form="旅行", hiragana_form="りょこう", kanji_chars=frozenset({"旅", "行"}))
    classes = classify_word_kanji(candidate, known_kanji={"行"}, schedule={}, batch_n=5)
    # 旅 absent from schedule and coverage -> orphan; 行 known.
    assert classes["旅"] == KanjiClass.ORPHAN
    assert orphan_kanji_count(classes) == 1
    assert has_future_kanji(classes) is False
