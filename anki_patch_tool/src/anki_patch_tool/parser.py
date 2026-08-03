"""Parses the plain 3-column Front/Back/Tags TSV export files this project's
own exporters produce (backend/app/export/vocab_tsv.py, kanji_tsv.py) -- no
header row, no GUID/deck column, tags as a space-separated string.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Row:
    front: str
    back: str
    tags: str


def parse_export_tsv(path: str | Path) -> list[Row]:
    rows: list[Row] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for fields in csv.reader(f, delimiter="\t"):
            if not fields or not any(field.strip() for field in fields):
                continue
            front = fields[0].strip()
            back = fields[1].strip() if len(fields) > 1 else ""
            tags = fields[2].strip() if len(fields) > 2 else ""
            if not front:
                continue
            rows.append(Row(front=front, back=back, tags=tags))
    return rows
