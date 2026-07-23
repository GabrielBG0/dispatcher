import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import export_service

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/{batch_n}/vocab-tsv")
def get_vocab_tsv(batch_n: int, split_by_pos: bool = Query(default=False), db: Session = Depends(get_db)) -> dict:
    try:
        files = export_service.export_vocab(db, batch_n, split_by_pos=split_by_pos)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return files


@router.get("/{batch_n}/kanji-tsv")
def get_kanji_tsv(batch_n: int, db: Session = Depends(get_db)) -> dict:
    try:
        content = export_service.export_kanji_readings(db, batch_n)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"kanji_readings.tsv": content}


@router.get("/{batch_n}/pdf")
def get_pdf(batch_n: int, db: Session = Depends(get_db)) -> FileResponse:
    try:
        output_path = Path(tempfile.gettempdir()) / f"dispatcher_batch_{batch_n}.pdf"
        export_service.export_pdf(db, batch_n, output_path)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FileResponse(output_path, media_type="application/pdf", filename=f"batch_{batch_n}_kanji.pdf")


@router.get("/{batch_n}/pdf/warnings")
def get_pdf_warnings(batch_n: int, db: Session = Depends(get_db)) -> list[dict]:
    try:
        _, warnings = export_service.build_kanji_pdf_pages(db, batch_n)
    except export_service.ExportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [w.__dict__ for w in warnings]
