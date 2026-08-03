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


def test_kanji_reading_tsv_merges_multiple_readings_for_same_kanji_form():
    # 度 is legitimately read たび (standalone counter word) or ど (counter
    # suffix, e.g. 年度/限度) -- exporting these as two separate rows would
    # produce two Anki notes with an identical front and no way to tell them
    # apart during review, so they must merge into one card.
    rows = [
        VocabExportRow(kanji_form="度", hiragana_form="ど", meaning="degree", part_of_speech="general"),
        VocabExportRow(kanji_form="度", hiragana_form="たび", meaning="time, occasion", part_of_speech="general"),
    ]
    content = export_kanji_reading_tsv(rows)
    lines = content.strip("\n").split("\n")
    assert len(lines) == 1
    front, back, tags = lines[0].split("\t")
    assert front == "度"
    assert back == "1 - たび. 2 - ど"
    assert tags == "jlpt::n3 source::n3_supplement"


def test_kanji_reading_tsv_merge_combines_batch_tags():
    rows = [
        VocabExportRow(
            kanji_form="度", hiragana_form="たび", meaning="time, occasion", part_of_speech="general",
            batch_number=3,
        ),
        VocabExportRow(
            kanji_form="度", hiragana_form="ど", meaning="degree", part_of_speech="general", batch_number=7,
        ),
    ]
    content = export_kanji_reading_tsv(rows)
    tags = content.strip("\n").split("\t")[2]
    assert tags == "batch::3 batch::7 jlpt::n3 source::n3_supplement"


def test_kanji_reading_tsv_does_not_merge_distinct_kanji_forms():
    # Regression guard: only rows sharing the exact same kanji_form merge --
    # unrelated words must never collapse into each other.
    rows = [
        VocabExportRow(kanji_form="例えば", hiragana_form="たとえば", meaning="for example", part_of_speech="adverb"),
        VocabExportRow(kanji_form="分かる", hiragana_form="わかる", meaning="to understand", part_of_speech="verb"),
    ]
    content = export_kanji_reading_tsv(rows)
    assert len(content.strip("\n").split("\n")) == 2
