from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.enrichment.jisho_client import JishoClient
from app.services import dedupe_service, vocab_service

router = APIRouter(prefix="/api/vocab", tags=["vocab"])


@router.get("")
def list_vocab(
    kana_only: bool = False,
    include_reviewed: bool = False,
    search: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    result = vocab_service.list_vocab(
        db, kana_only=kana_only, include_reviewed=include_reviewed, search=search, limit=limit, offset=offset
    )
    return {
        "total": result.total,
        "items": [
            {
                "id": v.id,
                "kanji_form": v.kanji_form,
                "hiragana_form": v.hiragana_form,
                "meaning": v.meaning,
                "part_of_speech": v.part_of_speech,
                "status": v.status,
                "assigned_batch": v.assigned_batch,
                "usually_kana": v.usually_kana,
                "source": v.source,
            }
            for v in result.items
        ],
    }


@router.get("/{vocab_id}/kanji-candidates")
async def get_kanji_candidates(
    vocab_id: int, reading: str | None = None, db: Session = Depends(get_db)
) -> dict:
    client = JishoClient()
    try:
        candidates = await vocab_service.get_kanji_candidates(db, vocab_id, client, reading=reading)
    except vocab_service.VocabServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        await client.aclose()
    return {
        "candidates": [
            {
                "word": c.word,
                "definitions": c.definitions,
                "meaning": c.meaning,
                "score": c.score,
                "usually_kana": c.usually_kana,
            }
            for c in candidates
        ]
    }


class UpdateVocabRequest(BaseModel):
    kanji_form: str | None = None
    hiragana_form: str | None = None
    meaning: str | None = None
    usually_kana: bool | None = None
    part_of_speech: str | None = None


@router.patch("/{vocab_id}")
def update_vocab(vocab_id: int, payload: UpdateVocabRequest, db: Session = Depends(get_db)) -> dict:
    try:
        row = vocab_service.update_vocab(
            db,
            vocab_id,
            kanji_form=payload.kanji_form,
            hiragana_form=payload.hiragana_form,
            meaning=payload.meaning,
            usually_kana=payload.usually_kana,
            part_of_speech=payload.part_of_speech,
        )
    except vocab_service.VocabServiceError as exc:
        status = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return {
        "id": row.id,
        "kanji_form": row.kanji_form,
        "hiragana_form": row.hiragana_form,
        "meaning": row.meaning,
        "part_of_speech": row.part_of_speech,
        "usually_kana": row.usually_kana,
    }


@router.delete("/{vocab_id}")
def delete_vocab(vocab_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        vocab_service.delete_vocab(db, vocab_id)
    except vocab_service.VocabServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": vocab_id}


@router.post("/{vocab_id}/confirm-kana-only")
def confirm_kana_only(vocab_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        row = vocab_service.mark_kana_only_confirmed(db, vocab_id)
    except vocab_service.VocabServiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"id": row.id, "source": row.source}


@router.get("/duplicates")
def get_duplicate_groups(db: Session = Depends(get_db)) -> list[dict]:
    groups = dedupe_service.find_duplicate_groups(db)
    return [
        {
            "kanji_form": g.kanji_form,
            "hiragana_form": g.hiragana_form,
            "similarity": g.similarity,
            "suggested_keep_id": g.suggested_keep_id,
            "auto_resolvable": g.auto_resolvable,
            "reason": g.reason,
            "rows": [
                {
                    "id": r.id,
                    "meaning": r.meaning,
                    "status": r.status,
                    "assigned_batch": r.assigned_batch,
                    "source": r.source,
                }
                for r in g.rows
            ],
        }
        for g in groups
    ]


class ResolveDuplicatesRequest(BaseModel):
    keep_id: int
    delete_ids: list[int]


@router.post("/duplicates/resolve")
def resolve_duplicate_group(payload: ResolveDuplicatesRequest, db: Session = Depends(get_db)) -> dict:
    try:
        dedupe_service.resolve_duplicate_group(db, keep_id=payload.keep_id, delete_ids=payload.delete_ids)
    except dedupe_service.DedupeServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"kept": payload.keep_id, "deleted": payload.delete_ids}
