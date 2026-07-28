import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
from app.enrichment import jobs
from app.services import import_service

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    with tmp:
        shutil.copyfileobj(upload.file, tmp)
    return Path(tmp.name)


@router.post("/vocab-list")
def import_vocab_list(file: UploadFile, db: Session = Depends(get_db)) -> dict:
    path = _save_upload(file)
    try:
        return import_service.import_vocab_list(db, path, source=file.filename or "vocab_list")
    finally:
        path.unlink(missing_ok=True)


@router.post("/kanji-schedule")
def import_kanji_schedule(file: UploadFile, db: Session = Depends(get_db)) -> dict:
    path = _save_upload(file)
    try:
        return import_service.import_kanji_schedule(db, path)
    finally:
        path.unlink(missing_ok=True)


@router.post("/anki-export")
def import_anki_export(file: UploadFile, db: Session = Depends(get_db)) -> dict:
    path = _save_upload(file)
    try:
        return import_service.import_anki_export(db, path)
    finally:
        path.unlink(missing_ok=True)


@router.post("/enrich/vocab-words")
def start_vocab_word_enrichment(background_tasks: BackgroundTasks) -> dict:
    job_id = jobs.create_job("jisho_words", total=0)
    background_tasks.add_task(jobs.run_vocab_word_enrichment, job_id)
    return {"job_id": job_id}


@router.post("/enrich/kana-kanji-forms")
def start_kana_kanji_form_enrichment(background_tasks: BackgroundTasks) -> dict:
    job_id = jobs.create_job("jisho_kana_kanji", total=0)
    background_tasks.add_task(jobs.run_kana_kanji_form_enrichment, job_id)
    return {"job_id": job_id}


@router.post("/enrich/kanji-meanings")
def start_kanji_meaning_enrichment(background_tasks: BackgroundTasks) -> dict:
    job_id = jobs.create_job("jisho_kanji", total=0)
    background_tasks.add_task(jobs.run_kanji_meaning_enrichment, job_id)
    return {"job_id": job_id}


@router.post("/enrich/kanjivg")
def start_kanjivg_enrichment(background_tasks: BackgroundTasks) -> dict:
    job_id = jobs.create_job("kanjivg", total=0)
    background_tasks.add_task(jobs.run_kanjivg_enrichment, job_id)
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: int) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job
