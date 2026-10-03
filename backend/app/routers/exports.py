import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import export_service

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/{batch_n}/vocab-tsv")
def get_vocab_tsv(
    batch_n: int,
    split_by_pos: bool = Query(default=False),
    cumulative: bool = Query(default=False),
    from_batch: int = Query(default=1),
    db: Session = Depends(get_db),
) -> dict:
    try:
        if cumulative:
            files = export_service.export_vocab_cumulative(
                db, batch_n, split_by_pos=split_by_pos, from_batch_n=from_batch
            )
        else:
            files = export_service.export_vocab(db, batch_n, split_by_pos=split_by_pos)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return files


@router.get("/{batch_n}/vocab-txt")
def get_vocab_txt(
    batch_n: int,
    cumulative: bool = Query(default=False),
    from_batch: int = Query(default=1),
    db: Session = Depends(get_db),
) -> dict:
    try:
        if cumulative:
            files = export_service.export_vocab_japanese_txt_cumulative(db, batch_n, from_batch_n=from_batch)
        else:
            files = export_service.export_vocab_japanese_txt(db, batch_n)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return files


@router.get("/{batch_n}/kanji-tsv")
def get_kanji_tsv(
    batch_n: int,
    cumulative: bool = Query(default=False),
    from_batch: int = Query(default=1),
    db: Session = Depends(get_db),
) -> dict:
    try:
        if cumulative:
            files = export_service.export_kanji_readings_cumulative(db, batch_n, from_batch_n=from_batch)
        else:
            files = export_service.export_kanji_readings(db, batch_n)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return files


@router.get("/{batch_n}/preview")
def get_export_preview(
    batch_n: int, split_by_pos: bool = Query(default=True), db: Session = Depends(get_db)
) -> list[dict]:
    try:
        words = export_service.get_export_preview(db, batch_n, split_by_pos=split_by_pos)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [
        {
            "vocab_id": w.vocab_id,
            "kanji_form": w.kanji_form,
            "hiragana_form": w.hiragana_form,
            "meaning": w.meaning,
            "part_of_speech": w.part_of_speech,
            "usually_kana": w.usually_kana,
            "needs_kanji_reading": w.needs_kanji_reading,
            "covers_target_kanji": w.covers_target_kanji,
            "vocab_card": w.vocab_card.__dict__,
            "kanji_reading_card": w.kanji_reading_card.__dict__ if w.kanji_reading_card else None,
        }
        for w in words
    ]


@router.get("/{batch_n}/pdf")
def get_pdf(
    batch_n: int,
    cumulative: bool = Query(default=False),
    from_batch: int = Query(default=1),
    db: Session = Depends(get_db),
) -> FileResponse:
    if not cumulative:
        filename = f"batch_{batch_n}_kanji.pdf"
    elif from_batch == 1:
        filename = f"kanji_cumulative_through_week_{batch_n}.pdf"
    else:
        filename = f"kanji_weeks_{from_batch}-{batch_n}.pdf"
    try:
        output_path = Path(tempfile.gettempdir()) / f"dispatcher_{filename}"
        export_service.export_pdf(db, batch_n, output_path, cumulative=cumulative, from_batch_n=from_batch)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(output_path, media_type="application/pdf", filename=filename)


@router.get("/{batch_n}/pdf/warnings")
def get_pdf_warnings(
    batch_n: int,
    cumulative: bool = Query(default=False),
    from_batch: int = Query(default=1),
    db: Session = Depends(get_db),
) -> list[dict]:
    try:
        if cumulative:
            _, warnings = export_service.build_kanji_pdf_pages_cumulative(db, batch_n, from_batch_n=from_batch)
        else:
            _, warnings = export_service.build_kanji_pdf_pages(db, batch_n)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [w.__dict__ for w in warnings]
