import pytest

from app.enrichment.jisho_client import JishoWordResult, JishoWordSense
from app.models.vocab import Vocab
from app.services import vocab_service


def _vocab(**kwargs) -> Vocab:
    defaults = dict(kanji_form="x", hiragana_form="x", meaning="", status="available")
    defaults.update(kwargs)
    return Vocab(**defaults)


def test_list_vocab_paginates_all_rows_by_default(db_session):
    db_session.add_all([_vocab(kanji_form=str(i), hiragana_form=str(i)) for i in range(5)])
    db_session.commit()

    result = vocab_service.list_vocab(db_session, limit=2, offset=1)

    assert result.total == 5
    assert len(result.items) == 2


def test_list_vocab_kana_only_filters_out_rows_with_real_kanji(db_session):
    db_session.add_all(
        [
            _vocab(kanji_form="そう", hiragana_form="そう", meaning="monk"),
            _vocab(kanji_form="旅行", hiragana_form="りょこう", meaning="travel"),
        ]
    )
    db_session.commit()

    result = vocab_service.list_vocab(db_session, kana_only=True)

    assert result.total == 1
    assert result.items[0].hiragana_form == "そう"


def test_list_vocab_search_matches_meaning_or_reading(db_session):
    db_session.add_all(
        [
            _vocab(kanji_form="旅行", hiragana_form="りょこう", meaning="travel, trip"),
            _vocab(kanji_form="時間", hiragana_form="じかん", meaning="time"),
        ]
    )
    db_session.commit()

    result = vocab_service.list_vocab(db_session, search="travel")

    assert result.total == 1
    assert result.items[0].kanji_form == "旅行"


def test_update_vocab_sets_kanji_form_and_usually_kana(db_session):
    row = _vocab(kanji_form="そう", hiragana_form="そう", meaning="monk")
    db_session.add(row)
    db_session.commit()

    updated = vocab_service.update_vocab(db_session, row.id, kanji_form="僧", usually_kana=False)

    assert updated.kanji_form == "僧"
    assert updated.usually_kana is False


def test_update_vocab_raises_on_natural_key_collision(db_session):
    existing = _vocab(kanji_form="僧", hiragana_form="そう", meaning="monk")
    editable = _vocab(kanji_form="そう", hiragana_form="そう", meaning="monk")
    db_session.add_all([existing, editable])
    db_session.commit()

    with pytest.raises(vocab_service.VocabServiceError):
        vocab_service.update_vocab(db_session, editable.id, kanji_form="僧")


def test_update_vocab_raises_for_missing_row(db_session):
    with pytest.raises(vocab_service.VocabServiceError):
        vocab_service.update_vocab(db_session, 999999, kanji_form="僧")


def test_update_vocab_sets_hiragana_form(db_session):
    row = _vocab(kanji_form="から", hiragana_form="からー", meaning="shell")  # typo'd reading
    db_session.add(row)
    db_session.commit()

    updated = vocab_service.update_vocab(db_session, row.id, hiragana_form="から")

    assert updated.hiragana_form == "から"


def test_delete_vocab_removes_the_row(db_session):
    row = _vocab(kanji_form="そう", hiragana_form="そう", meaning="monk")
    db_session.add(row)
    db_session.commit()
    row_id = row.id

    vocab_service.delete_vocab(db_session, row_id)

    assert db_session.get(Vocab, row_id) is None


def test_delete_vocab_raises_for_missing_row(db_session):
    with pytest.raises(vocab_service.VocabServiceError):
        vocab_service.delete_vocab(db_session, 999999)


def test_delete_vocab_resolves_a_collision_left_by_a_failed_edit(db_session):
    # The exact scenario this exists for: editing a kana-only row's
    # kanji_form to match one Jisho already resolved on another row hits
    # the natural-key collision and update_vocab refuses -- deleting the
    # row being edited (not the one it collided with) is the fix.
    already_resolved = _vocab(kanji_form="僧", hiragana_form="そう", meaning="monk")
    being_edited = _vocab(kanji_form="そう", hiragana_form="そう", meaning="monk")
    db_session.add_all([already_resolved, being_edited])
    db_session.commit()

    with pytest.raises(vocab_service.VocabServiceError):
        vocab_service.update_vocab(db_session, being_edited.id, kanji_form="僧")

    vocab_service.delete_vocab(db_session, being_edited.id)

    assert db_session.get(Vocab, being_edited.id) is None
    assert db_session.get(Vocab, already_resolved.id) is not None


def test_mark_kana_only_confirmed_excludes_row_from_default_review_queue(db_session):
    row = _vocab(kanji_form="とても", hiragana_form="とても", meaning="very", source="jlpt_n3_vocabulary.xls")
    db_session.add(row)
    db_session.commit()

    assert vocab_service.list_vocab(db_session, kana_only=True).total == 1

    vocab_service.mark_kana_only_confirmed(db_session, row.id)

    assert vocab_service.list_vocab(db_session, kana_only=True).total == 0
    included = vocab_service.list_vocab(db_session, kana_only=True, include_reviewed=True)
    assert included.total == 1
    assert "reviewed_kana_only" in included.items[0].source


def test_mark_kana_only_confirmed_is_idempotent(db_session):
    row = _vocab(kanji_form="とても", hiragana_form="とても", meaning="very", source="jlpt_n3_vocabulary.xls")
    db_session.add(row)
    db_session.commit()

    vocab_service.mark_kana_only_confirmed(db_session, row.id)
    vocab_service.mark_kana_only_confirmed(db_session, row.id)

    assert row.source.count("reviewed_kana_only") == 1


def test_mark_kana_only_confirmed_raises_for_missing_row(db_session):
    with pytest.raises(vocab_service.VocabServiceError):
        vocab_service.mark_kana_only_confirmed(db_session, 999999)


class _FakeClient:
    def __init__(self, results: list[JishoWordResult]):
        self._results = results
        self.calls: list[str] = []

    async def search_words(self, keyword: str) -> list[JishoWordResult]:
        self.calls.append(keyword)
        return self._results


@pytest.mark.asyncio
async def test_get_kanji_candidates_ranks_by_meaning_overlap(db_session):
    row = _vocab(kanji_form="から", hiragana_form="から", meaning="shell")
    db_session.add(row)
    db_session.commit()

    client = _FakeClient(
        [
            JishoWordResult(word="殻", reading="から", is_common=True, jlpt=[], senses=[JishoWordSense(["shell"], ["Noun"])]),
            JishoWordResult(word="空", reading="から", is_common=True, jlpt=[], senses=[JishoWordSense(["empty"], ["Noun"])]),
        ]
    )

    candidates = await vocab_service.get_kanji_candidates(db_session, row.id, client)

    assert [c.word for c in candidates] == ["殻", "空"]


@pytest.mark.asyncio
async def test_get_kanji_candidates_raises_for_missing_row(db_session):
    with pytest.raises(vocab_service.VocabServiceError):
        await vocab_service.get_kanji_candidates(db_session, 999999, _FakeClient([]))


@pytest.mark.asyncio
async def test_get_kanji_candidates_reading_override_searches_the_override(db_session):
    # A typo'd or alternate reading (e.g. one half of アイデア/アイディア) should
    # be searchable without first saving it to the row.
    row = _vocab(kanji_form="からー", hiragana_form="からー", meaning="shell")
    db_session.add(row)
    db_session.commit()

    client = _FakeClient(
        [JishoWordResult(word="殻", reading="から", is_common=True, jlpt=[], senses=[JishoWordSense(["shell"], ["Noun"])])]
    )

    candidates = await vocab_service.get_kanji_candidates(db_session, row.id, client, reading="から")

    assert client.calls == ["から"]  # searched the override, not the stored (typo'd) hiragana_form
    assert [c.word for c in candidates] == ["殻"]
