"""One-time backfill: fill in the real kanji spelling for vocab rows whose
`kanji_form` is currently just the kana reading again (e.g. kanji_form="から"
alongside hiragana_form="から", when the word is actually 殻).

Root cause: the source spreadsheet (`jlpt_n3_vocabulary.xls`) leaves
`kanji_form` equal to the reading for ~1970 rows. Some of those are
genuinely kana-only words with no kanji at all (particles like the から
"from"), and some -- like ある/いる/できる -- do have a kanji spelling that
Jisho tags "Usually written using kana alone". Both kanji_form AND
hiragana_form ending up identical on multiple homophone rows produces
duplicate-looking Anki card fronts, which is worse than showing the kanji,
so this fills in the kanji either way. For rows where the matching sense is
tagged "usually kana alone" it also sets `usually_kana=True`, which flips
card_formatter's display to kana-form-first (e.g. "ある（有る）" instead of
"有る（ある）") so the card still signals the conventional spelling while
no longer being a plain-kana duplicate.

Matching a row's stored `meaning` against Jisho's senses (via English
definition overlap) is what picks the right homophone -- e.g. 殻 "shell" vs
空 "empty" vs the から "from" particle -- for a given reading. Ambiguous
cases (no confident match, or more than one candidate kanji reaches the
match threshold) are left untouched and printed for manual review, per the
principle that a wrong kanji is worse than a missing one in a study tool.

Usage (from backend/):
    uv run python scripts/backfill_vocab_kanji_form.py [--dry-run] [--limit N]
"""

import argparse
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.enrichment.jisho_client import JishoClient, JishoWordResult, JishoWordSense  # noqa: E402
from app.kanji_utils import extract_kanji  # noqa: E402
from app.models.vocab import Vocab  # noqa: E402

_LEADING_TAG_RE = re.compile(r"^\([^)]*\)\s*")
_KANA_TAG_RE = re.compile(r"usually.*kana", re.IGNORECASE)

_MATCH_THRESHOLD = 0.6


def _normalize_meaning(text: str) -> set[str]:
    text = _LEADING_TAG_RE.sub("", text)
    text = text.replace(".", ",")
    text = re.sub(r"\d+\s*-\s*", "", text)
    return {p.strip().lower() for p in re.split(r"[,/]", text) if p.strip()}


def _is_usually_kana(sense: JishoWordSense) -> bool:
    return any(_KANA_TAG_RE.search(tag) for tag in sense.tags)


def _best_matching_sense(row_meaning_tokens: set[str], result: JishoWordResult) -> tuple[float, bool]:
    """Best (Jaccard overlap, is_usually_kana) across this entry's senses.
    is_usually_kana reflects the specific sense that produced the best
    score, since the tag can sit on one sense of a multi-sense entry.

    Jaccard (intersection / union) rather than intersection / candidate-size:
    a candidate sense phrased as a single generic word (e.g. just
    "together") would otherwise score a perfect 1.0 overlap-of-candidate
    ratio against any row meaning containing that word, out-scoring the
    real, richer-worded match -- that's exactly how いっしょ nearly matched
    to 一所 (an obscure sense literally just "together") over 一緒 (the
    actual common word, whose matching sense has other words alongside).
    Jaccard penalizes that by also counting what's in the row meaning but
    NOT in the candidate, so a thin one-word sense can't win purely on
    having a small denominator."""
    best_score = 0.0
    best_is_kana = False
    for sense in result.senses:
        cand_tokens = {d.strip().lower() for d in sense.english_definitions if d.strip()}
        if not cand_tokens:
            continue
        union = row_meaning_tokens | cand_tokens
        if not union:
            continue
        overlap = row_meaning_tokens & cand_tokens
        score = len(overlap) / len(union)
        if score > best_score:
            best_score = score
            best_is_kana = _is_usually_kana(sense)
    return best_score, best_is_kana


async def backfill(dry_run: bool, limit: int | None) -> None:
    db = SessionLocal()
    client = JishoClient()
    try:
        rows = [r for r in db.query(Vocab).all() if not extract_kanji(r.kanji_form)]
        if limit is not None:
            rows = rows[:limit]
        print(f"Found {len(rows)} vocab rows with no kanji in kanji_form.")

        updated = 0
        unchanged_no_kanji_word = 0
        skipped_ambiguous = 0
        skipped_no_match = 0

        for row in rows:
            try:
                results = await client.search_words(row.hiragana_form)
            except Exception as exc:  # noqa: BLE001 - one bad lookup shouldn't abort the run
                print(f"  [error] {row.hiragana_form}: {exc}")
                skipped_no_match += 1
                continue

            row_tokens = _normalize_meaning(row.meaning) if row.meaning else set()

            candidates = [
                r
                for r in results
                if r.reading == row.hiragana_form and r.word and extract_kanji(r.word)
            ]
            if not candidates:
                unchanged_no_kanji_word += 1
                continue

            if not row_tokens:
                print(f"  [skip: no stored meaning to disambiguate] {row.hiragana_form}")
                skipped_no_match += 1
                continue

            scored = [(c, *_best_matching_sense(row_tokens, c)) for c in candidates]
            passing = [(c, ratio, is_kana) for c, ratio, is_kana in scored if ratio >= _MATCH_THRESHOLD]

            if not passing:
                skipped_no_match += 1
                continue

            distinct_words = {c.word for c, _, _ in passing}
            if len(distinct_words) > 1:
                print(
                    f"  [skip: ambiguous] {row.hiragana_form} (meaning={row.meaning!r}) -> "
                    f"candidates {[c.word for c, _, _ in passing]}"
                )
                skipped_ambiguous += 1
                continue

            new_kanji_form, _, is_usually_kana = passing[0]
            new_kanji_form = new_kanji_form.word
            kana_note = " [usually kana]" if is_usually_kana else ""
            print(
                f"  {row.hiragana_form}: kanji_form {row.kanji_form!r} -> {new_kanji_form!r}{kana_note}"
            )
            if not dry_run:
                row.kanji_form = new_kanji_form
                row.usually_kana = is_usually_kana
            updated += 1

        if not dry_run:
            db.commit()

        print(
            f"\n{'Would update' if dry_run else 'Updated'} {updated}, "
            f"no kanji-bearing entry found {unchanged_no_kanji_word}, "
            f"ambiguous {skipped_ambiguous}, no confident match {skipped_no_match}."
        )
    finally:
        await client.aclose()
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without writing them.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N matching rows.")
    args = parser.parse_args()
    asyncio.run(backfill(dry_run=args.dry_run, limit=args.limit))


if __name__ == "__main__":
    main()
