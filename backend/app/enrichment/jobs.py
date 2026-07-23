"""Background enrichment job runner. Each job type is a plain async/sync
function that updates a DB-backed EnrichmentJob row as it progresses, so the
Import screen can poll `/api/imports/jobs/{id}` instead of holding an open
connection. Jobs open their own DB session (via `session_factory`, defaulting
to the app's real SessionLocal) since they run outside a request's session
lifecycle (FastAPI BackgroundTasks) -- the factory is a parameter so tests
can point jobs at an isolated DB instead of monkeypatching module state.

Note: vocab/kanji rows are selected by "still blank" (meaning == "" /
meanings is None), so re-running a job only ever fills gaps -- it will not
repair a row that was already (mis-)populated by a prior enrichment bug.
Backfilling those requires a one-off script that clears the bad field first.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.enrichment.jisho_client import JishoClient
from app.enrichment.kanjivg_client import build_stroke_index, download_archive, stroke_paths_to_json
from app.models.enrichment_job import EnrichmentJob
from app.models.kanji import Kanji
from app.models.vocab import Vocab

SessionFactory = Callable[[], Session]

# Priority order matters: a Jisho sense can carry multiple POS tags at once
# (e.g. ["Noun", "Suru verb"] for 旅行), so this checks by category priority
# across *all* tags rather than tag-by-tag, verb taking precedence over noun.
_POS_PRIORITY = [
    ("verb", "verb"),
    ("adjective", "adjective"),
    ("adverb", "adverb"),
]


def create_job(job_type: str, total: int, session_factory: SessionFactory = SessionLocal) -> int:
    db = session_factory()
    try:
        job = EnrichmentJob(job_type=job_type, status="pending", total=total, completed=0, not_found=0)
        db.add(job)
        db.commit()
        db.refresh(job)
        return job.id
    finally:
        db.close()


def get_job(job_id: int, session_factory: SessionFactory = SessionLocal) -> dict | None:
    db = session_factory()
    try:
        job = db.get(EnrichmentJob, job_id)
        if job is None:
            return None
        return {
            "id": job.id,
            "job_type": job.job_type,
            "status": job.status,
            "total": job.total,
            "completed": job.completed,
            "not_found": job.not_found,
            "error": job.error,
        }
    finally:
        db.close()


def _pos_from_jisho(parts_of_speech: list[str]) -> str:
    lowered_tags = [t.lower() for t in parts_of_speech]
    for keyword, mapped in _POS_PRIORITY:
        if any(keyword in tag for tag in lowered_tags):
            return mapped
    return "general"


async def run_vocab_word_enrichment(
    job_id: int, session_factory: SessionFactory = SessionLocal, client: JishoClient | None = None
) -> None:
    db = session_factory()
    job = db.get(EnrichmentJob, job_id)
    if job is None:
        db.close()
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()

    owns_client = client is None
    client = client or JishoClient()
    try:
        rows = db.query(Vocab).filter(Vocab.meaning == "").all()
        job.total = len(rows)
        db.commit()

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
                if match and match.senses:
                    row.meaning = "; ".join(
                        ", ".join(s.english_definitions) for s in match.senses if s.english_definitions
                    )
                    row.part_of_speech = _pos_from_jisho(match.senses[0].parts_of_speech)
                    row.jlpt_level = match.jlpt[0] if match.jlpt else row.jlpt_level
                    row.source = f"{row.source},jisho".strip(",") if row.source else "jisho"
                else:
                    row.source = f"{row.source},jisho_not_found".strip(",") if row.source else "jisho_not_found"
                    job.not_found += 1
            except Exception:  # noqa: BLE001 - one bad lookup shouldn't abort the whole job
                row.source = f"{row.source},jisho_error".strip(",") if row.source else "jisho_error"
                job.not_found += 1

            job.completed += 1
            db.commit()

        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    finally:
        if owns_client:
            await client.aclose()
        db.close()


async def run_kanji_meaning_enrichment(
    job_id: int, session_factory: SessionFactory = SessionLocal, client: JishoClient | None = None
) -> None:
    db = session_factory()
    job = db.get(EnrichmentJob, job_id)
    if job is None:
        db.close()
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()

    owns_client = client is None
    client = client or JishoClient()
    try:
        rows = db.query(Kanji).filter(Kanji.meanings.is_(None)).all()
        job.total = len(rows)
        db.commit()

        for row in rows:
            try:
                result = await client.fetch_kanji(row.kanji)
                if result:
                    row.meanings = ", ".join(result.meanings) if result.meanings else None
                    row.kun_yomi = "・".join(result.kun_yomi) if result.kun_yomi else None
                    row.on_yomi = "・".join(result.on_yomi) if result.on_yomi else None
                else:
                    job.not_found += 1
            except Exception:  # noqa: BLE001
                job.not_found += 1

            job.completed += 1
            db.commit()

        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    finally:
        if owns_client:
            await client.aclose()
        db.close()


def run_kanjivg_enrichment(
    job_id: int,
    session_factory: SessionFactory = SessionLocal,
    archive_path: Path | None = None,
) -> None:
    db = session_factory()
    job = db.get(EnrichmentJob, job_id)
    if job is None:
        db.close()
        return

    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    db.commit()

    try:
        archive_path = archive_path or (settings.kanjivg_cache_dir / "kanjivg-latest.xml.gz")
        download_archive(archive_path)
        index = build_stroke_index(archive_path)

        rows = db.query(Kanji).filter(Kanji.stroke_data.is_(None)).all()
        job.total = len(rows)
        db.commit()

        for row in rows:
            paths = index.get(row.kanji)
            if paths:
                row.stroke_data = stroke_paths_to_json(paths)
            job.completed += 1
            db.commit()

        job.status = "completed"
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    finally:
        db.close()
