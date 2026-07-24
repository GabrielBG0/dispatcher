"""One-time backfill: re-fetch Jisho data for vocab rows that were enriched
before the meaning format changed, and rewrite `meaning` in the new
numbered/slash-joined style, capped at the top 2 senses (see
`app.enrichment.jobs.format_meaning`).

Rows are re-fetched from Jisho rather than reparsed from the stored string
because the old format joined definitions with ", " -- and some Jisho
definitions contain a literal comma inside parentheses (e.g. "relationship
(between, among)"), so splitting the stored text back apart is lossy. The
live API response has no such ambiguity.

Target set: any vocab row whose `meaning` contains "; ", which is exactly
the old multi-sense join separator -- i.e. words that had more than one
Jisho meaning. Single-sense rows are untouched (nothing to correct).

Usage (from backend/):
    uv run python scripts/backfill_vocab_meaning_format.py [--dry-run] [--limit N]
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal  # noqa: E402
from app.enrichment.jisho_client import JishoClient  # noqa: E402
from app.enrichment.jobs import format_meaning  # noqa: E402
from app.models.vocab import Vocab  # noqa: E402


async def backfill(dry_run: bool, limit: int | None) -> None:
    db = SessionLocal()
    client = JishoClient()
    try:
        rows = db.query(Vocab).filter(Vocab.meaning.like("%; %")).all()
        if limit is not None:
            rows = rows[:limit]
        print(f"Found {len(rows)} vocab rows with the old multi-sense format.")

        updated = 0
        unchanged = 0
        skipped = 0
        for row in rows:
            try:
                results = await client.search_words(row.kanji_form)
                match = next(
                    (
                        r
                        for r in results
                        if r.word == row.kanji_form and (not r.reading or r.reading == row.hiragana_form)
                    ),
                    results[0] if results else None,
                )
            except Exception as exc:  # noqa: BLE001 - one bad lookup shouldn't abort the run
                print(f"  [error] {row.kanji_form}: {exc}")
                skipped += 1
                continue

            if not match or not match.senses:
                print(f"  [skip] {row.kanji_form}: no Jisho match on re-fetch")
                skipped += 1
                continue

            new_meaning = format_meaning(match.senses)
            if new_meaning == row.meaning:
                unchanged += 1
                continue

            print(f"  {row.kanji_form}: {row.meaning!r} -> {new_meaning!r}")
            if not dry_run:
                row.meaning = new_meaning
            updated += 1

        if not dry_run:
            db.commit()

        print(
            f"\n{'Would update' if dry_run else 'Updated'} {updated}, "
            f"unchanged {unchanged}, skipped {skipped}."
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
