import pytest

from app.models.vocab import Vocab
from app.services import dedupe_service


def _vocab(**kwargs) -> Vocab:
    defaults = dict(kanji_form="x", hiragana_form="x", meaning="", status="available")
    defaults.update(kwargs)
    return Vocab(**defaults)


def test_finds_group_with_overlapping_meanings_from_two_imports(db_session):
    # The real-world case: the same katakana word imported twice, once with
    # its own terse meaning and once re-enriched from Jisho with numbered
    # senses -- different wording, same word.
    db_session.add_all(
        [
            _vocab(
                kanji_form="カバー", hiragana_form="カバー",
                meaning="cover, covering, dust jacket, wrapper", status="assigned", assigned_batch=1,
                source="jlpt_n3_vocabulary.xls",
            ),
            _vocab(
                kanji_form="カバー", hiragana_form="カバー",
                meaning="1 - cover / covering / dust jacket / wrapper. 2 - covering (a song)",
                status="available", source="jlpt_n3_vocab.csv,jisho",
            ),
        ]
    )
    db_session.commit()

    groups = dedupe_service.find_duplicate_groups(db_session)

    assert len(groups) == 1
    group = groups[0]
    assert group.kanji_form == "カバー"
    assert len(group.rows) == 2
    # The assigned row is already live in a batch -- it must win over the
    # unused duplicate regardless of import order.
    assert group.auto_resolvable is True
    keep_row = next(r for r in group.rows if r.id == group.suggested_keep_id)
    assert keep_row.status == "assigned"


def test_does_not_group_homophones_with_unrelated_meanings(db_session):
    # かし = 貸し "loan" vs 菓子 "pastry" -- same kana-only spelling in our
    # data, completely different words. Must not be flagged as a duplicate.
    db_session.add_all(
        [
            _vocab(kanji_form="かし", hiragana_form="かし", meaning="loan, lending"),
            _vocab(kanji_form="かし", hiragana_form="かし", meaning="pastry, confectionery"),
        ]
    )
    db_session.commit()

    groups = dedupe_service.find_duplicate_groups(db_session)

    assert groups == []


def test_flags_manual_review_when_multiple_rows_already_in_use(db_session):
    db_session.add_all(
        [
            _vocab(
                kanji_form="あと", hiragana_form="あと",
                meaning="trace, tracks, mark, sign", status="assigned", assigned_batch=8,
            ),
            _vocab(
                kanji_form="あと", hiragana_form="あと",
                meaning="1 - trace / tracks / mark / sign", status="assigned", assigned_batch=1,
            ),
        ]
    )
    db_session.commit()

    groups = dedupe_service.find_duplicate_groups(db_session)

    assert len(groups) == 1
    assert groups[0].auto_resolvable is False


def test_single_row_spelling_is_not_a_duplicate(db_session):
    db_session.add(_vocab(kanji_form="単", hiragana_form="たん", meaning="simple"))
    db_session.commit()

    assert dedupe_service.find_duplicate_groups(db_session) == []


def test_resolve_deletes_losers_and_keeps_winner_untouched(db_session):
    keep = _vocab(kanji_form="カバー", hiragana_form="カバー", meaning="cover", status="assigned")
    lose = _vocab(kanji_form="カバー", hiragana_form="カバー", meaning="1 - cover / covering")
    db_session.add_all([keep, lose])
    db_session.commit()
    keep_id, lose_id = keep.id, lose.id

    dedupe_service.resolve_duplicate_group(db_session, keep_id=keep_id, delete_ids=[lose_id])

    assert db_session.get(Vocab, lose_id) is None
    survivor = db_session.get(Vocab, keep_id)
    assert survivor.meaning == "cover"  # untouched, not merged with the deleted row


def test_resolve_refuses_to_delete_a_different_word(db_session):
    keep = _vocab(kanji_form="カバー", hiragana_form="カバー", meaning="cover")
    unrelated = _vocab(kanji_form="時間", hiragana_form="じかん", meaning="time")
    db_session.add_all([keep, unrelated])
    db_session.commit()

    with pytest.raises(dedupe_service.DedupeServiceError):
        dedupe_service.resolve_duplicate_group(db_session, keep_id=keep.id, delete_ids=[unrelated.id])

    assert db_session.get(Vocab, unrelated.id) is not None


def test_resolve_rejects_empty_delete_list(db_session):
    keep = _vocab(kanji_form="カバー", hiragana_form="カバー", meaning="cover")
    db_session.add(keep)
    db_session.commit()

    with pytest.raises(dedupe_service.DedupeServiceError):
        dedupe_service.resolve_duplicate_group(db_session, keep_id=keep.id, delete_ids=[])
