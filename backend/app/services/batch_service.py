"""Orchestration layer between the batches router and the pure selection
core. This is the only layer allowed to mix a DB session with calls into
app.selection -- select_batch itself never touches SQLAlchemy.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.bccwj_frequency_loader import load_bccwj_frequency_subset
from app.kanji_utils import extract_kanji
from app.models.batch import Batch
from app.models.kanji import Kanji
from app.models.kanji_coverage import KanjiCoverage
from app.models.kanji_schedule import KanjiSchedule
from app.models.study_config import StudyConfig
from app.models.vocab import Vocab
from app.selection.classify import classify_word_kanji, orphan_kanji_count
from app.selection.select_batch import select_batch
from app.selection.types import SelectionConfig, SelectionResult, VocabCandidate

BCCWJ_SUBSET_PATH = settings.seed_dir / "bccwj_n3_frequency_subset.tsv"


class BatchServiceError(Exception):
    pass


def _study_end_date(config: StudyConfig) -> date:
    return config.start_date + timedelta(weeks=config.new_card_weeks)


def _load_schedule(db: Session) -> dict[str, int]:
    rows = db.query(KanjiSchedule).join(Kanji).all()
    return {row.kanji.kanji: row.batch_number for row in rows}


def _load_coverage(db: Session) -> set[str]:
    rows = db.query(KanjiCoverage).join(Kanji).all()
    return {row.kanji.kanji for row in rows}


def _load_candidates(db: Session) -> list[VocabCandidate]:
    rows = db.query(Vocab).filter(Vocab.status == "available").all()
    return [
        VocabCandidate(
            id=v.id,
            kanji_form=v.kanji_form,
            hiragana_form=v.hiragana_form,
            kanji_chars=frozenset(extract_kanji(v.kanji_form)),
        )
        for v in rows
    ]


def generate_draft_batch(db: Session, batch_n: int, today: date) -> SelectionResult:
    existing = db.get(Batch, batch_n)
    if existing is not None and existing.status != "draft":
        raise BatchServiceError(f"batch {batch_n} already {existing.status}; cannot regenerate")

    study_config = db.query(StudyConfig).one_or_none()
    if study_config is None:
        raise BatchServiceError("study_config has not been set")

    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    target_kanji = {k for k, b in schedule.items() if b == batch_n}
    candidates = _load_candidates(db)
    frequency_lookup = load_bccwj_frequency_subset(BCCWJ_SUBSET_PATH)

    result = select_batch(
        candidates=candidates,
        coverage=coverage,
        schedule=schedule,
        target_kanji=target_kanji,
        batch_n=batch_n,
        config=SelectionConfig(
            daily_minimum=study_config.daily_minimum, study_end_date=_study_end_date(study_config)
        ),
        today=today,
        frequency_lookup=frequency_lookup,
    )

    if existing is None:
        db.add(Batch(batch_number=batch_n, status="draft", weekly_target_used=result.weekly_target_used))
    else:
        existing.weekly_target_used = result.weekly_target_used

    # Clear any previous draft assignment for this batch before applying the
    # new selection (regenerating a draft must not leave stale assignments).
    db.query(Vocab).filter(Vocab.assigned_batch == batch_n).update(
        {Vocab.status: "available", Vocab.assigned_batch: None, Vocab.needs_kanji_reading: False}
    )

    selected_by_id = {w.vocab_id: w for w in result.selected}
    if selected_by_id:
        vocab_rows = db.query(Vocab).filter(Vocab.id.in_(selected_by_id.keys())).all()
        for v in vocab_rows:
            w = selected_by_id[v.id]
            v.status = "assigned"
            v.assigned_batch = batch_n
            v.needs_kanji_reading = w.needs_kanji_reading

    db.commit()
    return result


@dataclass
class ReplacementCandidate:
    vocab_id: int
    kanji_form: str
    hiragana_form: str


def get_eligible_replacements(db: Session, batch_n: int) -> list[ReplacementCandidate]:
    """Words the UI may offer as a swap-in for batch_n: available, and
    passing the same skip-ahead guard used during generation (zero Future
    kanji -- Known/Orphan status doesn't affect eligibility, only whether a
    reading card would be generated, which the caller decides separately).
    """
    schedule = _load_schedule(db)
    future_kanji = {k for k, b in schedule.items() if b > batch_n}

    candidates = _load_candidates(db)
    eligible = [c for c in candidates if not (c.kanji_chars & future_kanji)]
    return [
        ReplacementCandidate(vocab_id=c.id, kanji_form=c.kanji_form, hiragana_form=c.hiragana_form)
        for c in eligible
    ]


@dataclass
class BatchWordDetail:
    vocab_id: int
    kanji_form: str
    hiragana_form: str
    meaning: str
    is_target_linked: bool
    needs_kanji_reading: bool
    covers_target_kanji: list[str]


@dataclass
class BatchDetail:
    batch_number: int
    status: str
    weekly_target_used: int
    target_kanji: list[str]
    target_kanji_coverage: dict[str, list[int]]
    words: list[BatchWordDetail]


def get_batch_detail(db: Session, batch_n: int) -> BatchDetail:
    batch = db.get(Batch, batch_n)
    if batch is None:
        raise BatchServiceError(f"batch {batch_n} does not exist")

    schedule = _load_schedule(db)
    target_kanji = {k for k, b in schedule.items() if b == batch_n}

    vocab_rows = db.query(Vocab).filter(Vocab.assigned_batch == batch_n).all()
    words: list[BatchWordDetail] = []
    coverage_map: dict[str, list[int]] = {k: [] for k in target_kanji}

    for v in vocab_rows:
        chars = extract_kanji(v.kanji_form)
        covers = sorted(chars & target_kanji)
        for k in covers:
            coverage_map[k].append(v.id)
        words.append(
            BatchWordDetail(
                vocab_id=v.id,
                kanji_form=v.kanji_form,
                hiragana_form=v.hiragana_form,
                meaning=v.meaning,
                is_target_linked=bool(covers),
                needs_kanji_reading=v.needs_kanji_reading,
                covers_target_kanji=covers,
            )
        )

    return BatchDetail(
        batch_number=batch_n,
        status=batch.status,
        weekly_target_used=batch.weekly_target_used,
        target_kanji=sorted(target_kanji),
        target_kanji_coverage=coverage_map,
        words=words,
    )


def _require_draft_batch(db: Session, batch_n: int) -> Batch:
    batch = db.get(Batch, batch_n)
    if batch is None:
        raise BatchServiceError(f"batch {batch_n} does not exist")
    if batch.status != "draft":
        raise BatchServiceError(f"batch {batch_n} is not a draft (status={batch.status}); edits are locked")
    return batch


def remove_word(db: Session, batch_n: int, vocab_id: int) -> None:
    _require_draft_batch(db, batch_n)
    vocab = db.query(Vocab).filter(Vocab.id == vocab_id, Vocab.assigned_batch == batch_n).one_or_none()
    if vocab is None:
        raise BatchServiceError(f"vocab {vocab_id} is not assigned to batch {batch_n}")
    vocab.status = "available"
    vocab.assigned_batch = None
    vocab.needs_kanji_reading = False
    db.commit()


def add_word(db: Session, batch_n: int, vocab_id: int) -> None:
    _require_draft_batch(db, batch_n)
    eligible_ids = {c.vocab_id for c in get_eligible_replacements(db, batch_n)}
    if vocab_id not in eligible_ids:
        raise BatchServiceError(f"vocab {vocab_id} is not eligible for batch {batch_n} (skip-ahead guard)")

    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    target_kanji = {k for k, b in schedule.items() if b == batch_n}
    known_kanji = coverage | target_kanji

    vocab = db.get(Vocab, vocab_id)
    candidate = VocabCandidate(
        id=vocab.id,
        kanji_form=vocab.kanji_form,
        hiragana_form=vocab.hiragana_form,
        kanji_chars=frozenset(extract_kanji(vocab.kanji_form)),
    )
    classes = classify_word_kanji(candidate, known_kanji, schedule, batch_n)
    covers_target = bool(candidate.kanji_chars & target_kanji)

    vocab.status = "assigned"
    vocab.assigned_batch = batch_n
    vocab.needs_kanji_reading = covers_target and orphan_kanji_count(classes) == 0
    db.commit()


def toggle_reading(db: Session, batch_n: int, vocab_id: int) -> bool:
    _require_draft_batch(db, batch_n)
    vocab = db.query(Vocab).filter(Vocab.id == vocab_id, Vocab.assigned_batch == batch_n).one_or_none()
    if vocab is None:
        raise BatchServiceError(f"vocab {vocab_id} is not assigned to batch {batch_n}")
    vocab.needs_kanji_reading = not vocab.needs_kanji_reading
    db.commit()
    return vocab.needs_kanji_reading


def swap_word(db: Session, batch_n: int, old_vocab_id: int, new_vocab_id: int) -> None:
    remove_word(db, batch_n, old_vocab_id)
    add_word(db, batch_n, new_vocab_id)


def finalize_batch(db: Session, batch_n: int) -> None:
    batch = db.get(Batch, batch_n)
    if batch is None:
        raise BatchServiceError(f"batch {batch_n} does not exist")
    if batch.status != "draft":
        raise BatchServiceError(f"batch {batch_n} is not a draft (status={batch.status})")

    schedule = _load_schedule(db)
    target_kanji = {k for k, b in schedule.items() if b == batch_n}

    kanji_by_char = {k.kanji: k for k in db.query(Kanji).filter(Kanji.kanji.in_(target_kanji)).all()}
    already_covered = {
        c.kanji_id
        for c in db.query(KanjiCoverage)
        .filter(KanjiCoverage.coverage_source == "n3_batch", KanjiCoverage.batch_number == batch_n)
        .all()
    }

    for char in target_kanji:
        kanji = kanji_by_char.get(char)
        if kanji is None:
            continue  # target kanji not yet imported into the kanji table; nothing to cover
        if kanji.id in already_covered:
            continue
        db.add(KanjiCoverage(kanji_id=kanji.id, coverage_source="n3_batch", batch_number=batch_n))

    batch.status = "finalized"
    db.commit()


def unfinalize_batch(db: Session, batch_n: int) -> None:
    """Rolls back exactly the coverage rows this batch's finalization added
    -- not a blanket coverage wipe -- and returns the batch to draft. Vocab
    word assignments are left untouched so the draft can keep being edited.
    """
    batch = db.get(Batch, batch_n)
    if batch is None:
        raise BatchServiceError(f"batch {batch_n} does not exist")
    if batch.status != "finalized":
        raise BatchServiceError(f"batch {batch_n} is not finalized (status={batch.status})")

    db.query(KanjiCoverage).filter(
        KanjiCoverage.coverage_source == "n3_batch", KanjiCoverage.batch_number == batch_n
    ).delete(synchronize_session=False)

    batch.status = "draft"
    db.commit()
