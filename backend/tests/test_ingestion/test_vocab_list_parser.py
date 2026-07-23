from tests.conftest import SEED_DIR

from app.ingestion.vocab_list_parser import parse_vocab_list

VOCAB_PATH = SEED_DIR / "jlpt_n3_vocabulary.xls"


def test_parses_real_vocab_file_row_count():
    result = parse_vocab_list(VOCAB_PATH)
    # Confirmed by direct inspection: 3701 real data rows (blank rows filtered).
    assert len(result.rows) == 3701
    assert not result.warnings


def test_trims_leading_whitespace_on_hiragana_form():
    result = parse_vocab_list(VOCAB_PATH)
    rippa = next(r for r in result.rows if r.kanji_form == "りっぱ")
    assert rippa.hiragana_form == "りっぱ"
    assert not rippa.hiragana_form.startswith(" ")


def test_flags_empty_meaning_rows_for_enrichment():
    result = parse_vocab_list(VOCAB_PATH)
    empty_meaning_rows = [r for r in result.rows if r.needs_enrichment]
    # Confirmed by direct inspection: 338 rows with empty meaning.
    assert len(empty_meaning_rows) == 338
    assert all(r.meaning == "" for r in empty_meaning_rows)


def test_pos_mapping_from_meaning_prefix():
    result = parse_vocab_list(VOCAB_PATH)

    ai = next(r for r in result.rows if r.kanji_form == "愛")
    assert ai.part_of_speech == "general"  # "(noun,) love, affection"

    wakaru = next(r for r in result.rows if r.kanji_form == "分かる" and r.meaning)
    assert wakaru.part_of_speech == "verb"  # "(intransitive) to be understood, ..."

    verb_rows = [r for r in result.rows if r.part_of_speech == "verb"]
    adjective_rows = [r for r in result.rows if r.part_of_speech == "adjective"]
    adverb_rows = [r for r in result.rows if r.part_of_speech == "adverb"]
    assert verb_rows and adjective_rows and adverb_rows
