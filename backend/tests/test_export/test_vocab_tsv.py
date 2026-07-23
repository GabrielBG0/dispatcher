from app.export.vocab_tsv import VocabExportRow, export_vocab_tsv_combined, export_vocab_tsv_split_by_pos


ROWS = [
    VocabExportRow(kanji_form="例えば", hiragana_form="たとえば", meaning="for example", part_of_speech="adverb"),
    VocabExportRow(kanji_form="分かる", hiragana_form="わかる", meaning="to understand", part_of_speech="verb"),
    VocabExportRow(kanji_form="美しい", hiragana_form="うつくしい", meaning="beautiful", part_of_speech="adjective"),
    VocabExportRow(kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general"),
]


def test_combined_export_has_one_line_per_row_with_tags():
    content = export_vocab_tsv_combined(ROWS)
    lines = content.strip("\n").split("\n")
    assert len(lines) == 4
    assert lines[0] == "例えば（たとえば）\tfor example\tjlpt::n3 source::n3_supplement"


def test_split_by_pos_produces_four_files():
    files = export_vocab_tsv_split_by_pos(ROWS)
    assert set(files) == {"verbs.tsv", "adjectives.tsv", "adverbs.tsv", "general_vocab.tsv"}
    assert "分かる（わかる）" in files["verbs.tsv"]
    assert "美しい（うつくしい）" in files["adjectives.tsv"]
    assert "例えば（たとえば）" in files["adverbs.tsv"]
    assert "時間（じかん）" in files["general_vocab.tsv"]


def test_split_by_pos_omits_empty_categories():
    files = export_vocab_tsv_split_by_pos([ROWS[0]])  # only an adverb
    assert set(files) == {"adverbs.tsv"}


def test_unknown_pos_falls_back_to_general():
    row = VocabExportRow(kanji_form="あ", hiragana_form="あ", meaning="ah", part_of_speech="interjection")
    files = export_vocab_tsv_split_by_pos([row])
    assert set(files) == {"general_vocab.tsv"}
