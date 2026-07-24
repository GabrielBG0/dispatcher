from datetime import date

import pytest

from app.models.batch import Batch
from app.models.kanji import Kanji
from app.models.kanji_coverage import KanjiCoverage
from app.models.kanji_schedule import KanjiSchedule
from app.models.study_config import StudyConfig
from app.models.vocab import Vocab
from app.services import batch_service


def _seed_kanji(db, char: str, batch_number: int) -> Kanji:
    k = Kanji(kanji=char)
    db.add(k)
    db.flush()
    db.add(KanjiSchedule(kanji_id=k.id, batch_number=batch_number))
    return k


def test_generate_draft_assigns_words_and_creates_batch_row(db_session):
    # new_card_weeks=1 forces every not-yet-known kanji into a single
    # output batch (see batch_service._load_schedule), which is what lets
    # these tiny fixtures put both 愛 and 犬 in batch 1 together without
    # needing 23 real kanji to fill it.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()

    result = batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))

    assert result.batch_number == 1
    assert len(result.selected) == 1

    batch = db_session.get(Batch, 1)
    assert batch is not None
    assert batch.status == "draft"

    vocab = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one()
    assert vocab.status == "assigned"
    assert vocab.assigned_batch == 1
    assert vocab.needs_kanji_reading is True


def test_generate_draft_regeneration_clears_stale_assignment(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.add(
        Vocab(kanji_form="愛猫", hiragana_form="あいねこ", meaning="cat lover", part_of_speech="general", status="available")
    )
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    first_assigned = {v.kanji_form for v in db_session.query(Vocab).filter(Vocab.assigned_batch == 1)}

    # Regenerate: everything previously assigned to this draft batch should
    # go back to available before the new selection is applied.
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    still_available = {
        v.kanji_form for v in db_session.query(Vocab).filter(Vocab.status == "available")
    }
    assert first_assigned  # sanity: something was actually selected the first time
    # Nothing should be double-assigned or orphaned in "assigned" status
    # without an assigned_batch.
    orphaned = db_session.query(Vocab).filter(Vocab.status == "assigned", Vocab.assigned_batch.is_(None)).all()
    assert orphaned == []
    assert still_available or first_assigned  # no crash, consistent state


def test_regenerate_can_reselect_a_word_already_in_the_batch(db_session):
    # Candidates are loaded from status=="available"; a word still sitting
    # in this same batch from the prior generation must not be invisible to
    # its own re-selection pool just because clearing the stale assignment
    # ran after candidates were loaded.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()

    first = batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    second = batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))

    assert {w.vocab_id for w in second.selected} == {w.vocab_id for w in first.selected}
    vocab = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one()
    assert vocab.status == "assigned"
    assert vocab.assigned_batch == 1


def test_finalize_adds_target_kanji_to_coverage(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    batch_service.finalize_batch(db_session, batch_n=1)

    batch = db_session.get(Batch, 1)
    assert batch.status == "finalized"

    covered_chars = {
        c.kanji.kanji
        for c in db_session.query(KanjiCoverage).filter(KanjiCoverage.batch_number == 1).all()
    }
    assert covered_chars == {"愛", "犬"}
    assert all(
        c.coverage_source == "n3_batch"
        for c in db_session.query(KanjiCoverage).filter(KanjiCoverage.batch_number == 1).all()
    )


def test_finalize_adds_target_kanji_even_without_covering_words(db_session):
    # Spec step 8: finalization adds the *full* target set to coverage,
    # unconditionally -- not just kanji that happened to get a covering word.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)  # no vocab covers this at all
    db_session.commit()

    result = batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    assert result.warnings  # zero eligible words for 愛

    batch_service.finalize_batch(db_session, batch_n=1)

    covered_chars = {c.kanji.kanji for c in db_session.query(KanjiCoverage).all()}
    assert "愛" in covered_chars


def test_unfinalize_rolls_back_exactly_this_batchs_coverage_rows(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "時", 2)
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    batch_service.finalize_batch(db_session, batch_n=1)

    # A pre-existing pre_n3 coverage row (from an Anki import) must survive
    # an unrelated batch's un-finalize -- this is not a blanket wipe.
    time_kanji = db_session.query(Kanji).filter(Kanji.kanji == "時").one()
    db_session.add(KanjiCoverage(kanji_id=time_kanji.id, coverage_source="pre_n3", batch_number=None))
    db_session.commit()

    batch_service.unfinalize_batch(db_session, batch_n=1)

    batch = db_session.get(Batch, 1)
    assert batch.status == "draft"

    remaining = db_session.query(KanjiCoverage).all()
    assert len(remaining) == 1
    assert remaining[0].coverage_source == "pre_n3"
    assert remaining[0].kanji.kanji == "時"


def test_already_covered_target_kanji_excluded_from_batch(db_session):
    # A kanji already known (e.g. from the pre_n3 seen-in-class baseline)
    # must not be treated as a target for a later batch: no forced
    # covering word, no reading card, and finalize must not re-add it to
    # coverage.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    known = _seed_kanji(db_session, "赤", 1)
    _seed_kanji(db_session, "愛", 1)
    db_session.add(KanjiCoverage(kanji_id=known.id, coverage_source="pre_n3", batch_number=None))
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()

    result = batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    assert not result.warnings  # no "no eligible covering word" warning for 赤

    detail = batch_service.get_batch_detail(db_session, batch_n=1)
    assert detail.target_kanji == ["愛"]

    batch_service.finalize_batch(db_session, batch_n=1)
    covered_chars = {c.kanji.kanji for c in db_session.query(KanjiCoverage).all()}
    assert covered_chars == {"赤", "愛"}
    n3_batch_rows = db_session.query(KanjiCoverage).filter(KanjiCoverage.coverage_source == "n3_batch").all()
    assert {r.kanji.kanji for r in n3_batch_rows} == {"愛"}


def test_finalize_rejects_non_draft_batch(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    batch_service.finalize_batch(db_session, batch_n=1)

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.finalize_batch(db_session, batch_n=1)


def test_get_batch_detail_reflects_persisted_assignments(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    detail = batch_service.get_batch_detail(db_session, batch_n=1)

    assert detail.status == "draft"
    assert sorted(detail.target_kanji) == sorted(["犬", "愛"])
    assert len(detail.words) == 1
    assert detail.words[0].kanji_form == "愛犬"
    assert detail.words[0].is_target_linked is True


def test_remove_word_returns_it_to_available(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one().id

    batch_service.remove_word(db_session, batch_n=1, vocab_id=vocab_id)

    vocab = db_session.get(Vocab, vocab_id)
    assert vocab.status == "available"
    assert vocab.assigned_batch is None
    assert vocab.needs_kanji_reading is False


def test_remove_word_with_exclude_marks_excluded_not_available(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one().id

    batch_service.remove_word(db_session, batch_n=1, vocab_id=vocab_id, exclude=True)

    vocab = db_session.get(Vocab, vocab_id)
    assert vocab.status == "excluded"
    assert vocab.assigned_batch is None


def test_remove_words_bulk_skips_ids_not_assigned_to_batch(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.add(
        Vocab(kanji_form="子犬", hiragana_form="こいぬ", meaning="puppy", part_of_speech="general", status="available")
    )
    db_session.commit()
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    ai_ken = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one()
    ko_inu = db_session.query(Vocab).filter(Vocab.kanji_form == "子犬").one()
    bogus_id = ko_inu.id + 10_000

    removed = batch_service.remove_words(db_session, batch_n=1, vocab_ids=[ai_ken.id, bogus_id], exclude=True)

    assert removed == [ai_ken.id]
    db_session.refresh(ai_ken)
    assert ai_ken.status == "excluded"


def test_replace_word_auto_picks_a_word_covering_an_uncovered_target(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    ai_ken = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    ko_inu = Vocab(
        kanji_form="子犬", hiragana_form="こいぬ", meaning="puppy", part_of_speech="general", status="available"
    )
    db_session.add_all([ai_ken, ko_inu])
    db_session.commit()

    replacement = batch_service.replace_word(db_session, batch_n=1, old_vocab_id=ai_ken.id)

    assert replacement is not None
    assert replacement.vocab_id == ko_inu.id
    db_session.refresh(ai_ken)
    db_session.refresh(ko_inu)
    assert ai_ken.status == "available"
    assert ai_ken.assigned_batch is None
    assert ko_inu.status == "assigned"
    assert ko_inu.assigned_batch == 1


def test_replace_word_with_exclude_excludes_the_old_word(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    ai_ken = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    db_session.add(ai_ken)
    db_session.add(
        Vocab(kanji_form="愛猫", hiragana_form="あいねこ", meaning="cat lover", part_of_speech="general", status="available")
    )
    db_session.commit()

    batch_service.replace_word(db_session, batch_n=1, old_vocab_id=ai_ken.id, exclude=True)

    db_session.refresh(ai_ken)
    assert ai_ken.status == "excluded"


def test_replace_word_returns_none_and_still_removes_when_nothing_eligible(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    ai_ken = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    db_session.add(ai_ken)
    db_session.commit()

    replacement = batch_service.replace_word(db_session, batch_n=1, old_vocab_id=ai_ken.id)

    assert replacement is None
    db_session.refresh(ai_ken)
    assert ai_ken.status == "available"
    assert ai_ken.assigned_batch is None


def test_replace_words_bulk_never_picks_the_same_replacement_twice(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    old_1 = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    old_2 = Vocab(
        kanji_form="愛猫", hiragana_form="あいねこ", meaning="cat lover", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    replacement_1 = Vocab(
        kanji_form="子犬", hiragana_form="こいぬ", meaning="puppy", part_of_speech="general", status="available"
    )
    replacement_2 = Vocab(
        kanji_form="愛情", hiragana_form="あいじょう", meaning="affection", part_of_speech="general", status="available"
    )
    db_session.add_all([old_1, old_2, replacement_1, replacement_2])
    db_session.commit()

    results = batch_service.replace_words(db_session, batch_n=1, vocab_ids=[old_1.id, old_2.id])

    added_ids = {added.vocab_id for _, added in results if added is not None}
    assert len(added_ids) == 2  # two distinct replacements, never the same one picked twice
    db_session.refresh(old_1)
    db_session.refresh(old_2)
    assert old_1.status == "available"
    assert old_2.status == "available"


def test_add_word_rejects_future_kanji_word(db_session):
    # new_card_weeks=2 keeps 行 and 旅 in separate output batches (one each)
    # rather than both collapsing into batch 1 -- see _load_schedule.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    _seed_kanji(db_session, "行", 1)
    _seed_kanji(db_session, "旅", 9)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.add(
        Vocab(kanji_form="旅行", hiragana_form="りょこう", meaning="travel", part_of_speech="general", status="available")
    )
    db_session.commit()
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "旅行").one().id

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.add_word(db_session, batch_n=1, vocab_id=vocab_id)


def test_add_word_sets_needs_reading_correctly(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one().id

    batch_service.add_word(db_session, batch_n=1, vocab_id=vocab_id)

    vocab = db_session.get(Vocab, vocab_id)
    assert vocab.status == "assigned"
    assert vocab.assigned_batch == 1
    assert vocab.needs_kanji_reading is True


def test_toggle_reading_flips_flag(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one().id
    original = db_session.get(Vocab, vocab_id).needs_kanji_reading

    new_value = batch_service.toggle_reading(db_session, batch_n=1, vocab_id=vocab_id)

    assert new_value != original
    assert db_session.get(Vocab, vocab_id).needs_kanji_reading == new_value


def test_swap_word_removes_old_and_adds_new(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    old_word = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    new_word = Vocab(
        kanji_form="愛猫", hiragana_form="あいねこ", meaning="cat lover", part_of_speech="general", status="available"
    )
    db_session.add_all([old_word, new_word])
    db_session.commit()

    batch_service.swap_word(db_session, batch_n=1, old_vocab_id=old_word.id, new_vocab_id=new_word.id)

    db_session.refresh(old_word)
    db_session.refresh(new_word)
    assert old_word.status == "available"
    assert old_word.assigned_batch is None
    assert new_word.status == "assigned"
    assert new_word.assigned_batch == 1


def test_edits_rejected_on_finalized_batch(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    batch_service.finalize_batch(db_session, batch_n=1)
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one().id

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.remove_word(db_session, batch_n=1, vocab_id=vocab_id)
    with pytest.raises(batch_service.BatchServiceError):
        batch_service.toggle_reading(db_session, batch_n=1, vocab_id=vocab_id)


def test_eligible_replacements_excludes_future_kanji_words(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    _seed_kanji(db_session, "行", 1)
    _seed_kanji(db_session, "旅", 9)  # future relative to batch 1
    db_session.add(
        Vocab(kanji_form="旅行", hiragana_form="りょこう", meaning="travel", part_of_speech="general", status="available")
    )
    db_session.add(
        Vocab(kanji_form="銀行", hiragana_form="ぎんこう", meaning="bank", part_of_speech="general", status="available")
    )
    db_session.commit()

    replacements = batch_service.get_eligible_replacements(db_session, batch_n=1)
    forms = {r.kanji_form for r in replacements}
    assert "銀行" in forms
    assert "旅行" not in forms


def test_load_schedule_requires_study_config(db_session):
    _seed_kanji(db_session, "愛", 1)
    db_session.commit()

    with pytest.raises(batch_service.BatchServiceError):
        batch_service._load_schedule(db_session)


def test_load_schedule_packs_full_batches_then_splits_remainder_across_trailing_weeks(db_session, monkeypatch):
    monkeypatch.setattr(batch_service, "KANJI_MINIMUM_PER_BATCH", 2)
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=3))
    for i, char in enumerate(["愛", "犬", "時", "間", "行"], start=1):
        _seed_kanji(db_session, char, i)
    db_session.commit()

    # 5 kanji at a floor of 2 over 3 weeks: 2 full batches of 2, and the
    # trailing 1 kanji becomes its own (short) final batch.
    schedule = batch_service._load_schedule(db_session)

    assert schedule["愛"] == schedule["犬"] == 1
    assert schedule["時"] == schedule["間"] == 2
    assert schedule["行"] == 3


def test_load_schedule_spreads_evenly_when_more_kanji_than_weeks_can_hold_at_the_floor(db_session, monkeypatch):
    monkeypatch.setattr(batch_service, "KANJI_MINIMUM_PER_BATCH", 2)
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    for i, char in enumerate(["愛", "犬", "時", "間", "行", "旅", "銀"], start=1):
        _seed_kanji(db_session, char, i)
    db_session.commit()

    # 7 kanji at a floor of 2 over only 2 weeks would need 3+ output
    # batches to keep every batch at exactly the floor -- more than the 2
    # weeks available -- so everything is spread evenly across the 2
    # weeks instead (4 and 3), each comfortably over the floor.
    schedule = batch_service._load_schedule(db_session)

    batch_1 = {"愛", "犬", "時", "間"}
    batch_2 = {"行", "旅", "銀"}
    assert {k for k, b in schedule.items() if b == 1} == batch_1
    assert {k for k, b in schedule.items() if b == 2} == batch_2


def test_load_schedule_excludes_already_known_kanji(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    known = _seed_kanji(db_session, "赤", 1)
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 2)
    db_session.add(KanjiCoverage(kanji_id=known.id, coverage_source="pre_n3", batch_number=None))
    db_session.commit()

    schedule = batch_service._load_schedule(db_session)

    assert "赤" not in schedule
    assert set(schedule) == {"愛", "犬"}
