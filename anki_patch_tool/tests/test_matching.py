from pathlib import Path

import pytest

from anki_patch_tool.matching import diff_rows
from anki_patch_tool.parser import parse_export_tsv

COMPARISON_DIR = Path(__file__).resolve().parents[2] / "comparation files"


def _load(deck: str) -> tuple[list, list]:
    old = parse_export_tsv(COMPARISON_DIR / f"{deck}_old.tsv")
    new = parse_export_tsv(COMPARISON_DIR / f"{deck}_new.tsv")
    return old, new


@pytest.mark.skipif(not COMPARISON_DIR.exists(), reason="comparation files/ not present")
def test_vocabulary_diff_shapes():
    old, new = _load("Japanese Vocabulary")
    results = diff_rows(old, new)

    by_action = {}
    for r in results:
        by_action.setdefault(r.action, []).append(r)

    # meaning corrected in place, front unchanged
    kome_updates = [r for r in by_action.get("update", []) if r.old and r.old.front == "米（こめ）"]
    assert kome_updates, "expected 米（こめ）meaning correction to be detected as an update"
    assert kome_updates[0].confidence == 1.0
    assert kome_updates[0].new.back != kome_updates[0].old.back

    # front changed (kana-only -> kanji+furigana), matched via reading
    kara_updates = [r for r in by_action.get("update", []) if r.old and r.old.front == "から"]
    assert kara_updates, "expected から -> 殻（から） to be detected as an update via reading match"
    assert kara_updates[0].new.front == "殻（から）"

    # at least one row genuinely removed
    assert by_action.get("delete"), "expected at least one row with no counterpart in the new file"

    # at least one row genuinely brand-new
    assert by_action.get("add"), "expected at least one row only present in the new file"

    # unchanged rows exist too
    assert by_action.get("unchanged"), "expected at least one identical row"


@pytest.mark.skipif(not COMPARISON_DIR.exists(), reason="comparation files/ not present")
@pytest.mark.parametrize(
    "deck", ["Japanese Kanji", "Japanese Verbs", "Japanese Adjectives", "Japanese Adverbs"]
)
def test_all_decks_parse_and_diff_without_error(deck: str):
    old, new = _load(deck)
    assert old and new
    results = diff_rows(old, new)
    assert len(results) >= len(old)  # every old row produces exactly one result
    accounted_old = sum(1 for r in results if r.old is not None)
    assert accounted_old == len(old)
