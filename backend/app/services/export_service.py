"""Orchestration for finalized-batch exports: pulls DB rows for a batch and
hands them to the pure export/ formatters. No formatting logic lives here.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.enrichment.kanjivg_client import stroke_paths_from_json
from app.export.kanji_tsv import export_kanji_reading_tsv
from app.export.pdf_renderer import KanjiPageData, KanjiPageWord, render_pdf
from app.export.vocab_tsv import VocabExportRow, export_vocab_tsv_combined, export_vocab_tsv_split_by_pos
from app.kanji_utils import extract_kanji
from app.models.batch import Batch
from app.models.kanji import Kanji
from app.models.kanji_schedule import KanjiSchedule
from app.models.vocab import Vocab


class ExportServiceError(Exception):
    pass


def _require_finalized_batch(db: Session, batch_n: int) -> Batch:
    batch = db.get(Batch, batch_n)
    if batch is None:
        raise ExportServiceError(f"batch {batch_n} does not exist")
    if batch.status not in ("finalized", "exported"):
        raise ExportServiceError(f"batch {batch_n} is not finalized (status={batch.status})")
    return batch


def _batch_vocab_rows(db: Session, batch_n: int) -> list[Vocab]:
    return db.query(Vocab).filter(Vocab.assigned_batch == batch_n).all()


def export_vocab(db: Session, batch_n: int, split_by_pos: bool) -> dict[str, str]:
    _require_finalized_batch(db, batch_n)
    rows = [
        VocabExportRow(
            kanji_form=v.kanji_form,
            hiragana_form=v.hiragana_form,
            meaning=v.meaning,
            part_of_speech=v.part_of_speech,
        )
        for v in _batch_vocab_rows(db, batch_n)
    ]
    if split_by_pos:
        return export_vocab_tsv_split_by_pos(rows)
    return {"vocab.tsv": export_vocab_tsv_combined(rows)}


def export_kanji_readings(db: Session, batch_n: int) -> str:
    _require_finalized_batch(db, batch_n)
    rows = [
        VocabExportRow(
            kanji_form=v.kanji_form,
            hiragana_form=v.hiragana_form,
            meaning=v.meaning,
            part_of_speech=v.part_of_speech,
        )
        for v in _batch_vocab_rows(db, batch_n)
        if v.needs_kanji_reading
    ]
    return export_kanji_reading_tsv(rows)


@dataclass
class PdfWarning:
    kanji: str
    detail: str


def build_kanji_pdf_pages(db: Session, batch_n: int) -> tuple[list[KanjiPageData], list[PdfWarning]]:
    _require_finalized_batch(db, batch_n)

    target_kanji_rows = (
        db.query(Kanji).join(KanjiSchedule).filter(KanjiSchedule.batch_number == batch_n).all()
    )
    vocab_rows = _batch_vocab_rows(db, batch_n)
    vocab_with_chars = [(v, frozenset(extract_kanji(v.kanji_form))) for v in vocab_rows]

    pages: list[KanjiPageData] = []
    warnings: list[PdfWarning] = []

    for kanji in sorted(target_kanji_rows, key=lambda k: k.kanji):
        words = [
            KanjiPageWord(kanji_form=v.kanji_form, hiragana_form=v.hiragana_form, meaning=v.meaning)
            for v, chars in vocab_with_chars
            if kanji.kanji in chars
        ]
        if kanji.stroke_data is None:
            warnings.append(PdfWarning(kanji=kanji.kanji, detail="no KanjiVG stroke data cached"))
        if not kanji.meanings and not kanji.kun_yomi and not kanji.on_yomi:
            warnings.append(PdfWarning(kanji=kanji.kanji, detail="no Jisho enrichment data"))

        stroke_paths = stroke_paths_from_json(kanji.stroke_data) if kanji.stroke_data else []

        pages.append(
            KanjiPageData(
                kanji=kanji.kanji,
                meanings=kanji.meanings or "",
                kun_yomi=kanji.kun_yomi or "",
                on_yomi=kanji.on_yomi or "",
                stroke_paths=stroke_paths,
                words=words,
            )
        )

    return pages, warnings


def export_pdf(db: Session, batch_n: int, output_path) -> list[PdfWarning]:
    pages, warnings = build_kanji_pdf_pages(db, batch_n)
    render_pdf(pages, output_path)
    return warnings
