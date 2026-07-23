import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tests.conftest import FIXTURES_DIR

from app import models  # noqa: F401
from app.db import Base
from app.enrichment import jobs
from app.enrichment.jisho_client import JishoClient
from app.models.kanji import Kanji
from app.models.vocab import Vocab

KANJIVG_FIXTURE = FIXTURES_DIR / "kanjivg_sample.xml.gz"


@pytest.fixture()
def session_factory():
    # A single shared in-memory connection (StaticPool) so multiple
    # session_factory() calls within one job see the same DB -- jobs open
    # their own session per call, same as the real SessionLocal pattern.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "旅行",
            "is_common": True,
            "jlpt": ["jlpt-n4"],
            "japanese": [{"word": "旅行", "reading": "りょこう"}],
            "senses": [
                {"english_definitions": ["travel", "trip"], "parts_of_speech": ["Noun", "Suru verb"]}
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_run_vocab_word_enrichment_fills_blank_meanings(session_factory):
    db = session_factory()
    db.add(Vocab(kanji_form="旅行", hiragana_form="りょこう", meaning="", part_of_speech="general", status="available"))
    db.add(Vocab(kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general", status="available"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_words", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_vocab_word_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["total"] == 1  # only the blank-meaning row needed enrichment
    assert status["completed"] == 1
    assert status["not_found"] == 0

    db = session_factory()
    ryokou = db.query(Vocab).filter(Vocab.kanji_form == "旅行").one()
    assert "travel" in ryokou.meaning
    assert ryokou.part_of_speech == "verb"  # "Suru verb" maps to verb
    assert ryokou.jlpt_level == "jlpt-n4"
    jikan = db.query(Vocab).filter(Vocab.kanji_form == "時間").one()
    assert jikan.meaning == "time"  # untouched, already had a meaning
    db.close()


EMPTY_WORDS_RESPONSE = {"meta": {"status": 200}, "data": []}


@pytest.mark.asyncio
async def test_run_vocab_word_enrichment_tracks_jisho_misses(session_factory):
    # Per the spec, enrichment misses (word not found on Jisho) must surface
    # as an actionable count, not disappear behind a "completed" status.
    db = session_factory()
    db.add(Vocab(kanji_form="謎語", hiragana_form="なぞご", meaning="", part_of_speech="general", status="available"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_words", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=EMPTY_WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_vocab_word_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["completed"] == 1
    assert status["not_found"] == 1

    db = session_factory()
    row = db.query(Vocab).filter(Vocab.kanji_form == "謎語").one()
    assert row.meaning == ""  # left unenriched, not silently marked done
    assert "jisho_not_found" in row.source
    db.close()


KANJI_HTML = """
<div class="kanji-details__main-meanings">love, affection</div>
<div class="kanji-details__main-readings">
  <dl class="dictionary_entry kun_yomi"><dt>Kun:</dt>
    <dd class="kanji-details__main-readings-list" lang="ja"><a href="#">いと.しい</a></dd></dl>
  <dl class="dictionary_entry on_yomi"><dt>On:</dt>
    <dd class="kanji-details__main-readings-list" lang="ja"><a href="#">アイ</a></dd></dl>
</div>
"""


@pytest.mark.asyncio
async def test_run_kanji_meaning_enrichment_fills_missing_kanji(session_factory):
    db = session_factory()
    db.add(Kanji(kanji="愛"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_kanji", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(url__regex=r"https://jisho\.org/search/.*").mock(
            return_value=httpx.Response(200, text=KANJI_HTML)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_kanji_meaning_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["not_found"] == 0

    db = session_factory()
    ai = db.query(Kanji).filter(Kanji.kanji == "愛").one()
    assert ai.meanings == "love, affection"
    assert ai.kun_yomi == "いと.しい"
    assert ai.on_yomi == "アイ"
    db.close()


@pytest.mark.asyncio
async def test_run_kanji_meaning_enrichment_tracks_jisho_misses(session_factory):
    db = session_factory()
    db.add(Kanji(kanji="込"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_kanji", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(url__regex=r"https://jisho\.org/search/.*").mock(
            return_value=httpx.Response(404)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_kanji_meaning_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["completed"] == 1
    assert status["not_found"] == 1


def test_run_kanjivg_enrichment_fills_stroke_data_from_local_archive(session_factory):
    db = session_factory()
    db.add(Kanji(kanji="愛"))
    db.add(Kanji(kanji="私"))
    db.commit()
    db.close()

    job_id = jobs.create_job("kanjivg", total=0, session_factory=session_factory)
    jobs.run_kanjivg_enrichment(job_id, session_factory=session_factory, archive_path=KANJIVG_FIXTURE)

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["completed"] == 2

    db = session_factory()
    ai = db.query(Kanji).filter(Kanji.kanji == "愛").one()
    assert ai.stroke_data is not None
    import json

    assert len(json.loads(ai.stroke_data)) == 13
    db.close()
