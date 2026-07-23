from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.study_config import StudyConfig

router = APIRouter(prefix="/api/config", tags=["config"])


class StudyConfigPayload(BaseModel):
    start_date: date
    total_weeks: int = 19
    new_card_weeks: int = 16
    review_weeks: int = 3
    daily_minimum: int = 18


@router.get("")
def get_config(db: Session = Depends(get_db)) -> StudyConfigPayload | None:
    config = db.query(StudyConfig).one_or_none()
    if config is None:
        return None
    return StudyConfigPayload(
        start_date=config.start_date,
        total_weeks=config.total_weeks,
        new_card_weeks=config.new_card_weeks,
        review_weeks=config.review_weeks,
        daily_minimum=config.daily_minimum,
    )


@router.put("")
def put_config(payload: StudyConfigPayload, db: Session = Depends(get_db)) -> StudyConfigPayload:
    config = db.query(StudyConfig).one_or_none()
    if config is None:
        config = StudyConfig(id=1)
        db.add(config)

    config.start_date = payload.start_date
    config.total_weeks = payload.total_weeks
    config.new_card_weeks = payload.new_card_weeks
    config.review_weeks = payload.review_weeks
    config.daily_minimum = payload.daily_minimum
    db.commit()
    return payload
