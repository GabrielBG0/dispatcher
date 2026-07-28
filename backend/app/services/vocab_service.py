"""General vocab-row read/search/edit operations for the standalone review
UI. Distinct from batch_service.edit_word, which only edits a word already
assigned to a specific batch -- most rows needing a manual kanji-form fix
are still `available` (never made it into a batch), so this works on any
vocab row regardless of status.
"""

from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enrichment.jisho_client import JishoClient
from app.enrichment.kana_kanji import KanjiCandidate, rank_candidates
from app.kanji_utils import extract_kanji
from app.models.vocab import Vocab

# Source tag marking a kana-only row a human has already looked at and
# confirmed has no real kanji spelling worth using (e.g. これ, とても).
# Without this, the kana-only review queue can never shrink: a reviewed row
# looks identical to an untouched one on every future scan (kanji_form still
# equals hiragana_form -- that's the whole point of confirming it), so
# list_vocab(kana_only=True) excludes tagged rows by default.
REVIEWED_KANA_ONLY_TAG = "reviewed_kana_only"


class VocabServiceError(Exception):
    pass


@dataclass
class VocabListResult:
    total: int
    items: list[Vocab]


def _is_reviewed_kana_only(row: Vocab) -> bool:
    return REVIEWED_KANA_ONLY_TAG in (row.source or "").split(",")


def list_vocab(
    db: Session,
    *,
    kana_only: bool = False,
    include_reviewed: bool = False,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> VocabListResult:
    query = db.query(Vocab)
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(Vocab.hiragana_form.ilike(like), Vocab.meaning.ilike(like), Vocab.kanji_form.ilike(like))
        )
    query = query.order_by(Vocab.id)

    if kana_only:
        # extract_kanji is Python-side (CJK regex), not expressible as a SQL
        # predicate, so kana-only rows are filtered after fetching --
        # acceptable at this table's size (a few thousand rows).
        rows = [r for r in query.all() if not extract_kanji(r.kanji_form)]
        if not include_reviewed:
            rows = [r for r in rows if not _is_reviewed_kana_only(r)]
        return VocabListResult(total=len(rows), items=rows[offset : offset + limit])

    return VocabListResult(total=query.count(), items=query.offset(offset).limit(limit).all())


def mark_kana_only_confirmed(db: Session, vocab_id: int) -> Vocab:
    """Tags a row as reviewed-and-genuinely-kana-only so it drops out of the
    default review queue. Idempotent -- confirming an already-confirmed row
    is a no-op, not a duplicate tag.
    """
    row = db.get(Vocab, vocab_id)
    if row is None:
        raise VocabServiceError(f"vocab {vocab_id} not found")

    if not _is_reviewed_kana_only(row):
        row.source = f"{row.source},{REVIEWED_KANA_ONLY_TAG}".strip(",") if row.source else REVIEWED_KANA_ONLY_TAG
        db.commit()
        db.refresh(row)
    return row


async def get_kanji_candidates(
    db: Session, vocab_id: int, client: JishoClient, *, reading: str | None = None
) -> list[KanjiCandidate]:
    """`reading` overrides the row's stored hiragana_form for the Jisho
    search -- lets a reviewer try a corrected or alternate reading (e.g. a
    typo, or one half of a slash-separated dual reading like
    アイデア/アイディア) without first saving it to the row.
    """
    row = db.get(Vocab, vocab_id)
    if row is None:
        raise VocabServiceError(f"vocab {vocab_id} not found")
    lookup_reading = reading if reading is not None else row.hiragana_form
    results = await client.search_words(lookup_reading)
    return rank_candidates(lookup_reading, row.meaning or "", results)


def update_vocab(
    db: Session,
    vocab_id: int,
    *,
    kanji_form: str | None = None,
    hiragana_form: str | None = None,
    meaning: str | None = None,
    usually_kana: bool | None = None,
    part_of_speech: str | None = None,
) -> Vocab:
    row = db.get(Vocab, vocab_id)
    if row is None:
        raise VocabServiceError(f"vocab {vocab_id} not found")

    if kanji_form is not None:
        row.kanji_form = kanji_form
    if hiragana_form is not None:
        row.hiragana_form = hiragana_form
    if meaning is not None:
        row.meaning = meaning
    if usually_kana is not None:
        row.usually_kana = usually_kana
    if part_of_speech is not None:
        row.part_of_speech = part_of_speech

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise VocabServiceError(
            "another vocab row already has this exact kanji_form/hiragana_form/meaning combination -- "
            "this looks like a duplicate; use the Duplicate vocab words tool instead of editing it away"
        ) from exc

    db.refresh(row)
    return row


def delete_vocab(db: Session, vocab_id: int) -> None:
    """Deletes a single vocab row outright. Mainly for the case update_vocab
    just refused: an edit collided with another row's natural key, meaning
    the row being edited turned out to be a duplicate of one that already
    has the correct data -- at that point the fix is deleting the row being
    edited, not re-editing it further. Unlike dedupe_service.resolve_duplicate_group,
    this takes no "keep" counterpart and doesn't require the two rows to
    share a spelling, since the whole point is the edit made them diverge
    from what's already in the table.
    """
    row = db.get(Vocab, vocab_id)
    if row is None:
        raise VocabServiceError(f"vocab {vocab_id} not found")
    db.delete(row)
    db.commit()
