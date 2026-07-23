from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Vocab(Base):
    __tablename__ = "vocab"
    __table_args__ = (
        UniqueConstraint("kanji_form", "hiragana_form", "meaning", name="uq_vocab_natural_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kanji_form: Mapped[str] = mapped_column(String, nullable=False, index=True)
    hiragana_form: Mapped[str] = mapped_column(String, nullable=False)
    meaning: Mapped[str] = mapped_column(String, nullable=False, default="")
    part_of_speech: Mapped[str] = mapped_column(String, nullable=False, default="general")
    jlpt_level: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="available", index=True)
    assigned_batch: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("batches.batch_number"), nullable=True, index=True
    )
    needs_kanji_reading: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="")
