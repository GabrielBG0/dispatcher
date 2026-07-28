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
    assert ryokou.meaning == "travel / trip"  # single sense, no numbering
    assert ryokou.part_of_speech == "verb"  # "Suru verb" maps to verb
    assert ryokou.jlpt_level == "jlpt-n4"
    jikan = db.query(Vocab).filter(Vocab.kanji_form == "時間").one()
    assert jikan.meaning == "time"  # untouched, already had a meaning
    db.close()


def test_pos_from_jisho_does_not_misclassify_adverb_as_verb():
    # Regression: "verb" is a substring of "adverb", and _POS_PRIORITY checks
    # verb first -- a naive `in` check misclassified every adverb-only tag
    # as a verb. Must use word-boundary matching instead.
    assert jobs.pos_from_jisho(["Adverb"]) == "adverb"
    assert jobs.pos_from_jisho(["Ichidan verb"]) == "verb"
    assert jobs.pos_from_jisho(["na-adjective"]) == "adjective"
    assert jobs.pos_from_jisho(["Noun"]) == "general"


WIKIPEDIA_AND_REAL_SENSE_WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "様子",
            "is_common": True,
            "jlpt": ["jlpt-n3"],
            "japanese": [{"word": "様子", "reading": "ようす"}],
            "senses": [
                {"english_definitions": ["Wikipedia blurb, not a real sense"], "parts_of_speech": ["Wikipedia definition"]},
                {"english_definitions": ["state", "condition", "appearance"], "parts_of_speech": ["Noun"]},
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_run_vocab_meaning_standardization_excludes_wikipedia_senses(session_factory):
    db = session_factory()
    db.add(
        Vocab(
            kanji_form="様子", hiragana_form="ようす", meaning="(noun) state, condition",
            part_of_speech="general", status="available", source="jlpt_n3_vocabulary.xls",
        )
    )
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_standardize_meanings", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=WIKIPEDIA_AND_REAL_SENSE_WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_vocab_meaning_standardization(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    db = session_factory()
    yousu = db.query(Vocab).filter(Vocab.kanji_form == "様子").one()
    assert yousu.meaning == "state / condition / appearance"  # the Wikipedia sense never appears
    assert yousu.part_of_speech == "general"
    db.close()


DUPLICATE_WORD_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "浴びる",
            "is_common": True,
            "jlpt": ["jlpt-n3"],
            "japanese": [{"word": "浴びる", "reading": "あびる"}],
            "senses": [
                {
                    "english_definitions": ["to bathe in", "to take (e.g. a shower)"],
                    "parts_of_speech": ["Ichidan verb"],
                },
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_run_vocab_meaning_standardization_survives_duplicate_row_conflict(session_factory):
    # Regression (production crash): two DB rows for the same word -- an
    # unresolved near-duplicate pair dedupe_service left alone because the
    # old meaning text on each looked different enough to pass as a distinct
    # sense -- both get re-fetched here and both resolve to the exact same
    # canonical Jisho text. The second write then collides with the
    # (kanji_form, hiragana_form, meaning) unique constraint; that must not
    # crash the job or take the rest of the run down with it.
    db = session_factory()
    db.add(
        Vocab(
            kanji_form="浴びる", hiragana_form="あびる", meaning="(transitive) to bathe, to shower",
            part_of_speech="general", status="available", source="jlpt_n3_vocabulary.xls",
        )
    )
    db.add(
        Vocab(
            kanji_form="浴びる", hiragana_form="あびる", meaning="to take a shower, to bask in",
            part_of_speech="general", status="available", source="jlpt_n3_vocabulary.xls",
        )
    )
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_standardize_meanings", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=DUPLICATE_WORD_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_vocab_meaning_standardization(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"  # must not crash on the collision
    assert status["total"] == 2
    assert status["completed"] == 2
    assert status["not_found"] == 1  # the row that lost the conflict

    db = session_factory()
    rows = db.query(Vocab).filter(Vocab.kanji_form == "浴びる").order_by(Vocab.id).all()
    standardized = [r for r in rows if r.meaning == "to bathe in / to take (e.g. a shower)"]
    conflicted = [r for r in rows if "jisho_duplicate_conflict" in r.source]
    assert len(standardized) == 1  # the winner got the canonical text
    assert len(conflicted) == 1  # the loser kept its old meaning, flagged for manual dedupe
    db.close()


@pytest.mark.asyncio
async def test_run_vocab_meaning_standardization_overwrites_non_jisho_meanings(session_factory):
    db = session_factory()
    db.add(
        Vocab(
            kanji_form="旅行", hiragana_form="りょこう", meaning="(noun) trip, journey",
            part_of_speech="general", status="available", source="jlpt_n3_vocabulary.xls",
        )
    )
    # Already numbered senses -- looks like a prior Jisho run, must be skipped.
    db.add(
        Vocab(
            kanji_form="掛ける", hiragana_form="かける", meaning="1 - to hang up. 2 - to sit",
            part_of_speech="verb", status="available", source="jisho",
        )
    )
    # Single sense with 2+ slashes -- also already Jisho-shaped, must be skipped.
    db.add(
        Vocab(
            kanji_form="僧", hiragana_form="そう", meaning="monk / priest / clergyman",
            part_of_speech="general", status="available", source="jisho",
        )
    )
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_standardize_meanings", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_vocab_meaning_standardization(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["total"] == 1  # only the non-standardized row needed re-fetching
    assert status["completed"] == 1
    assert status["not_found"] == 0

    db = session_factory()
    ryokou = db.query(Vocab).filter(Vocab.kanji_form == "旅行").one()
    assert ryokou.meaning == "travel / trip"  # overwritten with the standardized form
    assert "jisho_standardized" in ryokou.source
    kakeru = db.query(Vocab).filter(Vocab.kanji_form == "掛ける").one()
    assert kakeru.meaning == "1 - to hang up. 2 - to sit"  # untouched, already standardized
    sou = db.query(Vocab).filter(Vocab.kanji_form == "僧").one()
    assert sou.meaning == "monk / priest / clergyman"  # untouched, already standardized
    db.close()


MULTI_SENSE_WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "掛ける",
            "is_common": True,
            "jlpt": ["jlpt-n3"],
            "japanese": [{"word": "掛ける", "reading": "かける"}],
            "senses": [
                {"english_definitions": ["to hang up"], "parts_of_speech": ["Ichidan verb"]},
                {"english_definitions": ["to sit"], "parts_of_speech": ["Ichidan verb"]},
                {"english_definitions": ["to spend (time/money)"], "parts_of_speech": ["Ichidan verb"]},
                {"english_definitions": ["to make (a call)"], "parts_of_speech": ["Ichidan verb"]},
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_run_vocab_word_enrichment_caps_meaning_at_top_two_senses(session_factory):
    db = session_factory()
    db.add(Vocab(kanji_form="掛ける", hiragana_form="かける", meaning="", part_of_speech="general", status="available"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_words", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=MULTI_SENSE_WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_vocab_word_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    db = session_factory()
    kakeru = db.query(Vocab).filter(Vocab.kanji_form == "掛ける").one()
    assert kakeru.meaning == "1 - to hang up. 2 - to sit"  # only the top 2 of 4 senses, numbered
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


SOU_WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "僧",
            "is_common": True,
            "jlpt": [],
            "japanese": [{"word": "僧", "reading": "そう"}],
            "senses": [{"english_definitions": ["monk", "priest"], "parts_of_speech": ["Noun"]}],
        },
        {
            "slug": "然う",
            "is_common": True,
            "jlpt": [],
            "japanese": [{"word": "然う", "reading": "そう"}],
            "senses": [{"english_definitions": ["in that way", "thus"], "parts_of_speech": ["Adverb"]}],
        },
    ],
}


@pytest.mark.asyncio
async def test_run_kana_kanji_form_enrichment_matches_by_stored_meaning(session_factory):
    # The spec's motivating example: our DB has kanji_form="そう" (kana-only)
    # for a word that's really 僧, distinguishable from other そう homophones
    # by the stored meaning.
    db = session_factory()
    db.add(
        Vocab(
            kanji_form="そう", hiragana_form="そう", meaning="1 - monk / priest",
            part_of_speech="general", status="available",
        )
    )
    db.add(Vocab(kanji_form="時間", hiragana_form="じかん", meaning="time", status="available"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_kana_kanji", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=SOU_WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_kana_kanji_form_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["total"] == 1  # 時間 already has kanji, excluded from the kana-only set
    assert status["completed"] == 1
    assert status["not_found"] == 0

    db = session_factory()
    sou = db.query(Vocab).filter(Vocab.hiragana_form == "そう").one()
    assert sou.kanji_form == "僧"
    assert sou.usually_kana is False
    assert "jisho" in sou.source
    db.close()


ARU_WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "有る",
            "is_common": True,
            "jlpt": [],
            "japanese": [{"word": "有る", "reading": "ある"}],
            "senses": [
                {
                    "english_definitions": ["to exist", "to be"],
                    "parts_of_speech": ["Verb"],
                    "tags": ["Usually written using kana alone"],
                }
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_run_kana_kanji_form_enrichment_flags_usually_kana(session_factory):
    db = session_factory()
    db.add(Vocab(kanji_form="ある", hiragana_form="ある", meaning="to exist, to be", status="available"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_kana_kanji", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=ARU_WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_kana_kanji_form_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    db = session_factory()
    aru = db.query(Vocab).filter(Vocab.hiragana_form == "ある").one()
    assert aru.kanji_form == "有る"
    assert aru.usually_kana is True
    db.close()


KARA_AMBIGUOUS_WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "殻",
            "is_common": True,
            "jlpt": [],
            "japanese": [{"word": "殻", "reading": "から"}],
            "senses": [{"english_definitions": ["shell"], "parts_of_speech": ["Noun"]}],
        },
        {
            "slug": "空",
            "is_common": True,
            "jlpt": [],
            "japanese": [{"word": "空", "reading": "から"}],
            "senses": [{"english_definitions": ["shell"], "parts_of_speech": ["Noun"]}],
        },
    ],
}


@pytest.mark.asyncio
async def test_run_kana_kanji_form_enrichment_leaves_ambiguous_rows_untouched(session_factory):
    db = session_factory()
    db.add(Vocab(kanji_form="から", hiragana_form="から", meaning="shell", status="available"))
    db.commit()
    db.close()

    job_id = jobs.create_job("jisho_kana_kanji", total=0, session_factory=session_factory)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://jisho.org/api/v1/search/words").mock(
            return_value=httpx.Response(200, json=KARA_AMBIGUOUS_WORDS_RESPONSE)
        )
        client = JishoClient(min_delay_seconds=0)
        await jobs.run_kana_kanji_form_enrichment(job_id, session_factory=session_factory, client=client)
        await client.aclose()

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["not_found"] == 1  # ambiguous -- left for manual review, not guessed at

    db = session_factory()
    kara = db.query(Vocab).filter(Vocab.hiragana_form == "から").one()
    assert kara.kanji_form == "から"  # untouched
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
    assert status["not_found"] == 0

    db = session_factory()
    ai = db.query(Kanji).filter(Kanji.kanji == "愛").one()
    assert ai.stroke_data is not None
    import json

    assert len(json.loads(ai.stroke_data)) == 13
    db.close()


def test_run_kanjivg_enrichment_tracks_archive_misses(session_factory):
    db = session_factory()
    db.add(Kanji(kanji="愛"))
    db.add(Kanji(kanji="龘"))  # not present in the sample archive fixture
    db.commit()
    db.close()

    job_id = jobs.create_job("kanjivg", total=0, session_factory=session_factory)
    jobs.run_kanjivg_enrichment(job_id, session_factory=session_factory, archive_path=KANJIVG_FIXTURE)

    status = jobs.get_job(job_id, session_factory=session_factory)
    assert status["status"] == "completed"
    assert status["completed"] == 2
    assert status["not_found"] == 1
