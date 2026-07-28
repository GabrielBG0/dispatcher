import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base, get_db
from app.main import app
from app.models.vocab import Vocab


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine)

    def _override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app), session_local
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_get_duplicates_returns_empty_when_no_dupes(client):
    test_client, _ = client
    resp = test_client.get("/api/vocab/duplicates")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_then_resolve_duplicates_roundtrip(client):
    test_client, session_local = client
    db = session_local()
    db.add_all(
        [
            Vocab(kanji_form="カバー", hiragana_form="カバー", meaning="cover", status="assigned"),
            Vocab(kanji_form="カバー", hiragana_form="カバー", meaning="1 - cover / covering", status="available"),
        ]
    )
    db.commit()
    db.close()

    list_resp = test_client.get("/api/vocab/duplicates")
    assert list_resp.status_code == 200
    groups = list_resp.json()
    assert len(groups) == 1
    group = groups[0]
    keep_id = group["suggested_keep_id"]
    delete_ids = [r["id"] for r in group["rows"] if r["id"] != keep_id]

    resolve_resp = test_client.post(
        "/api/vocab/duplicates/resolve", json={"keep_id": keep_id, "delete_ids": delete_ids}
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json() == {"kept": keep_id, "deleted": delete_ids}

    db = session_local()
    assert db.get(Vocab, keep_id) is not None
    for did in delete_ids:
        assert db.get(Vocab, did) is None
    db.close()


def test_resolve_duplicates_rejects_unrelated_word(client):
    test_client, session_local = client
    db = session_local()
    keep = Vocab(kanji_form="カバー", hiragana_form="カバー", meaning="cover")
    unrelated = Vocab(kanji_form="時間", hiragana_form="じかん", meaning="time")
    db.add_all([keep, unrelated])
    db.commit()
    keep_id, unrelated_id = keep.id, unrelated.id
    db.close()

    resp = test_client.post(
        "/api/vocab/duplicates/resolve", json={"keep_id": keep_id, "delete_ids": [unrelated_id]}
    )
    assert resp.status_code == 400


def test_list_vocab_kana_only(client):
    test_client, session_local = client
    db = session_local()
    db.add_all(
        [
            Vocab(kanji_form="そう", hiragana_form="そう", meaning="monk"),
            Vocab(kanji_form="旅行", hiragana_form="りょこう", meaning="travel"),
        ]
    )
    db.commit()
    db.close()

    resp = test_client.get("/api/vocab", params={"kana_only": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["hiragana_form"] == "そう"


def test_confirm_kana_only_removes_row_from_default_queue(client):
    test_client, session_local = client
    db = session_local()
    row = Vocab(kanji_form="とても", hiragana_form="とても", meaning="very")
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    confirm_resp = test_client.post(f"/api/vocab/{row_id}/confirm-kana-only")
    assert confirm_resp.status_code == 200

    list_resp = test_client.get("/api/vocab", params={"kana_only": True})
    assert list_resp.json()["total"] == 0

    list_resp = test_client.get("/api/vocab", params={"kana_only": True, "include_reviewed": True})
    assert list_resp.json()["total"] == 1


def test_patch_vocab_updates_kanji_form(client):
    test_client, session_local = client
    db = session_local()
    row = Vocab(kanji_form="そう", hiragana_form="そう", meaning="monk")
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    resp = test_client.patch(f"/api/vocab/{row_id}", json={"kanji_form": "僧"})
    assert resp.status_code == 200
    assert resp.json()["kanji_form"] == "僧"


def test_patch_vocab_404_for_missing_row(client):
    test_client, _ = client
    resp = test_client.patch("/api/vocab/999999", json={"kanji_form": "僧"})
    assert resp.status_code == 404


def test_delete_vocab_removes_the_row(client):
    test_client, session_local = client
    db = session_local()
    row = Vocab(kanji_form="そう", hiragana_form="そう", meaning="monk")
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    resp = test_client.delete(f"/api/vocab/{row_id}")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": row_id}

    db = session_local()
    assert db.get(Vocab, row_id) is None
    db.close()


def test_delete_vocab_404_for_missing_row(client):
    test_client, _ = client
    resp = test_client.delete("/api/vocab/999999")
    assert resp.status_code == 404


SOU_WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "僧",
            "is_common": True,
            "jlpt": [],
            "japanese": [{"word": "僧", "reading": "そう"}],
            "senses": [{"english_definitions": ["monk", "priest"], "parts_of_speech": ["Noun"]}],
        }
    ],
}


def test_get_kanji_candidates_calls_jisho_and_ranks(client):
    test_client, session_local = client
    db = session_local()
    row = Vocab(kanji_form="そう", hiragana_form="そう", meaning="monk")
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=SOU_WORDS_RESPONSE)
        )
        resp = test_client.get(f"/api/vocab/{row_id}/kanji-candidates")

    assert resp.status_code == 200
    candidates = resp.json()["candidates"]
    assert candidates[0]["word"] == "僧"
    assert candidates[0]["score"] == 0.5  # {"monk"} overlap over {"monk", "priest"} union
    assert candidates[0]["meaning"] == "monk / priest"  # same numbered format as the enrichment job


def test_get_kanji_candidates_uses_reading_override(client):
    test_client, session_local = client
    db = session_local()
    row = Vocab(kanji_form="そうー", hiragana_form="そうー", meaning="monk")  # typo'd reading
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=SOU_WORDS_RESPONSE)
        )
        resp = test_client.get(f"/api/vocab/{row_id}/kanji-candidates", params={"reading": "そう"})

    assert resp.status_code == 200
    assert route.calls.last.request.url.params["keyword"] == "そう"
    assert resp.json()["candidates"][0]["word"] == "僧"


def test_patch_vocab_updates_hiragana_form(client):
    test_client, session_local = client
    db = session_local()
    row = Vocab(kanji_form="そうー", hiragana_form="そうー", meaning="monk")
    db.add(row)
    db.commit()
    row_id = row.id
    db.close()

    resp = test_client.patch(f"/api/vocab/{row_id}", json={"hiragana_form": "そう"})
    assert resp.status_code == 200
    assert resp.json()["hiragana_form"] == "そう"
