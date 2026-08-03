from anki_patch_tool.ankiconnect import AnkiNote
from anki_patch_tool.app import ReviewItem, resolve_against_anki
from anki_patch_tool.matching import MatchResult
from anki_patch_tool.parser import Row

TAGS = "jlpt::n3 source::n3_supplement"


def _note(note_id: int, front: str, back: str, deck: str = "Japanese Kanji") -> AnkiNote:
    return AnkiNote(
        note_id=note_id,
        model_name="Basic",
        fields={"Front": front, "Back": back},
        deck_names=[deck],
        card_ids=[note_id * 1000],
    )


def test_second_reading_of_a_polysemous_kanji_is_added_not_clobbered():
    # 度 has both たび (existing note, unchanged in the file diff) and ど (new
    # to the file). Both share a front -- the たび note must be excluded once
    # claimed by the "unchanged" match, so ど resolves to a genuine add.
    existing = _note(1, "度", "たび")
    matches = [
        MatchResult("unchanged", Row("度", "たび", TAGS), Row("度", "たび", TAGS), 1.0, "identical front and back"),
        MatchResult("add", None, Row("度", "ど", TAGS), 1.0, "new word, not present in old file"),
    ]

    items = resolve_against_anki(matches, [existing], deck="Japanese Kanji")

    assert len(items) == 1
    item = items[0]
    assert item.action == "add"
    assert item.note is None
    assert item.accepted is True
    assert item.chosen_new.back == "ど"


def test_add_candidate_matching_an_existing_note_exactly_needs_no_action():
    existing = _note(1, "度", "たび")
    matches = [MatchResult("add", None, Row("度", "たび", TAGS), 1.0, "new word, not present in old file")]

    items = resolve_against_anki(matches, [existing], deck="Japanese Kanji")

    assert len(items) == 1
    assert items[0].note_status == "already_exists"
    assert items[0].accepted is False
    assert items[0].can_apply() is False


def test_add_candidate_converts_to_update_when_front_matches_a_different_back():
    existing = _note(1, "度", "ど")
    matches = [MatchResult("add", None, Row("度", "たび", TAGS), 1.0, "new word, not present in old file")]

    items = resolve_against_anki(matches, [existing], deck="Japanese Kanji")

    assert len(items) == 1
    assert items[0].action == "update"
    assert items[0].note is existing
    assert items[0].accepted is True


def test_update_match_prefers_exact_pair_over_front_only_candidates():
    # Two existing notes share a front (度: たび and ど) -- an update for the
    # たび row must resolve to the たび note specifically, not get confused by
    # the co-existing ど note.
    taby_note = _note(1, "度", "たび")
    do_note = _note(2, "度", "ど")
    matches = [
        MatchResult("update", Row("度", "たび", TAGS), Row("度", "たびたび", TAGS), 1.0, "same front, meaning text changed"),
    ]

    items = resolve_against_anki(matches, [taby_note, do_note], deck="Japanese Kanji")

    assert len(items) == 1
    assert items[0].note is taby_note
    assert items[0].note_status == "found"


def test_needs_attention_only_for_unconfirmed_candidate_matches():
    fuzzy_match = MatchResult(
        "update", Row("殻", "から", TAGS), Row("殻", "から", TAGS), 0.6, "best guess",
        candidates=[Row("殻", "から", TAGS), Row("空", "から", TAGS)],
    )

    # Accepted (checked) by default and unconfirmed -> needs attention.
    item = ReviewItem(match=fuzzy_match, accepted=True, chosen_new=fuzzy_match.new, note=object())
    assert item.needs_attention() is True

    # Unchecked -> the user isn't applying it, nothing to confirm right now.
    unchecked = ReviewItem(match=fuzzy_match, accepted=False, chosen_new=fuzzy_match.new, note=object())
    assert unchecked.needs_attention() is False

    # Already went through ResolveDialog -> no longer pending.
    confirmed = ReviewItem(match=fuzzy_match, accepted=True, chosen_new=fuzzy_match.new, note=object(), resolved=True)
    assert confirmed.needs_attention() is False

    # No runner-up candidates at all (a clean exact match) -> nothing to confirm.
    clean_match = MatchResult("update", Row("度", "たび", TAGS), Row("度", "たび", TAGS), 1.0, "same front, meaning changed")
    clean = ReviewItem(match=clean_match, accepted=True, chosen_new=clean_match.new, note=object())
    assert clean.needs_attention() is False
