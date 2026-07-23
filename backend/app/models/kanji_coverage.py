from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class KanjiCoverage(Base):
    __tablename__ = "kanji_coverage"
    __table_args__ = (
        UniqueConstraint("kanji_id", "coverage_source", "batch_number", name="uq_kanji_coverage_entry"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kanji_id: Mapped[int] = mapped_column(Integer, ForeignKey("kanji.id"), nullable=False, index=True)
    coverage_source: Mapped[str] = mapped_column(String, nullable=False)  # pre_n3 | n3_batch
    batch_number: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("batches.batch_number"), nullable=True
    )
    covered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    kanji: Mapped["Kanji"] = relationship()
