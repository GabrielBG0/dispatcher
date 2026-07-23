"""Pure formatting: (vocab fields, card type) -> card text. No DB writes, no
stored card rows -- any export can be regenerated in any grouping from the
stored vocab rows alone, per spec.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class VocabCardFields:
    kanji_form: str
    hiragana_form: str
    meaning: str


@dataclass(frozen=True)
class Card:
    front: str
    back: str


def format_vocab_card(fields: VocabCardFields) -> Card:
    is_kana_only = fields.kanji_form == fields.hiragana_form
    front = fields.kanji_form if is_kana_only else f"{fields.kanji_form}（{fields.hiragana_form}）"
    return Card(front=front, back=fields.meaning)


def format_kanji_reading_card(fields: VocabCardFields) -> Card:
    return Card(front=fields.kanji_form, back=fields.hiragana_form)
