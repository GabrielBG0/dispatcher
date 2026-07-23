"""Parser for existing Genki-era Anki deck exports (TSV), used to build the
seen-in-class baseline.

No real export file exists yet (Gabriel's Genki deck hasn't been exported at
time of writing) -- this is built and tested against a hand-built fixture
matching Anki's standard "Notes in Plain Text" TSV export (optional leading
"#"-prefixed metadata comment lines, then tab-separated field rows, HTML
allowed within fields). It stays deliberately format-agnostic beyond that:
rather than assuming which column is "the word", it extracts every CJK
character from every field in a row. Matching against vocab rows and marking
kanji as pre_n3-covered happens downstream once real column semantics are
confirmed (Milestone 8).
"""

from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion.base import ParseResult
from app.kanji_utils import extract_kanji, strip_html


@dataclass
class ParsedAnkiRow:
    fields: list[str]
    kanji_chars: set[str] = field(default_factory=set)
    source_row: int = 0


def parse_anki_export(path: Path) -> ParseResult[ParsedAnkiRow]:
    result: ParseResult[ParsedAnkiRow] = ParseResult()

    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    for row_idx, line in enumerate(lines, start=1):
        line = line.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("#"):
            continue  # Anki export metadata line (#separator:tab, #html:true, ...)

        raw_fields = line.split("\t")
        clean_fields = [strip_html(f).strip() for f in raw_fields]

        if not any(clean_fields):
            result.add_warning(row_idx, "row has no non-empty fields", raw_fields)
            continue

        kanji_chars: set[str] = set()
        for f_val in clean_fields:
            kanji_chars |= extract_kanji(f_val)

        result.rows.append(
            ParsedAnkiRow(fields=clean_fields, kanji_chars=kanji_chars, source_row=row_idx)
        )

    return result
