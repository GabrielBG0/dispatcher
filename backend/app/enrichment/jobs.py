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

import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.enrichment.jisho_client import JishoClient, JishoWordSense
from app.enrichment.kana_kanji import (
    KanaKanjiOutcome,
    find_kanji_form,
    format_meaning_groups,
    is_meaning_already_standardized,
)
from app.enrichment.kanjivg_client import build_stroke_index, download_archive, stroke_paths_to_json
from app.kanji_utils import extract_kanji
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


def format_meaning(senses: list[JishoWordSense], limit: int = 2) -> str:
    """Top `limit` senses, each sense's definitions slash-joined. A single
    surviving sense is rendered plain; two or more get "1 - ", "2 - " ...
    prefixes so multi-meaning words read as a numbered list, e.g.:
    "1 - to put in / to insert. 2 - to admit / to accept". Delegates to
    kana_kanji.format_meaning_groups (the shared implementation also used to
    preview a candidate's meaning on the Vocab Review page) so there's one
    formatting rule, not two."""
    return format_meaning_groups([s.english_definitions for s in senses], limit=limit)


def pos_from_jisho(parts_of_speech: list[str]) -> str:
    lowered_tags = [t.lower() for t in parts_of_speech]
    for keyword, mapped in _POS_PRIORITY:
        # Word-boundary match, not plain substring -- "verb" is a substring
        # of "adverb", so a naive `in` check here misclassified every
        # adverb-only tag (e.g. "Adverb") as a verb because "verb" is
        # checked first in priority order.
        pattern = re.compile(rf"\b{re.escape(keyword)}\b")
        if any(pattern.search(tag) for tag in lowered_tags):
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
                    row.meaning = format_meaning(match.senses)
                    row.part_of_speech = pos_from_jisho(match.senses[0].parts_of_speech)
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


async def run_vocab_meaning_standardization(
    job_id: int, session_factory: SessionFactory = SessionLocal, client: JishoClient | None = None
) -> None:
    """Re-fetches Jisho meanings for vocab rows whose current `meaning` text
    doesn't already look like format_meaning's own output -- older imports
    (e.g. jlpt_n3_vocabulary.xls) wrote meanings as "(noun) foo, bar, baz"
    rather than the "1 - .../ 2 - ..." or "foo / bar / baz" shapes this job
    produces, so this overwrites those rows with the standardized form.
    Unlike run_vocab_word_enrichment (which only fills blanks), this
    OVERWRITES existing non-blank meaning text -- see
    kana_kanji.is_meaning_already_standardized for which rows are skipped
    as already in the target shape.
    """
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
        rows = [r for r in db.query(Vocab).all() if not is_meaning_already_standardized(r.meaning)]
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
                    row.meaning = format_meaning(match.senses)
                    row.part_of_speech = pos_from_jisho(match.senses[0].parts_of_speech)
                    row.jlpt_level = match.jlpt[0] if match.jlpt else row.jlpt_level
                    row.source = (
                        f"{row.source},jisho_standardized".strip(",") if row.source else "jisho_standardized"
                    )
                    try:
                        db.commit()
                    except IntegrityError:
                        # Natural-key collision (kanji_form, hiragana_form, meaning) --
                        # an unresolved near-duplicate row for the same word already
                        # standardized to this exact canonical text. Leave this row's
                        # old meaning in place rather than crash the whole job; flagged
                        # for manual dedupe review instead of silently dropped.
                        db.rollback()
                        row.source = (
                            f"{row.source},jisho_duplicate_conflict".strip(",")
                            if row.source
                            else "jisho_duplicate_conflict"
                        )
                        db.commit()
                        job.not_found += 1
                else:
                    row.source = (
                        f"{row.source},jisho_not_found".strip(",") if row.source else "jisho_not_found"
                    )
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


async def run_kana_kanji_form_enrichment(
    job_id: int, session_factory: SessionFactory = SessionLocal, client: JishoClient | None = None
) -> None:
    """Fills in the real kanji spelling for vocab rows whose `kanji_form` is
    currently kana-only (e.g. kanji_form="そう" for a word that's actually
    僧), using Jisho word search + the row's stored `meaning` to disambiguate
    homophones -- see enrichment/kana_kanji.py for the matching rules.
    Rows Jisho has no kanji-bearing entry for, or where the match is
    ambiguous/unconfident, are left untouched and counted as `not_found`.
    """
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
        rows = [r for r in db.query(Vocab).all() if not extract_kanji(r.kanji_form)]
        job.total = len(rows)
        db.commit()

        for row in rows:
            try:
                results = await client.search_words(row.hiragana_form)
                result = find_kanji_form(row.hiragana_form, row.meaning or "", results)
                if result.outcome is KanaKanjiOutcome.MATCHED:
                    row.kanji_form = result.kanji_form
                    row.usually_kana = result.usually_kana
                    row.source = f"{row.source},jisho".strip(",") if row.source else "jisho"
                    try:
                        db.commit()
                    except IntegrityError:
                        # Natural-key collision (kanji_form, hiragana_form, meaning) with
                        # another row -- extremely rare, but a bad write here shouldn't
                        # take the rest of the job down with it.
                        db.rollback()
                        job.not_found += 1
                else:
                    job.not_found += 1
            except Exception:  # noqa: BLE001 - one bad lookup shouldn't abort the whole job
                db.rollback()
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
            else:
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
        db.close()
