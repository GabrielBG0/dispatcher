from datetime import date

from app.ingestion.bccwj_frequency_loader import FrequencyInfo
from app.selection.select_batch import compute_weekly_target, select_batch
from app.selection.types import SelectionConfig, VocabCandidate


def make_candidate(id_, kanji_form, hiragana_form=None, status="available"):
    from app.kanji_utils import extract_kanji

    return VocabCandidate(
        id=id_,
        kanji_form=kanji_form,
        hiragana_form=hiragana_form or kanji_form,
        kanji_chars=frozenset(extract_kanji(kanji_form)),
        status=status,
    )


DEFAULT_CONFIG = SelectionConfig(daily_minimum=18, study_end_date=date(2026, 11, 16))
TODAY = date(2026, 7, 27)  # matches study_config.start_date


def test_skip_ahead_guard_blocks_future_kanji():
    # 行 is this week's target, 旅 is scheduled for a later week -> 旅行 excluded.
    ryokou = make_candidate(1, "旅行", "りょこう")
    candidates = [ryokou]
    result = select_batch(
        candidates,
        coverage=set(),
        schedule={"旅": 9, "行": 1},
        target_kanji={"行"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    assert result.selected == []
    assert result.warnings[0].kanji == "行"  # zero eligible words cover the target


def test_skip_ahead_guard_allows_when_blocking_kanji_already_known():
    ryokou = make_candidate(1, "旅行", "りょこう")
    result = select_batch(
        [ryokou],
        coverage={"旅"},  # already known from Genki
        schedule={"行": 1},
        target_kanji={"行"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    assert [w.vocab_id for w in result.selected] == [1]


def test_skip_ahead_guard_allows_when_blocking_kanji_is_also_a_target():
    ryokou = make_candidate(1, "旅行", "りょこう")
    result = select_batch(
        [ryokou],
        coverage=set(),
        schedule={"旅": 1, "行": 1},
        target_kanji={"旅", "行"},  # both targets this week
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    assert [w.vocab_id for w in result.selected] == [1]


def test_needs_kanji_reading_true_when_target_and_no_orphan():
    ryokou = make_candidate(1, "旅行", "りょこう")
    result = select_batch(
        [ryokou],
        coverage=set(),
        schedule={"旅": 1, "行": 1},
        target_kanji={"旅", "行"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    assert result.selected[0].needs_kanji_reading is True


def test_needs_kanji_reading_false_when_orphan_present():
    # 旅 is never scheduled and never covered (orphan) -> vocab card only, no reading card.
    ryokou = make_candidate(1, "旅行", "りょこう")
    result = select_batch(
        [ryokou],
        coverage=set(),
        schedule={"行": 1},  # 旅 absent from schedule entirely -> orphan
        target_kanji={"行"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    assert len(result.selected) == 1
    assert result.selected[0].needs_kanji_reading is False


def test_needs_kanji_reading_false_for_filler_word_with_no_target_kanji():
    filler_word = make_candidate(1, "時間", "じかん")
    result = select_batch(
        [filler_word],
        coverage={"時", "間"},  # already known, so word is filler not target-linked
        schedule={"行": 1},
        target_kanji={"行"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    # 行 has zero eligible covering words (filler_word doesn't contain it) -> warning
    assert any(w.kind == "no_eligible_covering_word" for w in result.warnings)
    assert len(result.selected) == 1
    assert result.selected[0].is_target_linked is False
    assert result.selected[0].needs_kanji_reading is False


def test_weekly_target_floors_at_daily_minimum_times_seven():
    target, floor = compute_weekly_target(
        today=date(2026, 11, 1), study_end_date=date(2026, 11, 16), remaining_words=10, daily_minimum=18
    )
    assert floor == 126
    assert target == 126  # far fewer than 126 words remain, floor wins


def test_weekly_target_rises_when_behind_pace():
    target, floor = compute_weekly_target(
        today=date(2026, 7, 27), study_end_date=date(2026, 11, 16), remaining_words=3671, daily_minimum=18
    )
    assert floor == 126
    assert target > 126  # 3671 words over ~16 weeks exceeds the floor pace


def test_remaining_weeks_clamped_to_one_at_study_end_date():
    # today == study_end_date: naive days-remaining is 0, must not divide by zero.
    target, floor = compute_weekly_target(
        today=date(2026, 11, 16), study_end_date=date(2026, 11, 16), remaining_words=140, daily_minimum=18
    )
    assert target == 140  # all remaining words dumped into the last possible week


def test_remaining_weeks_clamped_to_one_after_study_end_date():
    target, floor = compute_weekly_target(
        today=date(2026, 11, 20), study_end_date=date(2026, 11, 16), remaining_words=50, daily_minimum=18
    )
    assert target == 126  # floor wins, and this must not raise


def test_set_cover_prefers_fewer_orphan_then_more_common_word():
    # Two words both cover target kanji 愛; 愛情's second kanji (情) is
    # neither known nor scheduled -> orphan. 愛犬's second kanji (犬) is
    # already known -> zero orphans. The zero-orphan word must win.
    one_orphan = make_candidate(1, "愛情", "あいじょう")
    zero_orphan = make_candidate(2, "愛犬", "あいけん")
    result = select_batch(
        [one_orphan, zero_orphan],
        coverage={"犬"},
        schedule={"愛": 1},
        target_kanji={"愛"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    covering_ids = result.target_kanji_coverage["愛"]
    assert covering_ids[0] == 2  # 愛犬 (zero orphans) chosen over 愛情 (one orphan)


def test_set_cover_uses_bccwj_frequency_for_commonness_tiebreak():
    # Both words have zero orphan kanji and the same length; frequency data
    # should break the tie toward the more common one.
    word_a = make_candidate(1, "愛犬", "あいけん")
    word_b = make_candidate(2, "愛猫", "あいねこ")
    freq = {
        "愛犬": FrequencyInfo(core_rank=500, frequency=1000, pmw=10.0),
        "愛猫": FrequencyInfo(core_rank=50, frequency=5000, pmw=50.0),
    }
    result = select_batch(
        [word_a, word_b],
        coverage={"犬", "猫"},
        schedule={"愛": 1},
        target_kanji={"愛"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
        frequency_lookup=freq,
    )
    assert result.target_kanji_coverage["愛"][0] == 2  # 愛猫 has the better (lower) core_rank


def test_target_kanji_with_zero_eligible_words_surfaces_warning():
    result = select_batch(
        [],
        coverage=set(),
        schedule={"愛": 1},
        target_kanji={"愛"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    assert len(result.warnings) == 1
    assert result.warnings[0].kind == "no_eligible_covering_word"
    assert result.warnings[0].kanji == "愛"


def test_filler_fills_remaining_quota_after_target_linked_exhausted():
    target_word = make_candidate(1, "愛犬", "あいけん")
    filler_word = make_candidate(2, "時間", "じかん")
    config = SelectionConfig(daily_minimum=1, study_end_date=date(2026, 11, 16))  # low floor, easy to hit with 2 words
    result = select_batch(
        [target_word, filler_word],
        coverage={"犬", "時", "間"},
        schedule={"愛": 1},
        target_kanji={"愛"},
        batch_n=1,
        config=config,
        today=date(2026, 11, 15),  # 1 day left -> remaining_weeks clamps to 1 -> target = remaining_words = 2
    )
    ids = {w.vocab_id for w in result.selected}
    assert ids == {1, 2}
    filler_selection = next(w for w in result.selected if w.vocab_id == 2)
    assert filler_selection.is_target_linked is False


def test_seen_in_class_fallback_covers_kanji_with_no_fresh_word():
    # Only a seen-in-class word contains 愛; no fresh candidate covers it.
    seen = make_candidate(1, "愛犬", "あいけん", status="seen_in_class")
    result = select_batch(
        [],
        coverage=set(),
        schedule={"愛": 1},
        target_kanji={"愛"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
        full_candidate_pool=[seen],
    )
    assert [w.vocab_id for w in result.selected] == [1]
    assert result.selected[0].used_seen_in_class_fallback is True
    assert result.target_kanji_coverage["愛"] == [1]
    assert len(result.warnings) == 1
    assert result.warnings[0].kind == "covered_by_seen_in_class_fallback"


def test_skip_ahead_guard_still_blocks_seen_in_class_fallback():
    # 旅行 is seen-in-class but 旅 is a future kanji -> even fallback must
    # not use it; warning should explain why via cause/blocking_kanji.
    seen = make_candidate(1, "旅行", "りょこう", status="seen_in_class")
    result = select_batch(
        [],
        coverage=set(),
        schedule={"旅": 9, "行": 1},
        target_kanji={"行"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
        full_candidate_pool=[seen],
    )
    assert result.selected == []
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.kind == "no_eligible_covering_word"
    assert warning.cause == "blocked_by_future_kanji"
    assert warning.blocking_kanji == "旅"


def test_no_eligible_word_cause_is_no_vocab_in_source_when_pool_is_empty():
    result = select_batch(
        [],
        coverage=set(),
        schedule={"愛": 1},
        target_kanji={"愛"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
        full_candidate_pool=[],
    )
    assert result.warnings[0].cause == "no_vocab_in_source"


def test_seen_in_class_fallback_word_covering_two_targets_resolves_both_with_one_warning():
    # One seen-in-class word covers both 愛 and 犬. It gets fallback-selected
    # when the first of the two (sorted order) is processed, and since that
    # single word covers both target kanji, the second is already covered
    # by the same pick -- no separate warning for it.
    seen = make_candidate(1, "愛犬", "あいけん", status="seen_in_class")
    result = select_batch(
        [],
        coverage=set(),
        schedule={"愛": 1, "犬": 1},
        target_kanji={"愛", "犬"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
        full_candidate_pool=[seen],
    )
    assert [w.vocab_id for w in result.selected] == [1]
    assert result.target_kanji_coverage["愛"] == [1]
    assert result.target_kanji_coverage["犬"] == [1]
    assert len(result.warnings) == 1
    assert result.warnings[0].kind == "covered_by_seen_in_class_fallback"


def test_seen_in_class_fallback_does_not_count_against_weekly_target():
    # daily_minimum=0 zeroes the pacing floor, and today == study_end_date
    # clamps remaining_weeks to 1, so weekly_target == remaining_words == 2
    # (the two fresh filler candidates) -- a quota the fallback word must
    # not eat into. If the fallback counted toward the quota, only one of
    # the two fresh fillers would get selected.
    seen = make_candidate(1, "愛犬", "あいけん", status="seen_in_class")
    filler_1 = make_candidate(2, "時間", "じかん")
    filler_2 = make_candidate(3, "国語", "こくご")
    config = SelectionConfig(daily_minimum=0, study_end_date=date(2026, 7, 27))
    result = select_batch(
        [filler_1, filler_2],
        coverage=set(),
        schedule={"愛": 1},
        target_kanji={"愛"},
        batch_n=1,
        config=config,
        today=date(2026, 7, 27),
        full_candidate_pool=[seen],
    )
    ids = {w.vocab_id for w in result.selected}
    assert ids == {1, 2, 3}  # fallback word plus both fresh fillers
    fallback_selection = next(w for w in result.selected if w.vocab_id == 1)
    assert fallback_selection.used_seen_in_class_fallback is True


def test_zero_coverage_default_run_produces_sane_result():
    # The actual launch state: no Anki export imported yet, kanji_coverage
    # is completely empty. Selection must still work, not treat this as
    # a special/degenerate case.
    words = [make_candidate(1, "愛犬", "あいけん"), make_candidate(2, "愛猫", "あいねこ")]
    result = select_batch(
        words,
        coverage=set(),  # nothing known at all
        schedule={"愛": 1, "犬": 1, "猫": 1},  # all targets this week too
        target_kanji={"愛", "犬", "猫"},
        batch_n=1,
        config=DEFAULT_CONFIG,
        today=TODAY,
    )
    assert len(result.selected) == 2
    assert not result.warnings
    assert all(w.needs_kanji_reading for w in result.selected)  # no orphans, all target-linked
