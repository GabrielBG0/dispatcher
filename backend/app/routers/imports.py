import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy.orm import Session

from app.db import get_db
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
