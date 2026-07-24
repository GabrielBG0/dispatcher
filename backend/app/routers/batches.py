from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import batch_service

router = APIRouter(prefix="/api/batches", tags=["batches"])


class BulkWordsPayload(BaseModel):
    vocab_ids: list[int]
    exclude: bool = False


def _replacement_dict(r: batch_service.ReplacementCandidate | None) -> dict | None:
    return r.__dict__ if r is not None else None


@router.post("/{batch_n}/generate")
def generate_draft(batch_n: int, db: Session = Depends(get_db)) -> dict:
    try:
        result = batch_service.generate_draft_batch(db, batch_n, today=date.today())
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "batch_number": result.batch_number,
        "weekly_target_used": result.weekly_target_used,
        "pacing_floor": result.pacing_floor,
        "behind_pace": result.weekly_target_used > result.pacing_floor,
        "selected_count": len(result.selected),
        "target_kanji_coverage": result.target_kanji_coverage,
        "warnings": [w.__dict__ for w in result.warnings],
    }


@router.get("/{batch_n}")
def get_batch(batch_n: int, db: Session = Depends(get_db)) -> dict:
    try:
        detail = batch_service.get_batch_detail(db, batch_n)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "batch_number": detail.batch_number,
        "status": detail.status,
        "weekly_target_used": detail.weekly_target_used,
        "target_kanji": detail.target_kanji,
        "target_kanji_coverage": detail.target_kanji_coverage,
        "words": [w.__dict__ for w in detail.words],
    }


@router.get("/{batch_n}/eligible-replacements")
def eligible_replacements(batch_n: int, db: Session = Depends(get_db)) -> list[dict]:
    return [r.__dict__ for r in batch_service.get_eligible_replacements(db, batch_n)]


@router.delete("/{batch_n}/words/{vocab_id}")
def remove_word(batch_n: int, vocab_id: int, exclude: bool = False, db: Session = Depends(get_db)) -> dict:
    try:
        batch_service.remove_word(db, batch_n, vocab_id, exclude=exclude)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{batch_n}/words/bulk-remove")
def bulk_remove_words(batch_n: int, payload: BulkWordsPayload, db: Session = Depends(get_db)) -> dict:
    try:
        removed = batch_service.remove_words(db, batch_n, payload.vocab_ids, exclude=payload.exclude)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"removed_vocab_ids": removed}


@router.post("/{batch_n}/words/{vocab_id}/replace")
def replace_word(batch_n: int, vocab_id: int, exclude: bool = False, db: Session = Depends(get_db)) -> dict:
    try:
        replacement = batch_service.replace_word(db, batch_n, vocab_id, exclude=exclude)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"removed_vocab_id": vocab_id, "added": _replacement_dict(replacement)}


@router.post("/{batch_n}/words/bulk-replace")
def bulk_replace_words(batch_n: int, payload: BulkWordsPayload, db: Session = Depends(get_db)) -> dict:
    try:
        results = batch_service.replace_words(db, batch_n, payload.vocab_ids, exclude=payload.exclude)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "results": [
            {"removed_vocab_id": removed_id, "added": _replacement_dict(added)} for removed_id, added in results
        ]
    }


@router.post("/{batch_n}/words/{vocab_id}")
def add_word(batch_n: int, vocab_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        batch_service.add_word(db, batch_n, vocab_id)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{batch_n}/words/{vocab_id}/toggle-reading")
def toggle_reading(batch_n: int, vocab_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        needs_reading = batch_service.toggle_reading(db, batch_n, vocab_id)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"needs_kanji_reading": needs_reading}


@router.post("/{batch_n}/words/{vocab_id}/swap")
def swap_word(batch_n: int, vocab_id: int, replacement_vocab_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        batch_service.swap_word(db, batch_n, vocab_id, replacement_vocab_id)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/{batch_n}/finalize")
def finalize(batch_n: int, db: Session = Depends(get_db)) -> dict:
    try:
        batch_service.finalize_batch(db, batch_n)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"batch_number": batch_n, "status": "finalized"}


@router.post("/{batch_n}/unfinalize")
def unfinalize(batch_n: int, db: Session = Depends(get_db)) -> dict:
    try:
        batch_service.unfinalize_batch(db, batch_n)
    except batch_service.BatchServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"batch_number": batch_n, "status": "draft"}
