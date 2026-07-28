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

Matching logic (which reading, which stored meaning wins which candidate
kanji) lives in app.enrichment.kana_kanji, shared with the in-app
"Kana-only word kanji forms" enrichment job (POST
/api/imports/enrich/kana-kanji-forms) -- this script is for a careful,
narrated first pass with dry-run/limit before trusting the job to run over
the whole table unattended.

Usage (from backend/):
    uv run python scripts/backfill_vocab_kanji_form.py [--dry-run] [--limit N]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.enrichment.jisho_client import JishoClient  # noqa: E402
from app.enrichment.kana_kanji import KanaKanjiOutcome, find_kanji_form  # noqa: E402
from app.kanji_utils import extract_kanji  # noqa: E402
from app.models.vocab import Vocab  # noqa: E402


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

            result = find_kanji_form(row.hiragana_form, row.meaning or "", results)

            if result.outcome is KanaKanjiOutcome.NO_KANJI_CANDIDATE:
                unchanged_no_kanji_word += 1
                continue
            if result.outcome is KanaKanjiOutcome.NO_STORED_MEANING:
                print(f"  [skip: no stored meaning to disambiguate] {row.hiragana_form}")
                skipped_no_match += 1
                continue
            if result.outcome is KanaKanjiOutcome.NO_CONFIDENT_MATCH:
                skipped_no_match += 1
                continue
            if result.outcome is KanaKanjiOutcome.AMBIGUOUS:
                print(
                    f"  [skip: ambiguous] {row.hiragana_form} (meaning={row.meaning!r}) -> "
                    f"candidates {result.candidate_words}"
                )
                skipped_ambiguous += 1
                continue

            kana_note = " [usually kana]" if result.usually_kana else ""
            print(
                f"  {row.hiragana_form}: kanji_form {row.kanji_form!r} -> {result.kanji_form!r}{kana_note}"
            )
            if not dry_run:
                row.kanji_form = result.kanji_form
                row.usually_kana = result.usually_kana
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
