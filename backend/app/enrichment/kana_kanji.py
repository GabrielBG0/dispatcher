"""Matching logic for finding the kanji spelling of a vocab row whose
`kanji_form` is currently kana-only, given Jisho word-search results for its
reading. Shared by the "kana word -> kanji form" enrichment job
(enrichment/jobs.py) and the manual backfill CLI (scripts/backfill_vocab_kanji_form.py)
so the matching rules only exist in one place.

Matching a row's stored `meaning` against Jisho's senses (via English
definition overlap) is what picks the right homophone -- e.g. 殻 "shell" vs
空 "empty" vs the から "from" particle -- for a given reading. Ambiguous
cases (no confident match, or more than one candidate kanji reaches the
match threshold) come back as a non-MATCHED outcome rather than a guess,
per the principle that a wrong kanji is worse than a missing one in a study
tool.
"""

import re
from dataclasses import dataclass, field
from enum import Enum

from app.enrichment.jisho_client import JishoWordResult, JishoWordSense
from app.kanji_utils import extract_kanji

_LEADING_TAG_RE = re.compile(r"^\([^)]*\)\s*")
_KANA_TAG_RE = re.compile(r"usually.*kana", re.IGNORECASE)

MATCH_THRESHOLD = 0.6


class KanaKanjiOutcome(Enum):
    MATCHED = "matched"
    NO_KANJI_CANDIDATE = "no_kanji_candidate"  # Jisho has no kanji-bearing entry for this reading
    NO_STORED_MEANING = "no_stored_meaning"  # nothing to disambiguate homophones with
    NO_CONFIDENT_MATCH = "no_confident_match"  # every candidate scored below MATCH_THRESHOLD
    AMBIGUOUS = "ambiguous"  # more than one distinct kanji spelling cleared the threshold


@dataclass
class KanaKanjiResult:
    outcome: KanaKanjiOutcome
    kanji_form: str | None = None
    usually_kana: bool = False
    candidate_words: list[str] = field(default_factory=list)


def normalize_meaning(text: str) -> set[str]:
    text = _LEADING_TAG_RE.sub("", text)
    text = text.replace(".", ",")
    text = re.sub(r"\d+\s*-\s*", "", text)
    return {p.strip().lower() for p in re.split(r"[,/]", text) if p.strip()}


def is_usually_kana(sense: JishoWordSense) -> bool:
    return any(_KANA_TAG_RE.search(tag) for tag in sense.tags)


def best_matching_sense(row_meaning_tokens: set[str], result: JishoWordResult) -> tuple[float, bool]:
    """Best (Jaccard overlap, is_usually_kana) across this entry's senses.
    is_usually_kana reflects the specific sense that produced the best
    score, since the tag can sit on one sense of a multi-sense entry.

    Jaccard (intersection / union) rather than intersection / candidate-size:
    a candidate sense phrased as a single generic word (e.g. just
    "together") would otherwise score a perfect 1.0 overlap-of-candidate
    ratio against any row meaning containing that word, out-scoring the
    real, richer-worded match -- that's exactly how いっしょ nearly matched
    to 一所 (an obscure sense literally just "together") over 一緒 (the
    actual common word, whose matching sense has other words alongside).
    Jaccard penalizes that by also counting what's in the row meaning but
    NOT in the candidate, so a thin one-word sense can't win purely on
    having a small denominator."""
    best_score = 0.0
    best_is_kana = False
    for sense in result.senses:
        cand_tokens = {d.strip().lower() for d in sense.english_definitions if d.strip()}
        if not cand_tokens:
            continue
        union = row_meaning_tokens | cand_tokens
        if not union:
            continue
        overlap = row_meaning_tokens & cand_tokens
        score = len(overlap) / len(union)
        if score > best_score:
            best_score = score
            best_is_kana = is_usually_kana(sense)
    return best_score, best_is_kana


_SENSE_ONE_MARKER_RE = re.compile(r"1\s*-")
_SENSE_TWO_MARKER_RE = re.compile(r"2\s*-")


def is_meaning_already_standardized(meaning: str) -> bool:
    """True when `meaning` already looks like format_meaning_groups' own
    output shape -- either numbered senses ("1 - ... 2 - ...") or a single
    sense's definitions slash-joined with 2+ slashes. A lone "/" isn't
    trusted on its own since non-Jisho meaning text (e.g. the xls import's
    "(noun) foo, bar" style) can contain one incidentally; 2+ slashes is a
    much stronger signal the row already went through the formatter."""
    if not meaning:
        return False
    if _SENSE_ONE_MARKER_RE.search(meaning) and _SENSE_TWO_MARKER_RE.search(meaning):
        return True
    return meaning.count("/") >= 2


def format_meaning_groups(sense_definitions: list[list[str]], limit: int = 2) -> str:
    """Same "1 - .../ 2 - ..." formatting run_vocab_word_enrichment writes
    into Vocab.meaning (see enrichment/jobs.py:format_meaning) -- kept here
    as the shared implementation, operating on plain definition lists
    rather than JishoWordSense objects, since rank_candidates already
    collapses senses to that shape. Top `limit` senses, each sense's
    definitions slash-joined; a single surviving sense is rendered plain,
    two or more get numbered prefixes.
    """
    groups = [" / ".join(defs) for defs in sense_definitions[:limit] if defs]
    if len(groups) <= 1:
        return groups[0] if groups else ""
    return ". ".join(f"{i} - {group}" for i, group in enumerate(groups, start=1))


@dataclass
class KanjiCandidate:
    word: str
    definitions: list[str]
    meaning: str
    score: float
    usually_kana: bool


def rank_candidates(reading: str, meaning: str, results: list[JishoWordResult]) -> list[KanjiCandidate]:
    """All kanji-bearing candidates for `reading`, ranked by how well they
    match `meaning` (best first). Unlike find_kanji_form, this never
    collapses to a single answer or refuses to decide -- it's for showing a
    human reviewer their options on a row find_kanji_form couldn't resolve
    on its own (ambiguous, no confident match, or no stored meaning at
    all), so every kanji-bearing candidate comes back even at score 0.
    """
    candidates = [r for r in results if r.reading == reading and r.word and extract_kanji(r.word)]
    row_tokens = normalize_meaning(meaning) if meaning else set()

    best_by_word: dict[str, KanjiCandidate] = {}
    for c in candidates:
        score, kana = best_matching_sense(row_tokens, c) if row_tokens else (0.0, is_usually_kana_entry(c))
        definitions = [d for s in c.senses for d in s.english_definitions]
        formatted_meaning = format_meaning_groups([s.english_definitions for s in c.senses])
        existing = best_by_word.get(c.word)
        if existing is None or score > existing.score:
            best_by_word[c.word] = KanjiCandidate(
                word=c.word, definitions=definitions, meaning=formatted_meaning, score=score, usually_kana=kana
            )

    return sorted(best_by_word.values(), key=lambda c: -c.score)


def is_usually_kana_entry(result: JishoWordResult) -> bool:
    return any(is_usually_kana(sense) for sense in result.senses)


def find_kanji_form(reading: str, meaning: str, results: list[JishoWordResult]) -> KanaKanjiResult:
    candidates = [r for r in results if r.reading == reading and r.word and extract_kanji(r.word)]
    if not candidates:
        return KanaKanjiResult(outcome=KanaKanjiOutcome.NO_KANJI_CANDIDATE)

    row_tokens = normalize_meaning(meaning) if meaning else set()
    if not row_tokens:
        return KanaKanjiResult(outcome=KanaKanjiOutcome.NO_STORED_MEANING)

    scored = [(c, *best_matching_sense(row_tokens, c)) for c in candidates]
    passing = [(c, ratio, kana) for c, ratio, kana in scored if ratio >= MATCH_THRESHOLD]
    if not passing:
        return KanaKanjiResult(outcome=KanaKanjiOutcome.NO_CONFIDENT_MATCH)

    distinct_words = {c.word for c, _, _ in passing}
    if len(distinct_words) > 1:
        return KanaKanjiResult(
            outcome=KanaKanjiOutcome.AMBIGUOUS, candidate_words=sorted(distinct_words)
        )

    match, _, kana = passing[0]
    return KanaKanjiResult(outcome=KanaKanjiOutcome.MATCHED, kanji_form=match.word, usually_kana=kana)
