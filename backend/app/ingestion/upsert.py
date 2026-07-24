"""Idempotent upsert helpers. Re-importing the same file must not duplicate
rows -- each function is safe to call repeatedly with the same input.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ingestion.anki_export_parser import ParsedAnkiRow
from app.ingestion.kanji_schedule_parser import ParsedKanjiScheduleRow
from app.ingestion.vocab_list_parser import ParsedVocabRow
from app.models.kanji import Kanji
from app.models.kanji_coverage import KanjiCoverage
from app.models.kanji_schedule import KanjiSchedule
from app.models.vocab import Vocab


@dataclass
class UpsertStats:
    inserted: int = 0
    skipped_existing: int = 0
    updated: int = 0


def upsert_vocab_rows(db: Session, rows: list[ParsedVocabRow], source: str) -> UpsertStats:
    """Natural key is (kanji_form, hiragana_form, meaning) -- see plan for why:
    (kanji_form, hiragana_form) alone collides on genuine homonyms/distinct
    senses in the real N3 vocab list, so meaning is part of the key.

    Blank-meaning rows are a special case: Jisho enrichment fills in
    `meaning` on the existing row after import, which changes the natural
    key. Re-importing the same source file afterward would otherwise see no
    match (the key it's looking for, with an empty meaning, no longer
    exists) and insert a duplicate blank-meaning row alongside the
    already-enriched one. A blank meaning carries no information that could
    distinguish a genuine new sense, so any incoming blank-meaning row is
    matched against (kanji_form, hiragana_form) alone, regardless of
    whatever meaning the existing row now has.
    """
    stats = UpsertStats()

    existing_rows = db.query(Vocab).all()
    existing_by_triple = {(v.kanji_form, v.hiragana_form, v.meaning): v for v in existing_rows}
    existing_kf_hf: set[tuple[str, str]] = {(v.kanji_form, v.hiragana_form) for v in existing_rows}

    for row in rows:
        if not row.meaning:
            kf_hf = (row.kanji_form, row.hiragana_form)
            if kf_hf in existing_kf_hf:
                stats.skipped_existing += 1
                continue
        else:
            triple = (row.kanji_form, row.hiragana_form, row.meaning)
            if triple in existing_by_triple:
                stats.skipped_existing += 1
                continue

        db.add(
            Vocab(
                kanji_form=row.kanji_form,
                hiragana_form=row.hiragana_form,
                meaning=row.meaning,
                part_of_speech=row.part_of_speech,
                status="available",
                source=source,
            )
        )
        existing_by_triple[(row.kanji_form, row.hiragana_form, row.meaning)] = None
        existing_kf_hf.add((row.kanji_form, row.hiragana_form))
        stats.inserted += 1

    db.commit()
    return stats


def _get_or_create_kanji(db: Session, cache: dict[str, Kanji], character: str) -> Kanji:
    if character in cache:
        return cache[character]

    kanji = db.query(Kanji).filter(Kanji.kanji == character).one_or_none()
    if kanji is None:
        kanji = Kanji(kanji=character)
        db.add(kanji)
        db.flush()  # assign kanji.id without a full commit

    cache[character] = kanji
    return kanji


def upsert_kanji_schedule_rows(
    db: Session, rows: list[ParsedKanjiScheduleRow]
) -> UpsertStats:
    """Natural key is the kanji character itself. Re-importing updates the
    batch_number/difficulty_rank if the schedule file changed, rather than
    silently ignoring a correction.
    """
    stats = UpsertStats()
    kanji_cache: dict[str, Kanji] = {}

    existing_schedule = {ks.kanji_id: ks for ks in db.query(KanjiSchedule).all()}

    for row in rows:
        kanji = _get_or_create_kanji(db, kanji_cache, row.kanji)
        if kanji.difficulty_rank != row.difficulty_rank:
            kanji.difficulty_rank = row.difficulty_rank

        schedule_entry = existing_schedule.get(kanji.id)
        if schedule_entry is None:
            db.flush()  # ensure kanji.id is populated for a brand-new kanji
            db.add(KanjiSchedule(kanji_id=kanji.id, batch_number=row.batch_number))
            existing_schedule[kanji.id] = None
            stats.inserted += 1
        elif schedule_entry.batch_number != row.batch_number:
            schedule_entry.batch_number = row.batch_number
            stats.updated += 1
        else:
            stats.skipped_existing += 1

    db.commit()
    return stats


def apply_seen_in_class(db: Session, anki_rows: list[ParsedAnkiRow]) -> UpsertStats:
    """Marks vocab rows as seen_in_class when a match candidate from an Anki
    export row is an exact kanji_form match, and records every CJK
    character encountered as pre_n3 kanji coverage.

    Deliberately kanji_form-only, never a hiragana/reading fallback: a
    real deck mixes cards that name a specific kanji spelling not in the
    vocab table (e.g. "会う（あう）" where 会う isn't a vocab row) with
    cards that are just teaching kana itself (a "Hiragana" deck card for
    the letter い). A reading-based fallback matched both indiscriminately
    against the wrong homophone (合う, coincidentally sharing あう) and
    against unrelated vocab words that merely share a kana letter (胃,
    毛, 子...) with no connection to what was actually studied. Kana-only
    vocab rows aren't lost by dropping the fallback -- their kanji_form
    equals their hiragana_form, so they already match via kanji_form.

    Field-generic by design (see anki_export_parser docstring): real column
    layout is unconfirmed, so this matches on field *content*, not position.
    """
    stats = UpsertStats()

    vocab_by_kanji_form: dict[str, list[Vocab]] = {}
    for v in db.query(Vocab).all():
        vocab_by_kanji_form.setdefault(v.kanji_form, []).append(v)

    all_kanji_chars: set[str] = set()
    matched_vocab_ids: set[int] = set()

    for row in anki_rows:
        all_kanji_chars |= row.kanji_chars
        for candidate in row.match_candidates:
            for v in vocab_by_kanji_form.get(candidate, []):
                if v.id not in matched_vocab_ids and v.status != "seen_in_class":
                    v.status = "seen_in_class"
                    # A word already sitting in a draft batch must be pulled
                    # out of it -- it's no longer eligible for selection.
                    v.assigned_batch = None
                    v.needs_kanji_reading = False
                    matched_vocab_ids.add(v.id)
                    stats.updated += 1

    kanji_cache: dict[str, Kanji] = {}
    existing_pre_n3 = {
        kc.kanji_id
        for kc in db.query(KanjiCoverage).filter(KanjiCoverage.coverage_source == "pre_n3").all()
    }

    for char in all_kanji_chars:
        kanji = _get_or_create_kanji(db, kanji_cache, char)
        db.flush()
        if kanji.id in existing_pre_n3:
            stats.skipped_existing += 1
            continue
        db.add(KanjiCoverage(kanji_id=kanji.id, coverage_source="pre_n3", batch_number=None))
        existing_pre_n3.add(kanji.id)
        stats.inserted += 1

    db.commit()
    return stats
