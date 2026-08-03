"""Kanji reading Anki TSV export: single file for the kanji-only deck,
containing only rows where needs_kanji_reading was true at selection time.

A single kanji spelling can legitimately have more than one valid reading
among selected words (e.g. 度 as the standalone counter word "たび" vs. the
counter-suffix reading "ど" in 年度/限度) -- exporting one reading card per
row for these would produce multiple Anki notes sharing an identical front
with no way to tell them apart during review. Rows sharing a kanji_form are
merged into a single card whose back lists every distinct reading, using the
same numbered-sense convention as multi-sense vocab meanings
(enrichment/kana_kanji.py::format_meaning_groups).
"""

from app.enrichment.kana_kanji import format_meaning_groups
from app.export.card_formatter import VocabCardFields, format_kanji_reading_card
from app.export.vocab_tsv import VocabExportRow, study_order_key, tags_for_row


def _merged_tags(rows: list[VocabExportRow]) -> str:
    tags = {tag for row in rows for tag in tags_for_row(row).split()}
    return " ".join(sorted(tags))


def export_kanji_reading_tsv(rows: list[VocabExportRow]) -> str:
    by_kanji_form: dict[str, list[VocabExportRow]] = {}
    for row in rows:
        by_kanji_form.setdefault(row.kanji_form, []).append(row)

    # Each entry: (row used for sort ordering, front, back, tags)
    output: list[tuple[VocabExportRow, str, str, str]] = []
    for kanji_form, group in by_kanji_form.items():
        distinct_readings = sorted({r.hiragana_form for r in group})
        if len(distinct_readings) == 1:
            row = group[0]
            card = format_kanji_reading_card(
                VocabCardFields(kanji_form=row.kanji_form, hiragana_form=row.hiragana_form, meaning=row.meaning)
            )
            output.append((row, card.front, card.back, tags_for_row(row)))
        else:
            back = format_meaning_groups([[reading] for reading in distinct_readings])
            # No single row represents a merged card -- sort it at the position
            # of its alphabetically-first reading, so it never sorts later than
            # any of the vocab meaning cards it corresponds to (kana ordering
            # guarantees the earliest reading's position is <= the others').
            sort_row = min(group, key=lambda r: r.hiragana_form)
            output.append((sort_row, kanji_form, back, _merged_tags(group)))

    return "".join(
        f"{front}\t{back}\t{tags}\n" for row, front, back, tags in sorted(output, key=lambda t: study_order_key(t[0]))
    )
