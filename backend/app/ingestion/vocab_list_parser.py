"""Parser for the N3 master vocab list (`jlpt_n3_vocabulary.xls`).

Confirmed format (inspected directly against the real file):
- Legacy .xls, single sheet, no header row.
- Rows 0-2 are site attribution / description junk, data starts at row 3.
- 3 columns: kanji_form, hiragana_form, meaning.
- hiragana_form frequently has leading whitespace.
- meaning is sometimes empty (needs Jisho enrichment) and sometimes starts
  with a free-text part-of-speech tag in parentheses, e.g. "(noun,)",
  "(transitive)", "(Godan)", "(adverbial)".
"""

from dataclasses import dataclass
from pathlib import Path

import xlrd

from app.ingestion.base import ParseResult

_HEADER_ROWS_TO_SKIP = 3

_POS_TAG_MAP = {
    "transitive": "verb",
    "intransitive": "verb",
    "godan": "verb",
    "ichidan": "verb",
    "suru": "verb",
    "adjective": "adjective",
    "adjectival": "adjective",
    "adverb": "adverb",
    "adverbial": "adverb",
}


@dataclass
class ParsedVocabRow:
    kanji_form: str
    hiragana_form: str
    meaning: str
    part_of_speech: str
    needs_enrichment: bool
    source_row: int


def _pos_from_meaning(meaning: str) -> str:
    if meaning.startswith("("):
        end = meaning.find(")")
        if end != -1:
            tag = meaning[1:end].strip().rstrip(",").strip().lower()
            return _POS_TAG_MAP.get(tag, "general")
    return "general"


def parse_vocab_list(path: Path) -> ParseResult[ParsedVocabRow]:
    result: ParseResult[ParsedVocabRow] = ParseResult()
    wb = xlrd.open_workbook(str(path))
    sheet = wb.sheet_by_index(0)

    for row_idx in range(_HEADER_ROWS_TO_SKIP, sheet.nrows):
        raw_row = [sheet.cell_value(row_idx, c) for c in range(sheet.ncols)]
        if len(raw_row) < 2:
            result.add_warning(row_idx, "row has fewer than 2 columns", raw_row)
            continue

        kanji_form = str(raw_row[0]).strip()
        hiragana_form = str(raw_row[1]).strip()
        meaning = str(raw_row[2]).strip() if len(raw_row) > 2 else ""

        if not kanji_form and not hiragana_form:
            continue  # blank row, not a warning-worthy failure

        if not kanji_form or not hiragana_form:
            result.add_warning(row_idx, "missing kanji_form or hiragana_form", raw_row)
            continue

        result.rows.append(
            ParsedVocabRow(
                kanji_form=kanji_form,
                hiragana_form=hiragana_form,
                meaning=meaning,
                part_of_speech=_pos_from_meaning(meaning),
                needs_enrichment=not meaning,
                source_row=row_idx,
            )
        )

    return result
