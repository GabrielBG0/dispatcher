from tests.conftest import FIXTURES_DIR, SEED_DIR

from app.ingestion.anki_export_parser import ParsedAnkiRow, parse_anki_export
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


def test_apply_seen_in_class_clears_stale_batch_assignment(db_session):
    # A word can already be sitting in a draft batch (assigned before this
    # Anki export was imported/re-imported). Marking it seen_in_class must
    # pull it out of that batch -- it's no longer eligible for selection --
    # not just flip status and leave assigned_batch/needs_kanji_reading stale.
    vocab_parsed = parse_vocab_list(VOCAB_PATH)
    upsert_vocab_rows(db_session, vocab_parsed.rows, source="jlpt_n3_vocabulary.xls")

    kirei_vocab = db_session.query(Vocab).filter(Vocab.hiragana_form == "きれい").first()
    kirei_vocab.status = "assigned"
    kirei_vocab.assigned_batch = 3
    kirei_vocab.needs_kanji_reading = True
    db_session.commit()

    anki_parsed = parse_anki_export(ANKI_FIXTURE_PATH)
    apply_seen_in_class(db_session, anki_parsed.rows)

    db_session.refresh(kirei_vocab)
    assert kirei_vocab.status == "seen_in_class"
    assert kirei_vocab.assigned_batch is None
    assert kirei_vocab.needs_kanji_reading is False


def test_apply_seen_in_class_never_matches_on_bare_reading_alone(db_session):
    # 空く and 開く are both read あく but are genuinely different words.
    # Matching is kanji_form-only by design (no reading fallback at all),
    # so a bare-reading candidate never matches anything, even when only
    # one of the two vocab rows happens to share that reading.
    aku_1 = Vocab(kanji_form="空く", hiragana_form="あく", meaning="to become empty", part_of_speech="verb", status="available")
    aku_2 = Vocab(kanji_form="開く", hiragana_form="あく", meaning="to open", part_of_speech="verb", status="available")
    db_session.add_all([aku_1, aku_2])
    db_session.commit()

    row = ParsedAnkiRow(fields=["あく"], match_candidates=["あく"])
    stats = apply_seen_in_class(db_session, [row])

    assert stats.updated == 0
    db_session.refresh(aku_1)
    db_session.refresh(aku_2)
    assert aku_1.status == "available"
    assert aku_2.status == "available"


def test_apply_seen_in_class_kanji_form_match_disambiguates_homophones(db_session):
    # Same あく homophone pair, but this time the Anki field gives the
    # kanji spelling too ("開く（あく）", reduced by paren-stripping to
    # the candidate "開く") -- the exact kanji_form match identifies 開く
    # unambiguously, leaving 空く untouched.
    aku_1 = Vocab(kanji_form="空く", hiragana_form="あく", meaning="to become empty", part_of_speech="verb", status="available")
    aku_2 = Vocab(kanji_form="開く", hiragana_form="あく", meaning="to open", part_of_speech="verb", status="available")
    db_session.add_all([aku_1, aku_2])
    db_session.commit()

    row = ParsedAnkiRow(fields=["開く（あく）"], match_candidates=["開く"])
    stats = apply_seen_in_class(db_session, [row])

    assert stats.updated == 1
    db_session.refresh(aku_1)
    db_session.refresh(aku_2)
    assert aku_1.status == "available"
    assert aku_2.status == "seen_in_class"


def test_apply_seen_in_class_does_not_substitute_a_different_homophone(db_session):
    # Real regression: a "Kanji" deck row ["Japanese Kanji", "会う", "あう",
    # tags] names 会う, which isn't in the vocab table. 合う is, and
    # happens to share the reading あう. With kanji_form-only matching,
    # neither the bare "会う" candidate nor the bare "あう" candidate
    # matches anything -- 合う is correctly left untouched, since the deck
    # never actually named it.
    au_gou = Vocab(kanji_form="合う", hiragana_form="あう", meaning="to fit/match", part_of_speech="verb", status="available")
    db_session.add(au_gou)
    db_session.commit()

    row = ParsedAnkiRow(
        fields=["Japanese Kanji", "会う", "あう", "common_word jlpt::n5"],
        kanji_chars={"会"},
        match_candidates=["Japanese Kanji", "会う", "あう", "common_word jlpt::n5"],
    )
    stats = apply_seen_in_class(db_session, [row])

    assert stats.updated == 0
    db_session.refresh(au_gou)
    assert au_gou.status == "available"

    # Same false-attribution risk via a combined field instead of separate
    # columns: "会う（あう）" reduces to candidate "会う", which still
    # isn't in the vocab table -- must not fall back to 合う either.
    row2 = ParsedAnkiRow(fields=["会う（あう）"], kanji_chars={"会"}, match_candidates=["会う"])
    stats2 = apply_seen_in_class(db_session, [row2])

    assert stats2.updated == 0
    db_session.refresh(au_gou)
    assert au_gou.status == "available"


def test_apply_seen_in_class_matches_kana_only_vocab_via_kanji_form(db_session):
    # Kana-only vocab rows store kanji_form == hiragana_form, so they
    # match through the same exact kanji_form lookup as kanji-bearing
    # words -- no separate reading-based path is needed for them.
    kirei = Vocab(kanji_form="きれい", hiragana_form="きれい", meaning="pretty", part_of_speech="adjective", status="available")
    db_session.add(kirei)
    db_session.commit()

    row = ParsedAnkiRow(fields=["きれい"], match_candidates=["きれい"])
    stats = apply_seen_in_class(db_session, [row])

    assert stats.updated == 1
    db_session.refresh(kirei)
    assert kirei.status == "seen_in_class"
