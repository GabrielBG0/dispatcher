from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview")
def get_overview(db: Session = Depends(get_db)) -> dict:
    overview = dashboard_service.get_overview(db, today=date.today())
    return asdict(overview)
