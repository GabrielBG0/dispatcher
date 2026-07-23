from tests.conftest import FIXTURES_DIR

from app.ingestion.anki_export_parser import parse_anki_export

FIXTURE_PATH = FIXTURES_DIR / "anki_export_sample.tsv"


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
