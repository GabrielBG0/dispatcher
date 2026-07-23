from tests.conftest import FIXTURES_DIR, SEED_DIR

from app.ingestion.anki_export_parser import parse_anki_export
from app.ingestion.kanji_schedule_parser import parse_kanji_schedule
from app.ingestion.upsert import (
    apply_seen_in_class,
    upsert_kanji_schedule_rows,
    upsert_vocab_rows,
)
from app.ingestion.vocab_list_parser import parse_vocab_list
from app.models.kanji import Kanji
from app.models.kanji_coverage import KanjiCoverage
from app.models.kanji_schedule import KanjiSchedule
from app.models.vocab import Vocab

VOCAB_PATH = SEED_DIR / "jlpt_n3_vocabulary.xls"
SCHEDULE_PATH = SEED_DIR / "n3_kanji_merged.xlsx"
ANKI_FIXTURE_PATH = FIXTURES_DIR / "anki_export_sample.tsv"


def test_vocab_upsert_keeps_distinct_senses_dedupes_true_duplicates(db_session):
    parsed = parse_vocab_list(VOCAB_PATH)
    stats = upsert_vocab_rows(db_session, parsed.rows, source="jlpt_n3_vocabulary.xls")

    # 3701 parsed rows: 30 are true (kanji_form, hiragana_form, meaning)
    # duplicates, plus 13 more where the same word/reading appears twice --
    # once with a meaning, once blank (an incomplete duplicate listing, not
    # a distinct sense; confirmed by direct inspection). Both collapse.
    assert stats.inserted == 3701 - 30 - 13
    assert stats.skipped_existing == 30 + 13

    row_count = db_session.query(Vocab).count()
    assert row_count == stats.inserted

    # Genuine same-reading-different-sense homonyms stay as separate rows.
    iya_rows = db_session.query(Vocab).filter(Vocab.kanji_form == "いや").all()
    assert len(iya_rows) == 2
    assert {r.meaning for r in iya_rows} == {
        "(noun) disagreeable, detestable, unpleasant, reluctant",
        "(noun) no, the noes",
    }


def test_vocab_upsert_after_enrichment_does_not_recreate_blank_row(db_session):
    # Regression test: found via manual browser testing. Enrichment fills
    # in a previously-blank `meaning`, which changes the natural key. A
    # naive re-import of the same unchanged source file must not then see
    # "no match" and insert a duplicate blank-meaning row alongside the
    # now-enriched one.
    parsed = parse_vocab_list(VOCAB_PATH)
    upsert_vocab_rows(db_session, parsed.rows, source="jlpt_n3_vocabulary.xls")

    blank_row = db_session.query(Vocab).filter(Vocab.meaning == "").first()
    assert blank_row is not None
    kanji_form, hiragana_form = blank_row.kanji_form, blank_row.hiragana_form

    # Simulate Jisho enrichment filling in the meaning on the existing row.
    blank_row.meaning = "to become clear (enriched)"
    db_session.commit()

    # Re-import the same, unchanged source file.
    second = upsert_vocab_rows(db_session, parsed.rows, source="jlpt_n3_vocabulary.xls")

    matching_rows = (
        db_session.query(Vocab)
        .filter(Vocab.kanji_form == kanji_form, Vocab.hiragana_form == hiragana_form)
        .all()
    )
    assert len(matching_rows) == 1  # not duplicated
    assert matching_rows[0].meaning == "to become clear (enriched)"  # enrichment preserved
    assert second.inserted == 0


def test_vocab_upsert_is_idempotent(db_session):
    parsed = parse_vocab_list(VOCAB_PATH)
    first = upsert_vocab_rows(db_session, parsed.rows, source="jlpt_n3_vocabulary.xls")
    second = upsert_vocab_rows(db_session, parsed.rows, source="jlpt_n3_vocabulary.xls")

    assert second.inserted == 0
    assert second.skipped_existing == len(parsed.rows)
    assert db_session.query(Vocab).count() == first.inserted


def test_kanji_schedule_upsert_creates_kanji_and_schedule(db_session):
    parsed = parse_kanji_schedule(SCHEDULE_PATH)
    stats = upsert_kanji_schedule_rows(db_session, parsed.rows)

    assert stats.inserted == 792
    assert db_session.query(Kanji).count() == 792
    assert db_session.query(KanjiSchedule).count() == 792

    ai_kanji = db_session.query(Kanji).filter(Kanji.kanji == "愛").one()
    ai_schedule = db_session.query(KanjiSchedule).filter(KanjiSchedule.kanji_id == ai_kanji.id).one()
    assert ai_schedule.batch_number == 11
    assert ai_kanji.difficulty_rank == 526


def test_kanji_schedule_upsert_is_idempotent(db_session):
    parsed = parse_kanji_schedule(SCHEDULE_PATH)
    upsert_kanji_schedule_rows(db_session, parsed.rows)
    second = upsert_kanji_schedule_rows(db_session, parsed.rows)

    assert second.inserted == 0
    assert second.updated == 0
    assert second.skipped_existing == 792
    assert db_session.query(Kanji).count() == 792


def test_apply_seen_in_class_matches_vocab_and_records_pre_n3_coverage(db_session):
    vocab_parsed = parse_vocab_list(VOCAB_PATH)
    upsert_vocab_rows(db_session, vocab_parsed.rows, source="jlpt_n3_vocabulary.xls")

    anki_parsed = parse_anki_export(ANKI_FIXTURE_PATH)
    stats = apply_seen_in_class(db_session, anki_parsed.rows)

    # All 5 kanji-bearing/kana words in the fixture (私,時間,学生,先生,きれい)
    # produce pre_n3 coverage for every distinct kanji character seen.
    all_kanji_chars = {"私", "時", "間", "学", "生", "先"}
    coverage_rows = db_session.query(KanjiCoverage).filter(
        KanjiCoverage.coverage_source == "pre_n3"
    ).all()
    covered_chars = {c.kanji.kanji for c in coverage_rows}
    assert covered_chars == all_kanji_chars
    assert stats.inserted == len(all_kanji_chars)

    # Vocab rows whose kanji_form/hiragana_form exactly match a fixture field
    # are marked seen_in_class (the sample vocab list happens to include some
    # kana-only reading matches like きれい).
    kirei_rows = db_session.query(Vocab).filter(Vocab.hiragana_form == "きれい").all()
    assert any(r.status == "seen_in_class" for r in kirei_rows)


def test_apply_seen_in_class_is_idempotent(db_session):
    vocab_parsed = parse_vocab_list(VOCAB_PATH)
    upsert_vocab_rows(db_session, vocab_parsed.rows, source="jlpt_n3_vocabulary.xls")

    anki_parsed = parse_anki_export(ANKI_FIXTURE_PATH)
    apply_seen_in_class(db_session, anki_parsed.rows)
    second = apply_seen_in_class(db_session, anki_parsed.rows)

    assert second.inserted == 0
    assert second.updated == 0
