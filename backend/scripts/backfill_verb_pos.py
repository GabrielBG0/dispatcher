"""One-off backfill: reclassify vocab rows that were mismarked "verb" by the
`pos_from_jisho` bug fixed in app/enrichment/jobs.py (Noun + Suru verb words
like 集中, 握手 were previously classified as verb instead of general/noun).

Re-queries Jisho for every Vocab row currently marked part_of_speech="verb",
using the same match/priority logic as the real enrichment jobs, and updates
only the rows whose reclassification actually changes. Meanings are left
untouched.

Usage (from backend/): uv run python scripts/backfill_verb_pos.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.enrichment.jisho_client import JishoClient
from app.enrichment.jobs import pos_from_jisho
from app.models.vocab import Vocab


async def main() -> None:
    db = SessionLocal()
    client = JishoClient()
    changed = []
    not_found = []
    errors = []
    try:
        rows = db.query(Vocab).filter(Vocab.part_of_speech == "verb").all()
        total = len(rows)
        print(f"checking {total} rows currently marked verb...")

        for i, row in enumerate(rows, start=1):
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
                if match and match.senses:
                    new_pos = pos_from_jisho(match.senses[0].parts_of_speech)
                    if new_pos != "verb":
                        print(f"  {row.kanji_form} ({row.hiragana_form}): verb -> {new_pos}")
                        row.part_of_speech = new_pos
                        changed.append(row.kanji_form)
                        db.commit()
                else:
                    not_found.append(row.kanji_form)
            except Exception as exc:  # noqa: BLE001 - one bad lookup shouldn't abort the whole run
                errors.append((row.kanji_form, str(exc)))

            if i % 100 == 0:
                print(f"...{i}/{total}")

        print()
        print(f"done. {len(changed)} rows reclassified, {len(not_found)} not found, {len(errors)} errors")
        if errors:
            print("errors:", errors)
    finally:
        await client.aclose()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
