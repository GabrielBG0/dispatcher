from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.anki_export_parser import parse_anki_export
from app.ingestion.kanji_schedule_parser import parse_kanji_schedule
from app.ingestion.upsert import apply_seen_in_class, upsert_kanji_schedule_rows, upsert_vocab_rows
from app.ingestion.vocab_list_parser import parse_vocab_list


def import_vocab_list(db: Session, path: Path, source: str) -> dict:
    parse_result = parse_vocab_list(path)
    stats = upsert_vocab_rows(db, parse_result.rows, source=source)
    return {
        "parsed_rows": len(parse_result.rows),
        "warnings": [w.__dict__ for w in parse_result.warnings],
        "inserted": stats.inserted,
        "skipped_existing": stats.skipped_existing,
        "updated": stats.updated,
    }


def import_kanji_schedule(db: Session, path: Path) -> dict:
    parse_result = parse_kanji_schedule(path)
    stats = upsert_kanji_schedule_rows(db, parse_result.rows)
    return {
        "parsed_rows": len(parse_result.rows),
        "warnings": [w.__dict__ for w in parse_result.warnings],
        "inserted": stats.inserted,
        "skipped_existing": stats.skipped_existing,
        "updated": stats.updated,
    }


def import_anki_export(db: Session, path: Path) -> dict:
    parse_result = parse_anki_export(path)
    stats = apply_seen_in_class(db, parse_result.rows)
    return {
        "parsed_rows": len(parse_result.rows),
        "warnings": [w.__dict__ for w in parse_result.warnings],
        "vocab_marked_seen_in_class": stats.updated,
        "kanji_coverage_inserted": stats.inserted,
        "kanji_coverage_already_present": stats.skipped_existing,
    }
