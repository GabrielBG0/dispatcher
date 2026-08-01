"""Kanji reading Anki TSV export: single file for the kanji-only deck,
containing only rows where needs_kanji_reading was true at selection time.
"""

from app.export.card_formatter import VocabCardFields, format_kanji_reading_card
from app.export.vocab_tsv import VocabExportRow, study_order_key, tags_for_row


def export_kanji_reading_tsv(rows: list[VocabExportRow]) -> str:
    lines = []
    for row in sorted(rows, key=study_order_key):
        card = format_kanji_reading_card(
            VocabCardFields(kanji_form=row.kanji_form, hiragana_form=row.hiragana_form, meaning=row.meaning)
        )
        lines.append(f"{card.front}\t{card.back}\t{tags_for_row(row)}\n")
    return "".join(lines)
