"""Pure diff/match logic between an old export and a new (corrected) export.

No Anki, no file I/O -- takes two lists of parser.Row and returns a list of
MatchResult, each tagged with the action a caller should offer the user:

- "unchanged": identical front and back, nothing to do.
- "update": an existing card's front and/or back should be rewritten.
- "delete": an old card has no counterpart in the new file.
- "add": a new-file entry has no counterpart in the old file (brand-new word).

Matching proceeds in confidence order: exact (front, back) -> exact front with
changed back -> "reading" match (front changed, e.g. furigana/kanji added to a
previously kana-only word: から -> 殻（から）) -> fuzzy back-text similarity as
a last resort. Anything below "exact front" confidence still carries its
runner-up candidates so a caller-side UI can offer the user a picker instead
of silently guessing.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Literal

from anki_patch_tool.parser import Row

_PAREN_INNER_RE = re.compile(r"[（(]([^）)]+)[）)]\s*$")

FUZZY_THRESHOLD = 0.5
MAX_CANDIDATES = 3

Action = Literal["unchanged", "update", "delete", "add"]


@dataclass
class MatchResult:
    action: Action
    old: Row | None
    new: Row | None
    confidence: float
    reason: str
    # Runner-up new rows the user could pick instead, when the match wasn't a
    # clean exact-front hit. Empty for "unchanged"/high-confidence "update".
    candidates: list[Row] = field(default_factory=list)


def _reading(front: str) -> str:
    """Returns the text inside a trailing parenthetical, or the whole string
    if there isn't one -- e.g. "殻（から）" -> "から", "から" -> "から".
    """
    m = _PAREN_INNER_RE.search(front)
    return m.group(1).strip() if m else front


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def diff_rows(old_rows: list[Row], new_rows: list[Row]) -> list[MatchResult]:
    results: list[MatchResult] = []

    unclaimed_new: list[int] = list(range(len(new_rows)))
    new_by_front: dict[str, list[int]] = {}
    for i in unclaimed_new:
        new_by_front.setdefault(new_rows[i].front, []).append(i)

    unmatched_old_idx: list[int] = []

    # Pass 1: exact front match (prefer an exact back match among duplicates).
    for oi, old in enumerate(old_rows):
        candidates = [i for i in new_by_front.get(old.front, []) if i in unclaimed_new]
        if not candidates:
            unmatched_old_idx.append(oi)
            continue
        exact_back = [i for i in candidates if new_rows[i].back == old.back]
        if exact_back:
            ni = exact_back[0]
            unclaimed_new.remove(ni)
            results.append(MatchResult("unchanged", old, new_rows[ni], 1.0, "identical front and back"))
        elif len(candidates) == 1:
            ni = candidates[0]
            unclaimed_new.remove(ni)
            results.append(MatchResult("update", old, new_rows[ni], 1.0, "same front, meaning text changed"))
        else:
            unmatched_old_idx.append(oi)

    # Pass 2: reading match -- front text changed but the kana reading matches.
    still_unmatched: list[int] = []
    for oi in unmatched_old_idx:
        old = old_rows[oi]
        old_reading = _reading(old.front)
        found_ni: int | None = None
        for ni in unclaimed_new:
            new = new_rows[ni]
            if old_reading == _reading(new.front) or old.front == _reading(new.front) or _reading(old.front) == new.front:
                found_ni = ni
                break
        if found_ni is not None:
            unclaimed_new.remove(found_ni)
            new = new_rows[found_ni]
            results.append(
                MatchResult(
                    "update", old, new, 0.9,
                    f"front text changed ({old.front!r} -> {new.front!r}), matched by reading",
                )
            )
        else:
            still_unmatched.append(oi)

    # Pass 3: fuzzy fallback, ranked by similarity of the back (meaning) text.
    for oi in still_unmatched:
        old = old_rows[oi]
        scored = sorted(
            ((_similarity(old.back, new_rows[ni].back), ni) for ni in unclaimed_new),
            key=lambda t: t[0],
            reverse=True,
        )
        top_candidates = [new_rows[ni] for _, ni in scored[:MAX_CANDIDATES]]
        if scored and scored[0][0] >= FUZZY_THRESHOLD:
            top_score, top_ni = scored[0]
            unclaimed_new.remove(top_ni)
            results.append(
                MatchResult(
                    "update", old, new_rows[top_ni], top_score,
                    "no exact front/reading match -- best guess by meaning-text similarity, please confirm",
                    candidates=top_candidates,
                )
            )
        else:
            results.append(
                MatchResult(
                    "delete", old, None, scored[0][0] if scored else 0.0,
                    "no matching entry found in the new file",
                    candidates=top_candidates,
                )
            )

    # Whatever's left in the new file is a brand-new word with no old-file counterpart.
    for ni in unclaimed_new:
        results.append(MatchResult("add", None, new_rows[ni], 1.0, "new word, not present in old file"))

    return results
