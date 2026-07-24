from tests.conftest import FIXTURES_DIR

from app.ingestion.anki_export_parser import parse_anki_export

FIXTURE_PATH = FIXTURES_DIR / "anki_export_sample.tsv"
REAL_FORMAT_FIXTURE_PATH = FIXTURES_DIR / "anki_export_real_format_sample.tsv"


def test_skips_comment_and_blank_lines():
    result = parse_anki_export(FIXTURE_PATH)
    # 5 data rows in the fixture; comment lines and the blank line are not rows.
    assert len(result.rows) == 5
    assert not result.warnings


def test_extracts_kanji_from_html_wrapped_fields():
    result = parse_anki_export(FIXTURE_PATH)
    watashi_row = next(r for r in result.rows if "私" in r.kanji_chars)
    assert watashi_row.kanji_chars == {"私"}
    assert "私" in watashi_row.fields


def test_kana_only_row_has_no_kanji():
    result = parse_anki_export(FIXTURE_PATH)
    kirei_row = next(r for r in result.rows if "きれい" in r.fields)
    assert kirei_row.kanji_chars == set()


def test_html_stripped_from_fields():
    result = parse_anki_export(FIXTURE_PATH)
    for row in result.rows:
        for field_val in row.fields:
            assert "<" not in field_val and ">" not in field_val


def test_multiline_quoted_field_is_one_row_not_two():
    # Real export quirk: a field containing a literal newline is quoted
    # (CSV-style), so the note spans two physical lines. A naive
    # line-by-line split would tear this into two bogus rows.
    result = parse_anki_export(REAL_FORMAT_FIXTURE_PATH)
    assert not result.warnings
    mai_row = next(r for r in result.rows if "枚" in r.kanji_chars)
    assert mai_row.fields[0] == "Japanese Grammar"
    assert "枚" in mai_row.fields[1]


def test_html_entity_unescaped():
    result = parse_anki_export(REAL_FORMAT_FIXTURE_PATH)
    dai_row = next(r for r in result.rows if r.fields[0] == "Japanese Vocabulary" and "台" in r.kanji_chars)
    assert not any("&nbsp;" in f for f in dai_row.fields)


def test_combined_kanji_reading_field_strips_to_kanji_candidate():
    result = parse_anki_export(REAL_FORMAT_FIXTURE_PATH)
    wasureru_row = next(r for r in result.rows if "忘" in r.kanji_chars)
    assert "忘れる" in wasureru_row.match_candidates
    assert "忘れる（わすれる）" not in wasureru_row.match_candidates


def test_field_with_no_parens_is_its_own_candidate():
    result = parse_anki_export(FIXTURE_PATH)
    kirei_row = next(r for r in result.rows if "きれい" in r.fields)
    assert "きれい" in kirei_row.match_candidates
