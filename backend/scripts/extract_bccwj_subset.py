"""One-time extraction: filter the raw BCCWJ frequency corpus down to only the
rows relevant to our N3 vocab list, so the large source file never needs to be
committed to the repo.

Usage (from backend/):
    uv run python scripts/extract_bccwj_subset.py

Reads:
    data/BCCWJ_frequencylist_luw2_ver1_0.tsv  (raw corpus, gitignored, ~190MB)
    seed/jlpt_n3_vocabulary.xls               (N3 vocab list)

Writes:
    seed/bccwj_n3_frequency_subset.tsv        (committed, small)
"""

import csv
import sys
from pathlib import Path

import xlrd

BACKEND_ROOT = Path(__file__).resolve().parent.parent
RAW_BCCWJ_PATH = BACKEND_ROOT / "data" / "BCCWJ_frequencylist_luw2_ver1_0.tsv"
VOCAB_XLS_PATH = BACKEND_ROOT / "seed" / "jlpt_n3_vocabulary.xls"
OUTPUT_PATH = BACKEND_ROOT / "seed" / "bccwj_n3_frequency_subset.tsv"

OUTPUT_FIELDS = ["lemma", "core_rank", "frequency", "pmw"]


def load_vocab_kanji_forms() -> set[str]:
    wb = xlrd.open_workbook(VOCAB_XLS_PATH)
    sheet = wb.sheet_by_index(0)
    forms: set[str] = set()
    for row_idx in range(3, sheet.nrows):
        kanji_form = str(sheet.cell_value(row_idx, 0)).strip()
        if kanji_form:
            forms.add(kanji_form)
    return forms


def extract() -> None:
    if not RAW_BCCWJ_PATH.exists():
        print(f"Raw BCCWJ file not found at {RAW_BCCWJ_PATH}; nothing to extract.", file=sys.stderr)
        sys.exit(1)

    vocab_forms = load_vocab_kanji_forms()
    print(f"Loaded {len(vocab_forms)} distinct vocab kanji forms.")

    matched = 0
    seen_lemmas: set[str] = set()
    with (
        open(RAW_BCCWJ_PATH, encoding="utf-8", newline="") as infile,
        open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as outfile,
    ):
        reader = csv.DictReader(infile, delimiter="\t")
        writer = csv.DictWriter(outfile, fieldnames=OUTPUT_FIELDS, delimiter="\t")
        writer.writeheader()
        for row in reader:
            lemma = row.get("lemma", "")
            if lemma in vocab_forms and lemma not in seen_lemmas:
                # A lemma can repeat across genre-specific rows in the source;
                # keep the first (== overall-corpus-frequency-ordered) occurrence.
                writer.writerow(
                    {
                        "lemma": lemma,
                        "core_rank": row.get("core_rank", ""),
                        "frequency": row.get("frequency", ""),
                        "pmw": row.get("pmw", ""),
                    }
                )
                seen_lemmas.add(lemma)
                matched += 1

    print(f"Matched {matched} / {len(vocab_forms)} vocab forms. Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    extract()
