import httpx
import pytest
import respx

from app.enrichment.jisho_client import JishoClient

WORDS_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "愛",
            "is_common": True,
            "jlpt": ["jlpt-n3"],
            "japanese": [{"word": "愛", "reading": "あい"}],
            "senses": [
                {
                    "english_definitions": ["love", "affection", "care"],
                    "parts_of_speech": ["Noun"],
                }
            ],
        }
    ],
}

KANJI_HTML = """
<html><body>
<div class="kanji-details__main-meanings">
      love, affection, favourite
    </div>
<!-- Regression fixture: real jisho.org pages reuse the exact class
     "dictionary_entry on_yomi" for the unrelated Radical row (confirmed
     by direct inspection), appearing BEFORE the real readings container.
     A selector that isn't scoped to .kanji-details__main-readings will
     silently grab this one instead and return no readings. -->
<dl class="dictionary_entry on_yomi">
  <dt>Radical:</dt>
  <dd><span class="radical_meaning">heart</span></dd>
</dl>
<div class="kanji-details__main-readings">
  <dl class="dictionary_entry kun_yomi">
    <dt>Kun:</dt>
    <dd class="kanji-details__main-readings-list" lang="ja">
      <a href="#">いと.しい</a>&#12289; <a href="#">かな.しい</a>
    </dd>
  </dl>
  <dl class="dictionary_entry on_yomi">
    <dt>On:</dt>
    <dd class="kanji-details__main-readings-list" lang="ja">
      <a href="#">アイ</a>
    </dd>
  </dl>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_search_words_parses_response():
    client = JishoClient(min_delay_seconds=0)
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://jisho.org/api/v1/search/words").mock(
                return_value=httpx.Response(200, json=WORDS_RESPONSE)
            )
            results = await client.search_words("愛")

        assert len(results) == 1
        assert results[0].word == "愛"
        assert results[0].reading == "あい"
        assert results[0].is_common is True
        assert results[0].jlpt == ["jlpt-n3"]
        assert results[0].senses[0].english_definitions == ["love", "affection", "care"]
    finally:
        await client.aclose()


WIKIPEDIA_SENSE_RESPONSE = {
    "meta": {"status": 200},
    "data": [
        {
            "slug": "東京証券取引所",
            "is_common": True,
            "jlpt": [],
            "japanese": [{"word": "東京証券取引所", "reading": "とうきょうしょうけんとりひきじょ"}],
            "senses": [
                {
                    "english_definitions": ["Tokyo Stock Exchange", "TSE"],
                    "parts_of_speech": ["Noun"],
                },
                {
                    "english_definitions": ["Tokyo Stock Exchange"],
                    "parts_of_speech": ["Wikipedia definition"],
                },
            ],
        }
    ],
}


@pytest.mark.asyncio
async def test_search_words_excludes_wikipedia_sourced_senses():
    # Real jisho.org response shape (confirmed by direct inspection): senses
    # merged in from Wikipedia/DBpedia carry parts_of_speech == ["Wikipedia
    # definition"] instead of a real grammatical tag. These are encyclopedia
    # blurbs, not vocabulary definitions, and must never reach a caller.
    client = JishoClient(min_delay_seconds=0)
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://jisho.org/api/v1/search/words").mock(
                return_value=httpx.Response(200, json=WIKIPEDIA_SENSE_RESPONSE)
            )
            results = await client.search_words("東京証券取引所")

        assert len(results) == 1
        assert len(results[0].senses) == 1  # the Wikipedia-definition sense was dropped
        assert results[0].senses[0].english_definitions == ["Tokyo Stock Exchange", "TSE"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetch_kanji_parses_readings_and_meanings():
    client = JishoClient(min_delay_seconds=0)
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(url__regex=r"https://jisho\.org/search/.*").mock(
                return_value=httpx.Response(200, text=KANJI_HTML)
            )
            result = await client.fetch_kanji("愛")

        assert result is not None
        assert result.meanings == ["love", "affection", "favourite"]
        assert result.kun_yomi == ["いと.しい", "かな.しい"]
        assert result.on_yomi == ["アイ"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_fetch_kanji_returns_none_when_no_data_found():
    client = JishoClient(min_delay_seconds=0)
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get(url__regex=r"https://jisho\.org/search/.*").mock(
                return_value=httpx.Response(200, text="<html><body>nothing here</body></html>")
            )
            result = await client.fetch_kanji("Z")

        assert result is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_throttle_enforces_min_delay():
    import time

    client = JishoClient(min_delay_seconds=0.2)
    try:
        with respx.mock(assert_all_called=True) as mock:
            mock.get("https://jisho.org/api/v1/search/words").mock(
                return_value=httpx.Response(200, json=WORDS_RESPONSE)
            )
            start = time.monotonic()
            await client.search_words("a")
            await client.search_words("b")
            elapsed = time.monotonic() - start

        assert elapsed >= 0.2
    finally:
        await client.aclose()
