from app.export.card_formatter import VocabCardFields, format_kanji_reading_card, format_vocab_card


def test_vocab_card_kanji_word_matches_spec_example():
    fields = VocabCardFields(kanji_form="例えば", hiragana_form="たとえば", meaning="for example")
    card = format_vocab_card(fields)
    assert card.front == "例えば（たとえば）"
    assert card.back == "for example"


def test_vocab_card_kana_only_word_shown_once():
    fields = VocabCardFields(kanji_form="きれい", hiragana_form="きれい", meaning="pretty, clean")
    card = format_vocab_card(fields)
    assert card.front == "きれい"
    assert card.back == "pretty, clean"


def test_kanji_reading_card_no_reading_on_front():
    fields = VocabCardFields(kanji_form="例えば", hiragana_form="たとえば", meaning="for example")
    card = format_kanji_reading_card(fields)
    assert card.front == "例えば"
    assert card.back == "たとえば"
