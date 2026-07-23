"""Pure aggregation over already-built data -- no new selection/business
logic lives here, just read queries for the Overview screen.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.batch import Batch
from app.models.study_config import StudyConfig
from app.models.vocab import Vocab


@dataclass
class BatchSummary:
    batch_number: int
    status: str
    weekly_target_used: int
    word_count: int


@dataclass
class DashboardOverview:
    words_total: int
    words_seen_in_class: int
    words_available: int
    words_assigned: int
    study_end_date: date | None
    weeks_remaining: int | None
    behind_pace: bool
    batches: list[BatchSummary]


def get_overview(db: Session, today: date) -> DashboardOverview:
    words_total = db.query(Vocab).count()
    words_seen_in_class = db.query(Vocab).filter(Vocab.status == "seen_in_class").count()
    words_available = db.query(Vocab).filter(Vocab.status == "available").count()
    words_assigned = db.query(Vocab).filter(Vocab.status == "assigned").count()

    study_config = db.query(StudyConfig).one_or_none()
    study_end_date = None
    weeks_remaining = None
    if study_config is not None:
        study_end_date = study_config.start_date + timedelta(weeks=study_config.new_card_weeks)
        weeks_remaining = max(0, (study_end_date - today).days // 7)

    batch_rows = db.query(Batch).order_by(Batch.batch_number).all()
    batches = [
        BatchSummary(
            batch_number=b.batch_number,
            status=b.status,
            weekly_target_used=b.weekly_target_used,
            word_count=db.query(Vocab).filter(Vocab.assigned_batch == b.batch_number).count(),
        )
        for b in batch_rows
    ]

    pacing_floor = study_config.daily_minimum * 7 if study_config else 0
    behind_pace = any(b.weekly_target_used > pacing_floor for b in batches)

    return DashboardOverview(
        words_total=words_total,
        words_seen_in_class=words_seen_in_class,
        words_available=words_available,
        words_assigned=words_assigned,
        study_end_date=study_end_date,
        weeks_remaining=weeks_remaining,
        behind_pace=behind_pace,
        batches=batches,
    )
