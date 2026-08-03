from app.export.vocab_tsv import VocabExportRow, export_vocab_tsv_combined, export_vocab_tsv_split_by_pos, tags_for_row


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
    # None of ROWS is target-linked, so all fall in the filler tier and sort
    # by hiragana_form: うつくしい, じかん, たとえば, わかる.
    assert lines[0] == "美しい（うつくしい）\tbeautiful\tjlpt::n3 source::n3_supplement"


def test_combined_export_orders_target_linked_before_filler_then_alphabetically():
    rows = [
        VocabExportRow(kanji_form="時計", hiragana_form="とけい", meaning="clock", part_of_speech="general"),
        VocabExportRow(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet dog", part_of_speech="general",
            is_target_linked=True, needs_kanji_reading=True,
        ),
        VocabExportRow(kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general"),
        VocabExportRow(
            kanji_form="愛情", hiragana_form="あいじょう", meaning="affection", part_of_speech="general",
            is_target_linked=True, needs_kanji_reading=False,
        ),
    ]
    content = export_vocab_tsv_combined(rows)
    fronts = [line.split("\t")[0] for line in content.strip("\n").split("\n")]
    # needs_kanji_reading first, then target-linked-but-orphan, then filler;
    # alphabetical (by hiragana_form) within each tier.
    assert fronts == ["愛犬（あいけん）", "愛情（あいじょう）", "時間（じかん）", "時計（とけい）"]


def test_split_by_pos_produces_four_files():
    files = export_vocab_tsv_split_by_pos(ROWS)
    assert set(files) == {
        "Japanese Verbs.tsv",
        "Japanese Adjectives.tsv",
        "Japanese Adverbs.tsv",
        "Japanese Vocabulary.tsv",
    }
    assert "分かる（わかる）" in files["Japanese Verbs.tsv"]
    assert "美しい（うつくしい）" in files["Japanese Adjectives.tsv"]
    assert "例えば（たとえば）" in files["Japanese Adverbs.tsv"]
    assert "時間（じかん）" in files["Japanese Vocabulary.tsv"]


def test_split_by_pos_omits_empty_categories():
    files = export_vocab_tsv_split_by_pos([ROWS[0]])  # only an adverb
    assert set(files) == {"Japanese Adverbs.tsv"}


def test_unknown_pos_falls_back_to_general():
    row = VocabExportRow(kanji_form="あ", hiragana_form="あ", meaning="ah", part_of_speech="interjection")
    files = export_vocab_tsv_split_by_pos([row])
    assert set(files) == {"Japanese Vocabulary.tsv"}


def test_tags_for_row_includes_batch_number_when_set():
    row = VocabExportRow(
        kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general", batch_number=5
    )
    assert tags_for_row(row) == "jlpt::n3 source::n3_supplement batch::5"


def test_tags_for_row_omits_batch_tag_when_not_set():
    row = VocabExportRow(kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general")
    assert tags_for_row(row) == "jlpt::n3 source::n3_supplement"
