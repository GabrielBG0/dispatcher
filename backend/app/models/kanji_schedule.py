from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class KanjiSchedule(Base):
    __tablename__ = "kanji_schedule"

    kanji_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("kanji.id"), primary_key=True
    )
    batch_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    kanji: Mapped["Kanji"] = relationship()
