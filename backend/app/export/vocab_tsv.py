"""Vocab Anki TSV export: split-by-part-of-speech (matching the group's
existing deck structure) or combined into one file, exporter's choice at
export time.
"""

from dataclasses import dataclass

from app.export.card_formatter import VocabCardFields, format_vocab_card

TAGS = "jlpt::n3 source::n3_supplement"

# Tag applied to a word selected via the seen-in-class fallback (see
# select_batch's SelectedWord.used_seen_in_class_fallback) -- marks it in
# Anki as a word the student already knew, not a fresh import.
FALLBACK_TAG = "seen_in_class_fallback"

POS_FILE_NAMES = {
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
    used_seen_in_class_fallback: bool = False
    # Both default False (a plain filler word); needs_kanji_reading implies
    # is_target_linked, but not the reverse (a target-linked word with an
    # orphan kanji still gets no reading card -- see README's needs_kanji_reading
    # rule). Used only to order rows so the reading deck ends up as the
    # leading run of the vocab deck -- see study_order_key.
    is_target_linked: bool = False
    needs_kanji_reading: bool = False
    # Which weekly batch this word was assigned/exported in -- None for rows
    # built outside the batch export flow (most tests), which don't care
    # about batch tagging and keep exactly TAGS/FALLBACK_TAG as before.
    batch_number: int | None = None


def tags_for_row(row: VocabExportRow) -> str:
    tags = TAGS
    if row.used_seen_in_class_fallback:
        tags += f" {FALLBACK_TAG}"
    if row.batch_number is not None:
        tags += f" batch::{row.batch_number}"
    return tags


def study_order_key(row: VocabExportRow) -> tuple:
    """Sort key shared by the vocab and kanji-reading TSV exports so that,
    on a fresh Anki import (new-card order left at "order added"), the
    reading deck is exactly the leading run of the vocab deck -- letting a
    student pace new-card introduction across both decks in lockstep instead
    of hitting a kanji reading before its meaning card.
    """
    tier = 0 if row.needs_kanji_reading else 1 if row.is_target_linked else 2
    return (tier, row.hiragana_form, row.kanji_form)


def _row_line(row: VocabExportRow) -> str:
    card = format_vocab_card(
        VocabCardFields(
            kanji_form=row.kanji_form,
            hiragana_form=row.hiragana_form,
            meaning=row.meaning,
            usually_kana=row.usually_kana,
        )
    )
    return f"{card.front}\t{card.back}\t{tags_for_row(row)}\n"


def export_vocab_tsv_combined(rows: list[VocabExportRow]) -> str:
    return "".join(_row_line(r) for r in sorted(rows, key=study_order_key))


def export_vocab_tsv_split_by_pos(rows: list[VocabExportRow]) -> dict[str, str]:
    by_pos: dict[str, list[VocabExportRow]] = {}
    for row in rows:
        pos = row.part_of_speech if row.part_of_speech in POS_FILE_NAMES else "general"
        by_pos.setdefault(pos, []).append(row)

    return {
        POS_FILE_NAMES[pos]: "".join(_row_line(r) for r in sorted(pos_rows, key=study_order_key))
        for pos, pos_rows in by_pos.items()
        if pos_rows
    }
