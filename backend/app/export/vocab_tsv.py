"""Vocab Anki TSV export: split-by-part-of-speech (matching the group's
existing deck structure) or combined into one file, exporter's choice at
export time.
"""

from dataclasses import dataclass

from app.export.card_formatter import VocabCardFields, format_vocab_card

TAGS = "jlpt::n3 source::n3_supplement"

_POS_FILE_NAMES = {
    "verb": "Japanese Verbs.tsv",
    "adjective": "Japanese Adjectives.tsv",
    "adverb": "Japanese Adverbs.tsv",
    "general": "Japanese Vocabulary.tsv",
}


@dataclass(frozen=True)
class VocabExportRow:
    kanji_form: str
    hiragana_form: str
    meaning: str
    part_of_speech: str
    usually_kana: bool = False


def _row_line(row: VocabExportRow) -> str:
    card = format_vocab_card(
        VocabCardFields(
            kanji_form=row.kanji_form,
            hiragana_form=row.hiragana_form,
            meaning=row.meaning,
            usually_kana=row.usually_kana,
        )
    )
    return f"{card.front}\t{card.back}\t{TAGS}\n"


def export_vocab_tsv_combined(rows: list[VocabExportRow]) -> str:
    return "".join(_row_line(r) for r in rows)


def export_vocab_tsv_split_by_pos(rows: list[VocabExportRow]) -> dict[str, str]:
    by_pos: dict[str, list[VocabExportRow]] = {}
    for row in rows:
        pos = row.part_of_speech if row.part_of_speech in _POS_FILE_NAMES else "general"
        by_pos.setdefault(pos, []).append(row)

    return {
        _POS_FILE_NAMES[pos]: "".join(_row_line(r) for r in pos_rows)
        for pos, pos_rows in by_pos.items()
        if pos_rows
    }
