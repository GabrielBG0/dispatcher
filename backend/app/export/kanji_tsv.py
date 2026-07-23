"""Kanji reading Anki TSV export: single file for the kanji-only deck,
containing only rows where needs_kanji_reading was true at selection time.
"""

from app.export.card_formatter import VocabCardFields, format_kanji_reading_card
from app.export.vocab_tsv import TAGS, VocabExportRow


def export_kanji_reading_tsv(rows: list[VocabExportRow]) -> str:
    lines = []
    for row in rows:
        card = format_kanji_reading_card(
            VocabCardFields(kanji_form=row.kanji_form, hiragana_form=row.hiragana_form, meaning=row.meaning)
        )
        lines.append(f"{card.front}\t{card.back}\t{TAGS}\n")
    return "".join(lines)
