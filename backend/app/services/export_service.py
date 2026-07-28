"""Orchestration for finalized-batch exports: pulls DB rows for a batch and
hands them to the pure export/ formatters. No formatting logic lives here.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.enrichment.kanjivg_client import stroke_paths_from_json
from app.export.card_formatter import VocabCardFields, format_kanji_reading_card, format_vocab_card
from app.export.kanji_tsv import export_kanji_reading_tsv
from app.export.pdf_renderer import KanjiPageData, KanjiPageWord, render_pdf
from app.export.vocab_tsv import (
    POS_FILE_NAMES,
    VocabExportRow,
    export_vocab_tsv_combined,
    export_vocab_tsv_split_by_pos,
    tags_for_row,
)
from app.kanji_utils import extract_kanji
from app.models.batch import Batch
from app.models.kanji import Kanji
from app.models.kanji_coverage import KanjiCoverage
from app.models.vocab import Vocab
from app.services.batch_service import is_seen_in_class_fallback


class ExportServiceError(Exception):
    pass


def _with_batch_suffix(filename: str, batch_n: int) -> str:
    stem, _, ext = filename.rpartition(".")
    return f"{stem} - Batch {batch_n}.{ext}"


# Caps the per-kanji word list so it can't push the stroke diagram onto a
# second PDF page; kanji with more vocab than this just show the first 8.
MAX_WORDS_PER_PAGE = 8


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
            usually_kana=v.usually_kana,
            used_seen_in_class_fallback=is_seen_in_class_fallback(v),
        )
        for v in _batch_vocab_rows(db, batch_n)
    ]
    if split_by_pos:
        files = export_vocab_tsv_split_by_pos(rows)
    else:
        files = {"Japanese Complete Vocab.tsv": export_vocab_tsv_combined(rows)}
    return {_with_batch_suffix(name, batch_n): content for name, content in files.items()}


def export_kanji_readings(db: Session, batch_n: int) -> dict[str, str]:
    _require_finalized_batch(db, batch_n)
    rows = [
        VocabExportRow(
            kanji_form=v.kanji_form,
            hiragana_form=v.hiragana_form,
            meaning=v.meaning,
            part_of_speech=v.part_of_speech,
            used_seen_in_class_fallback=is_seen_in_class_fallback(v),
        )
        for v in _batch_vocab_rows(db, batch_n)
        if v.needs_kanji_reading
    ]
    content = export_kanji_reading_tsv(rows)
    return {_with_batch_suffix("Japanese Kanji.tsv", batch_n): content}


@dataclass(frozen=True)
class ExportPreviewCard:
    front: str
    back: str
    deck: str
    tags: str


@dataclass(frozen=True)
class ExportPreviewWord:
    vocab_id: int
    kanji_form: str
    hiragana_form: str
    meaning: str
    part_of_speech: str
    usually_kana: bool
    needs_kanji_reading: bool
    covers_target_kanji: list[str]
    vocab_card: ExportPreviewCard
    kanji_reading_card: ExportPreviewCard | None


def _vocab_deck_name(part_of_speech: str, split_by_pos: bool) -> str:
    if not split_by_pos:
        return "Japanese Complete Vocab"
    pos = part_of_speech if part_of_speech in POS_FILE_NAMES else "general"
    return POS_FILE_NAMES[pos].removesuffix(".tsv")


def get_export_preview(db: Session, batch_n: int, split_by_pos: bool = True) -> list[ExportPreviewWord]:
    """Renders exactly what each word's card(s) will look like in the Anki
    export -- front, back, and the deck/file it lands in -- using the same
    formatters the real export uses, so this can't drift from what the user
    actually downloads. Built for reviewing a finalized batch, where
    mistakes (wrong part-of-speech -> wrong deck, bad kana/kanji data ->
    malformed front) are easiest to spot with the real rendering in front of
    you rather than the raw fields.
    """
    _require_finalized_batch(db, batch_n)

    # Mirrors build_kanji_pdf_pages: reads the frozen coverage snapshot a
    # finalized batch locked in, not the (possibly since-shifted) live
    # schedule.
    target_kanji_chars = {
        k.kanji
        for k in db.query(Kanji)
        .join(KanjiCoverage, KanjiCoverage.kanji_id == Kanji.id)
        .filter(KanjiCoverage.coverage_source == "n3_batch", KanjiCoverage.batch_number == batch_n)
        .all()
    }

    words: list[ExportPreviewWord] = []
    for v in _batch_vocab_rows(db, batch_n):
        fields = VocabCardFields(
            kanji_form=v.kanji_form, hiragana_form=v.hiragana_form, meaning=v.meaning, usually_kana=v.usually_kana
        )
        tags = tags_for_row(
            VocabExportRow(
                kanji_form=v.kanji_form,
                hiragana_form=v.hiragana_form,
                meaning=v.meaning,
                part_of_speech=v.part_of_speech,
                usually_kana=v.usually_kana,
                used_seen_in_class_fallback=is_seen_in_class_fallback(v),
            )
        )

        vocab_card_fields = format_vocab_card(fields)
        vocab_card = ExportPreviewCard(
            front=vocab_card_fields.front,
            back=vocab_card_fields.back,
            deck=_vocab_deck_name(v.part_of_speech, split_by_pos),
            tags=tags,
        )

        kanji_reading_card = None
        if v.needs_kanji_reading:
            reading_card_fields = format_kanji_reading_card(fields)
            kanji_reading_card = ExportPreviewCard(
                front=reading_card_fields.front, back=reading_card_fields.back, deck="Japanese Kanji", tags=tags
            )

        words.append(
            ExportPreviewWord(
                vocab_id=v.id,
                kanji_form=v.kanji_form,
                hiragana_form=v.hiragana_form,
                meaning=v.meaning,
                part_of_speech=v.part_of_speech,
                usually_kana=v.usually_kana,
                needs_kanji_reading=v.needs_kanji_reading,
                covers_target_kanji=sorted(extract_kanji(v.kanji_form) & target_kanji_chars),
                vocab_card=vocab_card,
                kanji_reading_card=kanji_reading_card,
            )
        )
    return words


@dataclass
class PdfWarning:
    kanji: str
    detail: str


def build_kanji_pdf_pages(db: Session, batch_n: int) -> tuple[list[KanjiPageData], list[PdfWarning]]:
    _require_finalized_batch(db, batch_n)

    # The schedule itself is a dynamically repacked view (see
    # batch_service._load_schedule) that can shift as coverage grows, so a
    # finalized batch's kanji list is read from the frozen record
    # finalize_batch wrote at the time it locked the batch in, not
    # recomputed live.
    target_kanji_rows = (
        db.query(Kanji)
        .join(KanjiCoverage, KanjiCoverage.kanji_id == Kanji.id)
        .filter(KanjiCoverage.coverage_source == "n3_batch", KanjiCoverage.batch_number == batch_n)
        .all()
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
        ][:MAX_WORDS_PER_PAGE]
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
