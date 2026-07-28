"""Finds and resolves duplicate vocab rows: rows that share the exact same
`kanji_form` + `hiragana_form` spelling, most often left behind by two
overlapping vocab-list imports (the repo's real DB has ~3300 rows from
`jlpt_n3_vocabulary.xls` plus ~1100 more from an untracked `jlpt_n3_vocab.csv`
import, many of them re-describing the same word in different wording).

This is deliberately narrower than "same reading" -- Japanese has enormous
numbers of genuine homophones with different kanji (かし = 貸し "loan" vs
菓子 "pastry", both still spelled かし while kana-only), and grouping by
reading alone pulls in hundreds of unrelated words. Same *spelling* is a much
stronger duplicate signal, and even then two rows can legitimately share a
spelling with unrelated senses split across cards on purpose, so a group is
only flagged when every pair of rows in it has meaning-text overlap at or
above MATCH_THRESHOLD -- otherwise at least one row is a distinct sense and
the whole group is left alone rather than guessing which subset to merge.

Resolution never merges field data between rows -- it only deletes the
loser(s) and keeps the winner exactly as it already is. A row already
`assigned`/`seen_in_class` may already be baked into an exported Anki deck,
so silently rewriting its `meaning` from a "duplicate" would desync it from
what the user already has.
"""

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.vocab import Vocab

_PAREN_RE = re.compile(r"\([^)]*\)")
_WORD_RE = re.compile(r"[a-z]+")
_SENSE_NUMBER_RE = re.compile(r"\d+\s*-\s*")

# Function words and Jisho/xls part-of-speech tags that show up inside
# meaning text itself (e.g. "(noun) aunt", "(transitive) to serve sake") --
# stripped before comparison so two rows differing only in a POS tag or a
# parenthetical aside still score as similar.
_STOPWORDS = {
    "a", "an", "the", "to", "of", "in", "on", "at", "for", "and", "or",
    "i", "e", "g", "etc", "noun", "nouns", "verb", "adverb", "adjective",
    "godan", "ichidan", "transitive", "intransitive", "pronoun", "prefix", "suffix",
}

MATCH_THRESHOLD = 0.5


def _meaning_words(meaning: str) -> set[str]:
    text = _PAREN_RE.sub(" ", meaning or "")
    text = _SENSE_NUMBER_RE.sub(" ", text)
    return set(_WORD_RE.findall(text.lower())) - _STOPWORDS


def _meaning_similarity(a: str, b: str) -> float:
    words_a, words_b = _meaning_words(a), _meaning_words(b)
    union = words_a | words_b
    if not union:
        return 0.0
    return len(words_a & words_b) / len(union)


@dataclass
class DuplicateRow:
    id: int
    meaning: str
    status: str
    assigned_batch: int | None
    source: str


@dataclass
class DuplicateGroup:
    kanji_form: str
    hiragana_form: str
    rows: list[DuplicateRow]
    similarity: float  # worst-case pairwise similarity across the group
    suggested_keep_id: int
    auto_resolvable: bool
    reason: str


class DedupeServiceError(Exception):
    pass


def find_duplicate_groups(db: Session) -> list[DuplicateGroup]:
    grouped: dict[tuple[str, str], list[Vocab]] = {}
    for row in db.query(Vocab).all():
        grouped.setdefault((row.kanji_form, row.hiragana_form), []).append(row)

    groups: list[DuplicateGroup] = []
    for (kanji_form, hiragana_form), rows in grouped.items():
        if len(rows) < 2:
            continue

        pairwise = [
            _meaning_similarity(rows[i].meaning, rows[j].meaning)
            for i in range(len(rows))
            for j in range(i + 1, len(rows))
        ]
        min_similarity = min(pairwise)
        if min_similarity < MATCH_THRESHOLD:
            continue  # at least one pair looks like a genuinely distinct sense

        non_available = [r for r in rows if r.status != "available"]
        if len(non_available) == 1:
            keep = non_available[0]
            auto_resolvable = True
            reason = "one row is already in use (assigned/seen) -- keeping it"
        elif len(non_available) == 0:
            # Nothing downstream depends on either row yet -- keep whichever
            # has the fuller meaning text (favors an already jisho-enriched
            # row over a blank or terse one), tie-broken by lowest id.
            keep = max(rows, key=lambda r: (len(r.meaning or ""), -r.id))
            auto_resolvable = True
            reason = "no row is in use yet -- keeping the one with the fuller meaning"
        else:
            keep = min(non_available, key=lambda r: r.id)
            auto_resolvable = False
            reason = (
                f"{len(non_available)} rows are already assigned/seen -- "
                "may already be in different exported decks, needs manual review"
            )

        groups.append(
            DuplicateGroup(
                kanji_form=kanji_form,
                hiragana_form=hiragana_form,
                rows=[
                    DuplicateRow(
                        id=r.id, meaning=r.meaning, status=r.status,
                        assigned_batch=r.assigned_batch, source=r.source,
                    )
                    for r in sorted(rows, key=lambda r: r.id)
                ],
                similarity=min_similarity,
                suggested_keep_id=keep.id,
                auto_resolvable=auto_resolvable,
                reason=reason,
            )
        )

    groups.sort(key=lambda g: (g.auto_resolvable, g.kanji_form))
    return groups


def resolve_duplicate_group(db: Session, keep_id: int, delete_ids: list[int]) -> None:
    """Deletes delete_ids outright and leaves keep_id untouched -- no field
    merging (see module docstring). Requires every id to share the exact
    same (kanji_form, hiragana_form) as keep_id, so a caller can never
    accidentally delete an unrelated word by passing the wrong id.
    """
    if not delete_ids:
        raise DedupeServiceError("delete_ids must not be empty")
    if keep_id in delete_ids:
        raise DedupeServiceError("keep_id cannot also be in delete_ids")

    keep_row = db.get(Vocab, keep_id)
    if keep_row is None:
        raise DedupeServiceError(f"vocab {keep_id} not found")

    delete_rows = db.query(Vocab).filter(Vocab.id.in_(delete_ids)).all()
    missing = set(delete_ids) - {r.id for r in delete_rows}
    if missing:
        raise DedupeServiceError(f"vocab ids not found: {sorted(missing)}")

    mismatched = [
        r.id for r in delete_rows
        if (r.kanji_form, r.hiragana_form) != (keep_row.kanji_form, keep_row.hiragana_form)
    ]
    if mismatched:
        raise DedupeServiceError(
            f"vocab ids {mismatched} don't share a spelling with {keep_id} -- refusing to merge unrelated words"
        )

    for row in delete_rows:
        db.delete(row)
    db.commit()
