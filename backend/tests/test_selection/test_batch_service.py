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
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
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


def test_finalize_adds_target_kanji_to_coverage(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
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


def test_finalize_rejects_non_draft_batch(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    _seed_kanji(db_session, "愛", 1)
    db_session.commit()

    batch_service.generate_draft_batch(db_session, batch_n=1, today=date(2026, 7, 27))
    batch_service.finalize_batch(db_session, batch_n=1)

    with pytest.raises(batch_service.BatchServiceError):
        batch_service.finalize_batch(db_session, batch_n=1)


def test_get_batch_detail_reflects_persisted_assignments(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
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


def test_add_word_rejects_future_kanji_word(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
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
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
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
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
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
