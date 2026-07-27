from datetime import date

import httpx
import pytest
import respx

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


def test_generate_draft_uses_seen_in_class_fallback_when_no_fresh_word_covers_target(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
            status="seen_in_class",
        )
    )
    db_session.commit()

    result = batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))

    assert len(result.selected) == 1
    assert result.selected[0].used_seen_in_class_fallback is True

    vocab = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one()
    assert vocab.status == "seen_in_class"  # never promoted to "assigned"
    assert vocab.assigned_batch == 1

    detail = batch_service.get_batch_detail(db_session, batch_n=1)
    assert detail.words[0].used_seen_in_class_fallback is True


def test_generate_draft_regeneration_clears_stale_fallback_assignment_without_flipping_status(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
            status="seen_in_class",
        )
    )
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))

    vocab = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one()
    assert vocab.status == "seen_in_class"  # never flips to "available"
    assert vocab.assigned_batch == 1


def test_finalize_does_not_promote_fallback_words_status(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
            status="seen_in_class",
        )
    )
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    batch_service.finalize_batch(db_session, batch_n=1)

    vocab = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one()
    assert vocab.status == "seen_in_class"


def test_remove_word_restores_fallback_word_to_seen_in_class_not_available(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
            status="seen_in_class",
        )
    )
    db_session.commit()
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one().id

    batch_service.remove_word(db_session, batch_n=1, vocab_id=vocab_id)

    vocab = db_session.get(Vocab, vocab_id)
    assert vocab.status == "seen_in_class"
    assert vocab.assigned_batch is None


def test_remove_word_with_exclude_still_excludes_a_fallback_word(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
            status="seen_in_class",
        )
    )
    db_session.commit()
    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    vocab_id = db_session.query(Vocab).filter(Vocab.kanji_form == "愛犬").one().id

    batch_service.remove_word(db_session, batch_n=1, vocab_id=vocab_id, exclude=True)

    vocab = db_session.get(Vocab, vocab_id)
    assert vocab.status == "excluded"
    assert vocab.assigned_batch is None


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


def test_get_batch_detail_target_kanji_stable_after_finalize(db_session):
    # Regression: get_batch_detail used to recompute target_kanji via the
    # live schedule even for finalized batches. Finalizing batch 1 adds its
    # kanji to coverage, which shrinks the "still unknown" pool and makes
    # _load_schedule repack every batch -- including batch 1's own slot --
    # with whatever kanji now lands there. That showed the *next* batch's
    # kanji under batch 1's coverage grid, all with 0 covering words.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    _seed_kanji(db_session, "時", 2)
    db_session.add(
        Vocab(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available")
    )
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    before = batch_service.get_batch_detail(db_session, batch_n=1)
    assert set(before.target_kanji) == {"愛", "犬"}

    batch_service.finalize_batch(db_session, batch_n=1)

    after = batch_service.get_batch_detail(db_session, batch_n=1)
    assert set(after.target_kanji) == {"愛", "犬"}
    assert after.target_kanji_coverage["愛"] != []
    assert after.target_kanji_coverage["犬"] != []


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


def test_manual_include_word_assigns_an_available_word(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    vocab = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available"
    )
    db_session.add(vocab)
    db_session.commit()

    batch_service.manual_include_word(db_session, batch_n=1, vocab_id=vocab.id)

    db_session.refresh(vocab)
    assert vocab.status == "assigned"
    assert vocab.assigned_batch == 1


def test_manual_include_word_preserves_seen_in_class_status(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    vocab = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="seen_in_class"
    )
    db_session.add(vocab)
    db_session.commit()

    batch_service.manual_include_word(db_session, batch_n=1, vocab_id=vocab.id)

    db_session.refresh(vocab)
    assert vocab.status == "seen_in_class"  # never promoted to "assigned"
    assert vocab.assigned_batch == 1

    detail = batch_service.get_batch_detail(db_session, batch_n=1)
    assert detail.words[0].used_seen_in_class_fallback is True


def test_manual_include_word_steals_from_another_draft_batch(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    _seed_kanji(db_session, "犬", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    vocab = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=2, needs_kanji_reading=True,
    )
    db_session.add(vocab)
    db_session.commit()

    batch_service.manual_include_word(db_session, batch_n=1, vocab_id=vocab.id)

    db_session.refresh(vocab)
    assert vocab.assigned_batch == 1
    assert vocab.status == "assigned"


def test_manual_include_word_refuses_to_steal_from_finalized_batch(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.add(Batch(batch_number=2, status="finalized", weekly_target_used=126))
    vocab = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=2, needs_kanji_reading=True,
    )
    db_session.add(vocab)
    db_session.commit()

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.manual_include_word(db_session, batch_n=1, vocab_id=vocab.id)

    db_session.refresh(vocab)
    assert vocab.assigned_batch == 2  # untouched


def test_manual_include_word_refuses_future_kanji_word(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    _seed_kanji(db_session, "行", 1)
    _seed_kanji(db_session, "旅", 9)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    vocab = Vocab(
        kanji_form="旅行", hiragana_form="りょこう", meaning="travel", part_of_speech="general", status="available"
    )
    db_session.add(vocab)
    db_session.commit()

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.manual_include_word(db_session, batch_n=1, vocab_id=vocab.id)


def test_manual_exclude_word_removes_and_excludes_an_in_batch_word(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    vocab = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    db_session.add(vocab)
    db_session.commit()

    batch_service.manual_exclude_word(db_session, batch_n=1, vocab_id=vocab.id)

    db_session.refresh(vocab)
    assert vocab.status == "excluded"
    assert vocab.assigned_batch is None


def test_manual_exclude_word_excludes_an_unassigned_word_directly(db_session):
    vocab = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general", status="available"
    )
    db_session.add(vocab)
    db_session.commit()

    batch_service.manual_exclude_word(db_session, batch_n=1, vocab_id=vocab.id)

    db_session.refresh(vocab)
    assert vocab.status == "excluded"
    assert vocab.assigned_batch is None


def test_manual_exclude_word_refuses_a_word_assigned_to_a_different_batch(db_session):
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    vocab = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=2, needs_kanji_reading=True,
    )
    db_session.add(vocab)
    db_session.commit()

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.manual_exclude_word(db_session, batch_n=1, vocab_id=vocab.id)


def test_get_kanji_word_options_splits_by_location_and_ranks_top_common(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=1))
    _seed_kanji(db_session, "愛", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    in_batch_word = Vocab(
        kanji_form="愛犬", hiragana_form="あいけん", meaning="pet", part_of_speech="general",
        status="assigned", assigned_batch=1, needs_kanji_reading=True,
    )
    other_batch_word = Vocab(
        kanji_form="愛猫", hiragana_form="あいねこ", meaning="cat lover", part_of_speech="general",
        status="assigned", assigned_batch=2, needs_kanji_reading=True,
    )
    available_word = Vocab(
        kanji_form="愛情", hiragana_form="あいじょう", meaning="affection", part_of_speech="general",
        status="available",
    )
    unrelated_word = Vocab(
        kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general", status="available"
    )
    db_session.add_all([in_batch_word, other_batch_word, available_word, unrelated_word])
    db_session.commit()

    # get_kanji_word_options is purely local/DB-bound -- no network call, so
    # no respx mock is needed (and none is registered: an attempted call
    # would raise inside respx if this regressed back to touching Jisho).
    with respx.mock(assert_all_called=False):
        options = batch_service.get_kanji_word_options(db_session, batch_n=1, kanji="愛")

    assert {w.vocab_id for w in options.in_batch} == {in_batch_word.id}
    assert {w.vocab_id for w in options.other_batches} == {other_batch_word.id}
    other = options.other_batches[0]
    assert other.assigned_batch == 2
    assert other.assigned_batch_status == "draft"
    top_common_ids = {w.vocab_id for w in options.top_common}
    assert top_common_ids == {in_batch_word.id, other_batch_word.id, available_word.id}
    assert unrelated_word.id not in top_common_ids  # doesn't contain 愛


def test_get_kanji_word_options_rejects_nonexistent_batch(db_session):
    with pytest.raises(batch_service.BatchServiceError):
        batch_service.get_kanji_word_options(db_session, batch_n=99, kanji="愛")


def _jisho_word(word, reading, jlpt, is_common, meaning, pos):
    return {
        "slug": word,
        "is_common": is_common,
        "jlpt": jlpt,
        "japanese": [{"word": word, "reading": reading}],
        "senses": [{"english_definitions": [meaning], "parts_of_speech": [pos]}],
    }


@pytest.mark.asyncio
async def test_search_jisho_word_suggestions_prefers_n3_tier_and_excludes_local_words(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "投", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.add(
        Vocab(kanji_form="投資", hiragana_form="とうし", meaning="investment", part_of_speech="noun", status="available")
    )
    db_session.commit()

    response = {
        "meta": {"status": 200},
        "data": [
            _jisho_word("投", "とう", [], False, "throw", "Noun"),  # bare kanji, must be dropped
            _jisho_word("投票", "とうひょう", ["jlpt-n3"], True, "voting", "Suru verb"),
            _jisho_word("投資", "とうし", ["jlpt-n1"], True, "investment", "Suru verb"),  # already local
        ],
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(return_value=httpx.Response(200, json=response))
        suggestions = await batch_service.search_jisho_word_suggestions(db_session, batch_n=1, kanji="投")

    forms = [s.kanji_form for s in suggestions]
    assert forms == ["投票"]  # 投資 excluded as already-local; n3 tier preferred over the (excluded) n1 word
    assert suggestions[0].part_of_speech == "verb"
    assert suggestions[0].meaning == "voting"
    assert suggestions[0].includable is True  # 票 isn't scheduled anywhere -> orphan, not future
    assert suggestions[0].blocking_kanji is None
    assert suggestions[0].blocking_batch is None


@pytest.mark.asyncio
async def test_search_jisho_word_suggestions_flags_words_combining_with_an_already_seen_kanji(db_session):
    # 話 is already known (pre_n3 baseline); 投 is this batch's new target
    # kanji. 投話 (made up) pairs them -- seen_kanji must list 話. 投資's
    # other kanji (資) was never seen -> seen_kanji stays empty.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "投", 1)
    seen = Kanji(kanji="話")
    db_session.add(seen)
    db_session.flush()
    db_session.add(KanjiCoverage(kanji_id=seen.id, coverage_source="pre_n3", batch_number=None))
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.commit()

    response = {
        "meta": {"status": 200},
        "data": [
            _jisho_word("投話", "とうわ", [], True, "made-up word", "Noun"),
            _jisho_word("投資", "とうし", [], True, "investment", "Noun"),
        ],
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(return_value=httpx.Response(200, json=response))
        suggestions = await batch_service.search_jisho_word_suggestions(db_session, batch_n=1, kanji="投")

    by_form = {s.kanji_form: s for s in suggestions}
    assert by_form["投話"].seen_kanji == ["話"]
    assert by_form["投資"].seen_kanji == []


@pytest.mark.asyncio
async def test_search_jisho_word_suggestions_marks_a_suggestion_blocked_by_skip_ahead_guard(db_session):
    # new_card_weeks=2 keeps 投 and 票 in separate output batches -- 票 ends
    # up scheduled for a later batch than 1, so 投票 must come back flagged
    # includable=False even though it's still shown for context.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    _seed_kanji(db_session, "投", 1)
    _seed_kanji(db_session, "票", 9)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.commit()

    response = {
        "meta": {"status": 200},
        "data": [_jisho_word("投票", "とうひょう", ["jlpt-n3"], True, "voting", "Suru verb")],
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(return_value=httpx.Response(200, json=response))
        suggestions = await batch_service.search_jisho_word_suggestions(db_session, batch_n=1, kanji="投")

    assert len(suggestions) == 1
    assert suggestions[0].includable is False
    assert suggestions[0].blocking_kanji == "票"
    assert suggestions[0].blocking_batch == 2  # repacked: only 2 kanji total over 2 weeks


@pytest.mark.asyncio
async def test_search_jisho_word_suggestions_falls_back_to_common_tier_when_no_n3_match(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "投", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.commit()

    response = {
        "meta": {"status": 200},
        "data": [
            _jisho_word("投資", "とうし", ["jlpt-n1"], True, "investment", "Noun"),
            _jisho_word("投影", "とうえい", [], False, "projection", "Noun"),
        ],
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(return_value=httpx.Response(200, json=response))
        suggestions = await batch_service.search_jisho_word_suggestions(db_session, batch_n=1, kanji="投")

    forms = [s.kanji_form for s in suggestions]
    assert forms == ["投資"]  # no n3 tier, falls back to the common-tagged word


@pytest.mark.asyncio
async def test_search_jisho_word_suggestions_falls_back_to_all_when_no_n3_or_common_match(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "投", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.commit()

    response = {
        "meta": {"status": 200},
        "data": [_jisho_word("投影", "とうえい", [], False, "projection", "Noun")],
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(return_value=httpx.Response(200, json=response))
        suggestions = await batch_service.search_jisho_word_suggestions(db_session, batch_n=1, kanji="投")

    assert [s.kanji_form for s in suggestions] == ["投影"]


@pytest.mark.asyncio
async def test_search_jisho_word_suggestions_raises_on_jisho_failure(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "投", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.commit()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(return_value=httpx.Response(500))
        with pytest.raises(batch_service.BatchServiceError, match="Could not reach Jisho"):
            await batch_service.search_jisho_word_suggestions(db_session, batch_n=1, kanji="投")


@pytest.mark.asyncio
async def test_search_jisho_word_suggestions_rejects_nonexistent_batch(db_session):
    with pytest.raises(batch_service.BatchServiceError):
        await batch_service.search_jisho_word_suggestions(db_session, batch_n=99, kanji="投")


def test_import_and_include_jisho_word_creates_and_assigns_a_new_vocab_row(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "投", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.commit()

    vocab_id = batch_service.import_and_include_jisho_word(
        db_session, batch_n=1, kanji_form="投票", hiragana_form="とうひょう", meaning="voting", part_of_speech="verb"
    )

    vocab = db_session.get(Vocab, vocab_id)
    assert vocab.kanji_form == "投票"
    assert vocab.source == "jisho"
    assert vocab.status == "assigned"
    assert vocab.assigned_batch == 1


def test_import_and_include_jisho_word_reuses_an_existing_matching_row(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "投", 1)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    existing = Vocab(
        kanji_form="投票", hiragana_form="とうひょう", meaning="voting", part_of_speech="verb", status="excluded"
    )
    db_session.add(existing)
    db_session.commit()

    vocab_id = batch_service.import_and_include_jisho_word(
        db_session, batch_n=1, kanji_form="投票", hiragana_form="とうひょう", meaning="voting", part_of_speech="verb"
    )

    assert vocab_id == existing.id
    assert db_session.query(Vocab).filter(Vocab.kanji_form == "投票").count() == 1


def test_import_and_include_jisho_word_does_not_persist_when_include_fails(db_session):
    # 投票 contains 票, scheduled for a later batch than 1 and not yet known
    # -- skip-ahead guard should block it, and the import must not leave a
    # dangling vocab row behind.
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=2))
    _seed_kanji(db_session, "投", 1)
    _seed_kanji(db_session, "票", 9)
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.commit()

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.import_and_include_jisho_word(
            db_session, batch_n=1, kanji_form="投票", hiragana_form="とうひょう",
            meaning="voting", part_of_speech="verb",
        )

    db_session.rollback()
    assert db_session.query(Vocab).filter(Vocab.kanji_form == "投票").count() == 0
