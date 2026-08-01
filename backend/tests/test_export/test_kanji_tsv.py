from app.export.kanji_tsv import export_kanji_reading_tsv
from app.export.vocab_tsv import VocabExportRow


def test_kanji_reading_tsv_front_has_no_reading():
    rows = [
        VocabExportRow(kanji_form="例えば", hiragana_form="たとえば", meaning="for example", part_of_speech="adverb"),
    ]
    content = export_kanji_reading_tsv(rows)
    assert content == "例えば\tたとえば\tjlpt::n3 source::n3_supplement\n"


def test_kanji_reading_tsv_sorts_alphabetically_by_hiragana_form():
    rows = [
        VocabExportRow(kanji_form="分かる", hiragana_form="わかる", meaning="to understand", part_of_speech="verb"),
        VocabExportRow(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet dog", part_of_speech="general"),
    ]
    content = export_kanji_reading_tsv(rows)
    fronts = [line.split("\t")[0] for line in content.strip("\n").split("\n")]
    assert fronts == ["愛犬", "分かる"]
