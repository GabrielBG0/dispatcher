from datetime import date

from sqlalchemy import Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class StudyConfig(Base):
    """Single-row table (id is always 1) holding the study schedule parameters."""

    __tablename__ = "study_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=19)
    new_card_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    review_weeks: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_minimum: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
