"""Parser for Anki "Notes in Plain Text" exports, confirmed against
Gabriel's real full-collection export (multiple decks, real column
semantics vary per deck). Stays field-generic by design: rather than
assuming a fixed column is "the word", it extracts every CJK character
from every field and produces a match candidate per field. Real-world
wrinkles this handles:

- A field containing a literal tab or newline is quoted, doubled-quote
  escaped -- Anki's export follows CSV quoting conventions even though the
  separator is a tab, so a naive line-by-line split tears a quoted
  multi-line field into bogus extra rows. Parsed with `csv` instead.
- HTML tags *and* HTML entities (e.g. `&nbsp;`) both appear in fields.
- Vocabulary/verb/adjective/grammar decks combine the word and its
  reading into one field, e.g. "忘れる（わすれる）" -- the reading is
  stripped so the remaining kanji form can be matched exactly (see
  `apply_seen_in_class`, which deliberately never matches on a bare
  reading -- a homophone with a different kanji spelling would otherwise
  get silently substituted).
"""

import csv
import html
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion.base import ParseResult
from app.kanji_utils import extract_kanji, strip_html

_PAREN_RE = re.compile(r"^(?P<primary>.+?)[\s ]*[（(][^）)]+[）)]?\s*$")


@dataclass
class ParsedAnkiRow:
    fields: list[str]
    kanji_chars: set[str] = field(default_factory=set)
    match_candidates: list[str] = field(default_factory=list)
    source_row: int = 0


def _clean_field(raw: str) -> str:
    return html.unescape(strip_html(raw)).replace(" ", " ").strip()


def _split_candidate(text: str) -> str:
    m = _PAREN_RE.match(text)
    return m.group("primary").strip() if m else text


def parse_anki_export(path: Path) -> ParseResult[ParsedAnkiRow]:
    result: ParseResult[ParsedAnkiRow] = ParseResult()

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    data_lines = [line for line in lines if not line.startswith("#")]
    reader = csv.reader(data_lines, delimiter="\t", quotechar='"')

    for row_idx, raw_fields in enumerate(reader, start=1):
        if not raw_fields or all(not f.strip() for f in raw_fields):
            continue

        clean_fields = [_clean_field(f) for f in raw_fields]
        if not any(clean_fields):
            result.add_warning(row_idx, "row has no non-empty fields", raw_fields)
            continue

        kanji_chars: set[str] = set()
        candidates: list[str] = []
        for f_val in clean_fields:
            if not f_val:
                continue
            kanji_chars |= extract_kanji(f_val)
            candidates.append(_split_candidate(f_val))

        result.rows.append(
            ParsedAnkiRow(
                fields=clean_fields,
                kanji_chars=kanji_chars,
                match_candidates=candidates,
                source_row=row_idx,
            )
        )

    return result
