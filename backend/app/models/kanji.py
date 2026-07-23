from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Kanji(Base):
    __tablename__ = "kanji"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kanji: Mapped[str] = mapped_column(String(1), nullable=False, unique=True, index=True)
    meanings: Mapped[str | None] = mapped_column(String, nullable=True)
    on_yomi: Mapped[str | None] = mapped_column(String, nullable=True)
    kun_yomi: Mapped[str | None] = mapped_column(String, nullable=True)
    jlpt_level: Mapped[str | None] = mapped_column(String, nullable=True)
    stroke_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
