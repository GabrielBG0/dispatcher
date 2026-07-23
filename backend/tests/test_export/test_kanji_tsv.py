from app.export.kanji_tsv import export_kanji_reading_tsv
from app.export.vocab_tsv import VocabExportRow


def test_kanji_reading_tsv_front_has_no_reading():
    rows = [
        VocabExportRow(kanji_form="例えば", hiragana_form="たとえば", meaning="for example", part_of_speech="adverb"),
    ]
    content = export_kanji_reading_tsv(rows)
    assert content == "例えば\tたとえば\tjlpt::n3 source::n3_supplement\n"
