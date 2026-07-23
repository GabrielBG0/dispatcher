from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import batch_service

router = APIRouter(prefix="/api/batches", tags=["batches"])


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


@router.get("/{batch_n}/eligible-replacements")
def eligible_replacements(batch_n: int, db: Session = Depends(get_db)) -> list[dict]:
    return [r.__dict__ for r in batch_service.get_eligible_replacements(db, batch_n)]


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
