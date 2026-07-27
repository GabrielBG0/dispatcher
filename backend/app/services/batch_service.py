"""Orchestration layer between the batches router and the pure selection
core. This is the only layer allowed to mix a DB session with calls into
app.selection -- select_batch itself never touches SQLAlchemy.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.enrichment.jisho_client import JishoClient
from app.enrichment.jobs import format_meaning, pos_from_jisho
from app.ingestion.bccwj_frequency_loader import load_bccwj_frequency_subset
from app.kanji_utils import extract_kanji
from app.models.batch import Batch
from app.models.kanji import Kanji
from app.models.kanji_coverage import KanjiCoverage
from app.models.kanji_schedule import KanjiSchedule
from app.models.study_config import StudyConfig
from app.models.vocab import Vocab
from app.selection.classify import classify_word_kanji, has_future_kanji, orphan_kanji_count
from app.selection.select_batch import select_batch
from app.selection.types import SelectionConfig, SelectionResult, VocabCandidate

# Jisho word-search results are capped the same way the local top-10 list
# is, so the online fallback panel doesn't dwarf the rest of the UI.
JISHO_SUGGESTION_LIMIT = 10

BCCWJ_SUBSET_PATH = settings.seed_dir / "bccwj_n3_frequency_subset.tsv"

# Enforced floor on new (not-yet-known) kanji per batch. Many raw source-file
# batches now contain far fewer real new kanji than their nominal ~50, since
# a large chunk of the schedule turned out to already be known once the
# seen-in-class data was corrected.
KANJI_MINIMUM_PER_BATCH = 23


class BatchServiceError(Exception):
    pass


def _study_end_date(config: StudyConfig) -> date:
    return config.start_date + timedelta(weeks=config.new_card_weeks)


def _distribute_evenly(total: int, bins: int) -> list[int]:
    """Splits `total` into `bins` non-negative chunks that differ by at
    most one, larger chunks first (e.g. 17 into 2 bins -> [9, 8])."""
    if bins <= 0:
        return []
    base, extra = divmod(total, bins)
    return [base + 1 if i < extra else base for i in range(bins)]


def _load_schedule(db: Session) -> dict[str, int]:
    """Repacked teaching schedule, spread over exactly
    study_config.new_card_weeks batches: not-yet-covered kanji, in original
    teaching order (imported batch_number, then difficulty_rank), packed
    into as many KANJI_MINIMUM_PER_BATCH-sized batches as fit, with
    whatever's left split evenly across the trailing batches (e.g. 339
    kanji at a 23 floor over 16 weeks gives 14 batches of 23 plus a final
    two of 9 and 8 -- only those last batches fall below the floor). If
    there's *more* kanji than new_card_weeks * KANJI_MINIMUM_PER_BATCH can
    hold at the floor size, everything is instead spread evenly across all
    new_card_weeks batches (each then comfortably exceeds the floor).
    Recomputed fresh from current coverage on every call: nothing is
    persisted, so a schedule re-import can't clobber it and it stays
    correct as coverage (seen-in-class imports, batch finalization) grows
    over time.
    """
    study_config = db.query(StudyConfig).one_or_none()
    if study_config is None:
        raise BatchServiceError("study_config has not been set")
    total_weeks = study_config.new_card_weeks

    rows = (
        db.query(KanjiSchedule)
        .join(Kanji)
        .order_by(
            KanjiSchedule.batch_number,
            Kanji.difficulty_rank.is_(None),
            Kanji.difficulty_rank,
            KanjiSchedule.kanji_id,
        )
        .all()
    )
    coverage = _load_coverage(db)
    ordered_unknown = [row.kanji.kanji for row in rows if row.kanji.kanji not in coverage]

    full_batches = len(ordered_unknown) // KANJI_MINIMUM_PER_BATCH
    remainder = len(ordered_unknown) % KANJI_MINIMUM_PER_BATCH
    remaining_slots = total_weeks - full_batches

    if remaining_slots <= 0:
        sizes = _distribute_evenly(len(ordered_unknown), total_weeks)
    else:
        sizes = [KANJI_MINIMUM_PER_BATCH] * full_batches + _distribute_evenly(remainder, remaining_slots)

    schedule: dict[str, int] = {}
    idx = 0
    for batch_n, size in enumerate(sizes, start=1):
        for kanji in ordered_unknown[idx : idx + size]:
            schedule[kanji] = batch_n
        idx += size
    return schedule


def _load_coverage(db: Session) -> set[str]:
    rows = db.query(KanjiCoverage).join(Kanji).all()
    return {row.kanji.kanji for row in rows}


def _load_target_kanji(schedule: dict[str, int], coverage: set[str], batch_n: int) -> set[str]:
    """Kanji the schedule assigns to this batch, minus anything already
    covered (pre_n3 baseline or a prior finalized batch). Already-known
    kanji must never force a set-cover word or a reading card.
    """
    return {k for k, b in schedule.items() if b == batch_n} - coverage


def _load_candidates(db: Session) -> list[VocabCandidate]:
    rows = db.query(Vocab).filter(Vocab.status == "available").all()
    return [
        VocabCandidate(
            id=v.id,
            kanji_form=v.kanji_form,
            hiragana_form=v.hiragana_form,
            kanji_chars=frozenset(extract_kanji(v.kanji_form)),
            usually_kana=v.usually_kana,
        )
        for v in rows
    ]


def _load_full_candidate_pool(db: Session) -> list[VocabCandidate]:
    """Every vocab row regardless of status, tagged with its current status.
    Used only by select_batch's diagnostic + seen-in-class fallback
    machinery -- never as a primary selection pool (see _load_candidates).
    """
    rows = db.query(Vocab).all()
    return [
        VocabCandidate(
            id=v.id,
            kanji_form=v.kanji_form,
            hiragana_form=v.hiragana_form,
            kanji_chars=frozenset(extract_kanji(v.kanji_form)),
            usually_kana=v.usually_kana,
            status=v.status,
        )
        for v in rows
    ]


def is_seen_in_class_fallback(vocab: Vocab) -> bool:
    """True iff this vocab row's current batch assignment came from the
    seen-in-class fallback in select_batch, not the strict/normal pass.
    Derived, not stored: a normal selection always flips status to
    "assigned"; a fallback selection deliberately leaves status as
    "seen_in_class" (see generate_draft_batch) -- no schema migration
    needed, since this app has no Alembic step, only additive create_all().
    """
    return vocab.status == "seen_in_class"


def generate_draft_batch(db: Session, batch_n: int, today: date) -> SelectionResult:
    existing = db.get(Batch, batch_n)
    if existing is not None and existing.status != "draft":
        raise BatchServiceError(f"batch {batch_n} already {existing.status}; cannot regenerate")

    study_config = db.query(StudyConfig).one_or_none()
    if study_config is None:
        raise BatchServiceError("study_config has not been set")

    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    target_kanji = _load_target_kanji(schedule, coverage, batch_n)

    # Clear any previous draft assignment for this batch *before* loading
    # candidates -- otherwise a word already in this batch is stuck at
    # status "assigned" and invisible to its own re-selection pool.
    db.query(Vocab).filter(Vocab.assigned_batch == batch_n, Vocab.status == "assigned").update(
        {Vocab.status: "available", Vocab.assigned_batch: None, Vocab.needs_kanji_reading: False}
    )
    # Same clear for a stale seen-in-class fallback assignment -- but this
    # one must NOT flip status to "available", since the word was never a
    # fresh/unused word to begin with.
    db.query(Vocab).filter(Vocab.assigned_batch == batch_n, Vocab.status == "seen_in_class").update(
        {Vocab.assigned_batch: None, Vocab.needs_kanji_reading: False}
    )

    candidates = _load_candidates(db)
    full_candidate_pool = _load_full_candidate_pool(db)
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
        full_candidate_pool=full_candidate_pool,
    )

    if existing is None:
        db.add(Batch(batch_number=batch_n, status="draft", weekly_target_used=result.weekly_target_used))
    else:
        existing.weekly_target_used = result.weekly_target_used

    selected_by_id = {w.vocab_id: w for w in result.selected}
    if selected_by_id:
        vocab_rows = db.query(Vocab).filter(Vocab.id.in_(selected_by_id.keys())).all()
        for v in vocab_rows:
            w = selected_by_id[v.id]
            if not w.used_seen_in_class_fallback:
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
    usually_kana: bool


def get_eligible_replacements(db: Session, batch_n: int) -> list[ReplacementCandidate]:
    """Words the UI may offer as a swap-in for batch_n: available, and
    passing the same skip-ahead guard used during generation (zero Future
    kanji -- Known/Orphan status doesn't affect eligibility, only whether a
    reading card would be generated, which the caller decides separately).
    """
    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    future_kanji = {k for k, b in schedule.items() if b > batch_n} - coverage

    candidates = _load_candidates(db)
    eligible = [c for c in candidates if not (c.kanji_chars & future_kanji)]
    return [
        ReplacementCandidate(
            vocab_id=c.id, kanji_form=c.kanji_form, hiragana_form=c.hiragana_form, usually_kana=c.usually_kana
        )
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
    usually_kana: bool
    covers_target_kanji: list[str]
    used_seen_in_class_fallback: bool


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
    coverage = _load_coverage(db)
    target_kanji = _load_target_kanji(schedule, coverage, batch_n)

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
                usually_kana=v.usually_kana,
                covers_target_kanji=covers,
                used_seen_in_class_fallback=is_seen_in_class_fallback(v),
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


def _take_out_of_batch(db: Session, batch_n: int, vocab_id: int, exclude: bool) -> Vocab | None:
    """Un-assigns vocab_id from batch_n, if it's actually there. `exclude`
    sends it to "excluded" (never selected again, for any reason the user
    chooses) instead of back to "available" (open to selection in a later
    batch) -- distinct from "seen_in_class", which specifically means
    "already knew this before starting". Returns None (no-op) if the word
    isn't assigned to this batch.
    """
    vocab = db.query(Vocab).filter(Vocab.id == vocab_id, Vocab.assigned_batch == batch_n).one_or_none()
    if vocab is None:
        return None
    if exclude:
        vocab.status = "excluded"
    elif not is_seen_in_class_fallback(vocab):
        vocab.status = "available"
    # else: leave status as "seen_in_class" -- restoring, not un-assigning a fresh word.
    vocab.assigned_batch = None
    vocab.needs_kanji_reading = False
    return vocab


def _assign_word(db: Session, batch_n: int, vocab_id: int) -> None:
    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    target_kanji = _load_target_kanji(schedule, coverage, batch_n)
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

    # A seen_in_class word being manually (or fallback-) included keeps its
    # status -- it isn't a fresh/unused word, so it must not be "used up"
    # the way normal assignment marks a word (see is_seen_in_class_fallback).
    if vocab.status != "seen_in_class":
        vocab.status = "assigned"
    vocab.assigned_batch = batch_n
    vocab.needs_kanji_reading = covers_target and orphan_kanji_count(classes) == 0


def _passes_skip_ahead_guard(db: Session, batch_n: int, kanji_form: str) -> bool:
    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    future_kanji = {k for k, b in schedule.items() if b > batch_n} - coverage
    return not (frozenset(extract_kanji(kanji_form)) & future_kanji)


def manual_include_word(db: Session, batch_n: int, vocab_id: int) -> None:
    """Manually assigns vocab_id into batch_n regardless of its current
    status or assignment -- available, seen_in_class, excluded, or already
    assigned to a different *draft* batch (in which case it's moved here,
    un-assigning it from the other batch first). Refuses to steal from a
    finalized/exported batch, and still enforces the skip-ahead guard --
    this manual panel doesn't get to bypass that invariant.
    """
    _require_draft_batch(db, batch_n)
    vocab = db.get(Vocab, vocab_id)
    if vocab is None:
        raise BatchServiceError(f"vocab {vocab_id} does not exist")

    if vocab.assigned_batch is not None and vocab.assigned_batch != batch_n:
        other_batch = db.get(Batch, vocab.assigned_batch)
        if other_batch is not None and other_batch.status != "draft":
            raise BatchServiceError(
                f"vocab {vocab_id} is locked in batch {vocab.assigned_batch} (status={other_batch.status})"
            )
        vocab.assigned_batch = None
        vocab.needs_kanji_reading = False

    if not _passes_skip_ahead_guard(db, batch_n, vocab.kanji_form):
        raise BatchServiceError(f"vocab {vocab_id} is not eligible for batch {batch_n} (skip-ahead guard)")

    _assign_word(db, batch_n, vocab_id)
    db.commit()


def manual_exclude_word(db: Session, batch_n: int, vocab_id: int) -> None:
    """Permanently excludes vocab_id. If it's currently in batch_n, this is
    exactly remove_word(..., exclude=True). If it's unassigned (available,
    seen_in_class, or already excluded), it's marked excluded directly --
    no assigned_batch requirement, since it was never in any batch to begin
    with. Refuses if it's assigned to a *different* batch -- remove it from
    that batch's own review page first.
    """
    vocab = db.get(Vocab, vocab_id)
    if vocab is None:
        raise BatchServiceError(f"vocab {vocab_id} does not exist")

    if vocab.assigned_batch == batch_n:
        remove_word(db, batch_n, vocab_id, exclude=True)
        return

    if vocab.assigned_batch is not None:
        raise BatchServiceError(
            f"vocab {vocab_id} is assigned to a different batch ({vocab.assigned_batch}); "
            "remove it from there first"
        )

    vocab.status = "excluded"
    db.commit()


@dataclass
class KanjiWordOption:
    vocab_id: int
    kanji_form: str
    hiragana_form: str
    meaning: str
    usually_kana: bool
    status: str
    assigned_batch: int | None
    assigned_batch_status: str | None
    core_rank: int | None


@dataclass
class JishoWordSuggestion:
    kanji_form: str
    hiragana_form: str
    meaning: str
    part_of_speech: str
    jlpt: list[str]
    is_common: bool
    includable: bool
    # Populated only when includable is False: the not-yet-known kanji
    # (other than the one being searched) that pushes this word to a later
    # batch, and the batch it's currently scheduled for.
    blocking_kanji: str | None = None
    blocking_batch: int | None = None
    # Any kanji in this word (other than the one being searched) that's
    # already known -- i.e. it pairs this batch's new kanji with one
    # you've already seen, which is worth flagging at a glance.
    seen_kanji: list[str] = field(default_factory=list)


@dataclass
class KanjiWordOptions:
    kanji: str
    in_batch: list[KanjiWordOption]
    other_batches: list[KanjiWordOption]
    top_common: list[KanjiWordOption]


def get_kanji_word_options(db: Session, batch_n: int, kanji: str) -> KanjiWordOptions:
    """Powers the batch review page's per-kanji drill-down: every local
    vocab-table word containing `kanji`, split into what's already in this
    batch, what's claimed by other batches, and (regardless of status) the
    10 most common words containing it -- so the user can see the full
    picture and decide what to include or exclude. Purely local/DB-bound
    (no network call), so this stays fast on the common path -- the Jisho
    search is a separate, explicitly-triggered action (search_jisho_word_suggestions).
    """
    if db.get(Batch, batch_n) is None:
        raise BatchServiceError(f"batch {batch_n} does not exist")

    frequency_lookup = load_bccwj_frequency_subset(BCCWJ_SUBSET_PATH)
    batch_status_by_number = {b.batch_number: b.status for b in db.query(Batch).all()}
    containing = [v for v in db.query(Vocab).all() if kanji in extract_kanji(v.kanji_form)]

    def _to_option(v: Vocab) -> KanjiWordOption:
        freq = frequency_lookup.get(v.kanji_form)
        return KanjiWordOption(
            vocab_id=v.id,
            kanji_form=v.kanji_form,
            hiragana_form=v.hiragana_form,
            meaning=v.meaning,
            usually_kana=v.usually_kana,
            status=v.status,
            assigned_batch=v.assigned_batch,
            assigned_batch_status=batch_status_by_number.get(v.assigned_batch) if v.assigned_batch else None,
            core_rank=freq.core_rank if freq else None,
        )

    def _rank_key(v: Vocab) -> tuple:
        freq = frequency_lookup.get(v.kanji_form)
        rank = freq.core_rank if freq and freq.core_rank is not None else math.inf
        return (rank, len(v.kanji_form), v.id)

    in_batch = [_to_option(v) for v in containing if v.assigned_batch == batch_n]
    other_batches = [_to_option(v) for v in containing if v.assigned_batch is not None and v.assigned_batch != batch_n]
    top_common = [_to_option(v) for v in sorted(containing, key=_rank_key)[:10]]

    return KanjiWordOptions(kanji=kanji, in_batch=in_batch, other_batches=other_batches, top_common=top_common)


async def search_jisho_word_suggestions(db: Session, batch_n: int, kanji: str) -> list[JishoWordSuggestion]:
    """Explicitly-triggered online search (a "Search Jisho" button, not part
    of the default panel load): looks up words containing `kanji` on Jisho,
    so the user can see options the local N3 vocab list is blind to, whether
    or not local words already exist for this kanji. Prefers N3-tagged
    words, then falls back to merely common words, then whatever Jisho
    returned at all if neither tier has anything. The bare kanji character
    itself (a single-character "word") is dropped, as is any word whose
    written form already matches a local vocab row -- this is meant to
    surface new options, not duplicate what the local sections already show
    (dedup is deliberately by kanji_form only, not the full natural key, so
    a local word already covering this kanji form is never re-suggested
    regardless of its reading).

    Each suggestion is also tagged with `includable`, checked against the
    same skip-ahead guard `manual_include_word` will enforce -- shown for
    context (a suggestion isn't hidden just because it's blocked right now)
    but flagged (with which kanji/batch is blocking it) so a multi-select
    can't be used to pick a guaranteed failure, and the UI labels it
    "for later" instead. `seen_kanji` separately lists any of the word's
    other kanji that are already known (coverage, not just "not future") --
    a word pairing this batch's new kanji with one already seen reinforces
    prior learning, worth flagging at a glance.

    Raises BatchServiceError (rather than swallowing the failure) if Jisho
    can't be reached -- this is now a foreground, user-triggered action, so
    the failure needs to reach the UI as an error, not look identical to
    "Jisho had nothing new".
    """
    if db.get(Batch, batch_n) is None:
        raise BatchServiceError(f"batch {batch_n} does not exist")

    exclude_kanji_forms = {v.kanji_form for v in db.query(Vocab).all() if kanji in extract_kanji(v.kanji_form)}

    client = JishoClient()
    try:
        results = await client.search_words(kanji)
    except Exception as exc:  # noqa: BLE001 - re-raised as a BatchServiceError, not swallowed
        raise BatchServiceError(f"Could not reach Jisho: {exc}") from exc
    finally:
        await client.aclose()

    candidates = [
        r
        for r in results
        if r.word and len(r.word) > 1 and kanji in r.word and r.senses and r.word not in exclude_kanji_forms
    ]
    n3_tier = [r for r in candidates if "jlpt-n3" in r.jlpt]
    common_tier = [r for r in candidates if r.is_common]
    tier = n3_tier or common_tier or candidates

    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    future_kanji = {k for k, b in schedule.items() if b > batch_n} - coverage

    def _blocking_kanji(word: str) -> str | None:
        blockers = frozenset(extract_kanji(word)) & future_kanji
        return min(blockers, key=lambda k: (schedule.get(k, math.inf), k)) if blockers else None

    suggestions = []
    for r in tier[:JISHO_SUGGESTION_LIMIT]:
        blocking_kanji = _blocking_kanji(r.word)
        other_chars = frozenset(extract_kanji(r.word)) - {kanji}
        suggestions.append(
            JishoWordSuggestion(
                kanji_form=r.word,
                hiragana_form=r.reading or r.word,
                meaning=format_meaning(r.senses),
                part_of_speech=pos_from_jisho(r.senses[0].parts_of_speech),
                jlpt=r.jlpt,
                is_common=r.is_common,
                includable=blocking_kanji is None,
                blocking_kanji=blocking_kanji,
                blocking_batch=schedule.get(blocking_kanji) if blocking_kanji else None,
                seen_kanji=sorted(other_chars & coverage),
            )
        )
    return suggestions


def import_and_include_jisho_word(
    db: Session, batch_n: int, kanji_form: str, hiragana_form: str, meaning: str, part_of_speech: str
) -> int:
    """Turns a Jisho suggestion into a real vocab row (reusing one that
    already matches the natural key, if a prior import already created it)
    and immediately runs it through manual_include_word -- same draft-only
    and skip-ahead-guard rules as any other manual include. Nothing is
    persisted if the include fails: the insert is only flushed, not
    committed, until manual_include_word's own commit at the end, so a
    rejected word leaves no orphaned row behind.
    """
    vocab = (
        db.query(Vocab)
        .filter(Vocab.kanji_form == kanji_form, Vocab.hiragana_form == hiragana_form, Vocab.meaning == meaning)
        .one_or_none()
    )
    if vocab is None:
        vocab = Vocab(
            kanji_form=kanji_form,
            hiragana_form=hiragana_form,
            meaning=meaning,
            part_of_speech=part_of_speech or "general",
            status="available",
            source="jisho",
        )
        db.add(vocab)
        db.flush()

    manual_include_word(db, batch_n, vocab.id)
    return vocab.id


def _best_replacement(db: Session, batch_n: int, exclude_ids: set[int]) -> ReplacementCandidate | None:
    """Picks the single best eligible replacement for batch_n, in the same
    preference order as initial generation: a word covering a target kanji
    that isn't yet covered by anything else in the batch comes first, then
    fewer orphan kanji, then more common/shorter (see
    select_batch._sort_key -- duplicated here rather than imported since
    it's a three-line tie-break and this module intentionally doesn't
    depend on select_batch's private helpers).

    `exclude_ids` lets a bulk caller avoid picking the same replacement
    twice across several words being replaced in one operation.
    """
    schedule = _load_schedule(db)
    coverage = _load_coverage(db)
    target_kanji = _load_target_kanji(schedule, coverage, batch_n)
    known_kanji = coverage | target_kanji

    assigned = db.query(Vocab).filter(Vocab.assigned_batch == batch_n).all()
    assigned_ids = {v.id for v in assigned}
    covered_target: set[str] = set()
    for v in assigned:
        covered_target |= extract_kanji(v.kanji_form) & target_kanji
    uncovered_target = target_kanji - covered_target

    frequency_lookup = load_bccwj_frequency_subset(BCCWJ_SUBSET_PATH)

    best: ReplacementCandidate | None = None
    best_key: tuple | None = None
    for r in get_eligible_replacements(db, batch_n):
        if r.vocab_id in assigned_ids or r.vocab_id in exclude_ids:
            continue
        kanji_chars = frozenset(extract_kanji(r.kanji_form))
        candidate = VocabCandidate(
            id=r.vocab_id, kanji_form=r.kanji_form, hiragana_form=r.hiragana_form, kanji_chars=kanji_chars
        )
        classes = classify_word_kanji(candidate, known_kanji, schedule, batch_n)
        orphan_count = orphan_kanji_count(classes)
        freq = frequency_lookup.get(r.kanji_form)
        rank = freq.core_rank if freq and freq.core_rank is not None else math.inf
        key = (0 if kanji_chars & uncovered_target else 1, orphan_count, rank, len(r.kanji_form), r.vocab_id)
        if best_key is None or key < best_key:
            best_key = key
            best = r
    return best


def remove_word(db: Session, batch_n: int, vocab_id: int, exclude: bool = False) -> None:
    _require_draft_batch(db, batch_n)
    if _take_out_of_batch(db, batch_n, vocab_id, exclude) is None:
        raise BatchServiceError(f"vocab {vocab_id} is not assigned to batch {batch_n}")
    db.commit()


def remove_words(db: Session, batch_n: int, vocab_ids: list[int], exclude: bool = False) -> list[int]:
    """Bulk remove_word. Silently skips any id not actually assigned to this
    batch (e.g. stale selection state in the UI) rather than aborting the
    whole request over one bad id; returns the ids that were actually
    removed.
    """
    _require_draft_batch(db, batch_n)
    removed = [vid for vid in vocab_ids if _take_out_of_batch(db, batch_n, vid, exclude) is not None]
    db.commit()
    return removed


def add_word(db: Session, batch_n: int, vocab_id: int) -> None:
    _require_draft_batch(db, batch_n)
    eligible_ids = {c.vocab_id for c in get_eligible_replacements(db, batch_n)}
    if vocab_id not in eligible_ids:
        raise BatchServiceError(f"vocab {vocab_id} is not eligible for batch {batch_n} (skip-ahead guard)")
    _assign_word(db, batch_n, vocab_id)
    db.commit()


def replace_word(db: Session, batch_n: int, old_vocab_id: int, exclude: bool = False) -> ReplacementCandidate | None:
    """Removes old_vocab_id (optionally excluding it from all future
    batches) and auto-picks the best available replacement in one step.
    Returns the replacement added, or None if no eligible word remains --
    the removal happens either way.
    """
    _require_draft_batch(db, batch_n)
    if _take_out_of_batch(db, batch_n, old_vocab_id, exclude) is None:
        raise BatchServiceError(f"vocab {old_vocab_id} is not assigned to batch {batch_n}")

    # The word just removed is now "available" (or "excluded") itself --
    # exclude its own id so it can never be picked as its own replacement.
    replacement = _best_replacement(db, batch_n, exclude_ids={old_vocab_id})
    if replacement is not None:
        _assign_word(db, batch_n, replacement.vocab_id)
    db.commit()
    return replacement


def replace_words(
    db: Session, batch_n: int, vocab_ids: list[int], exclude: bool = False
) -> list[tuple[int, ReplacementCandidate | None]]:
    """Bulk replace_word: removes each id (optionally excluding), picking a
    distinct auto-selected replacement for each -- a replacement already
    picked earlier in this same call is never picked again for a later one.
    Returns (removed_vocab_id, replacement_or_none) pairs in input order.
    """
    _require_draft_batch(db, batch_n)
    # Every id in this batch of removals becomes "available" (or "excluded")
    # as it's processed, and must never be picked as a replacement for one
    # of the others -- excluded from the very first pick, not just once its
    # own turn has been processed.
    never_pick: set[int] = set(vocab_ids)
    results: list[tuple[int, ReplacementCandidate | None]] = []
    for vocab_id in vocab_ids:
        if _take_out_of_batch(db, batch_n, vocab_id, exclude) is None:
            continue
        replacement = _best_replacement(db, batch_n, exclude_ids=never_pick)
        if replacement is not None:
            _assign_word(db, batch_n, replacement.vocab_id)
            never_pick.add(replacement.vocab_id)
        results.append((vocab_id, replacement))
    db.commit()
    return results


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
    coverage = _load_coverage(db)
    target_kanji = _load_target_kanji(schedule, coverage, batch_n)

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
