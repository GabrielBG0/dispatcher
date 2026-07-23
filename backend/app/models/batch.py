from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Batch(Base):
    __tablename__ = "batches"

    batch_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")  # draft | finalized | exported
    weekly_target_used: Mapped[int] = mapped_column(Integer, nullable=False)
