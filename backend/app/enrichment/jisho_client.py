"""Jisho.org client for vocab and kanji enrichment.

Decision (spec Open Item 3, resolved by direct testing against the live
site): the JSON words API (`/api/v1/search/words`) does NOT support the
`#kanji` query used on jisho.org's own search box -- it returns an empty
result set. Clean per-kanji on'yomi/kun'yomi/meanings only exist on the
rendered kanji HTML page, so kanji lookups scrape
`https://jisho.org/search/{char}%20%23kanji` and parse the
`kanji-details__main-*` sections. Word lookups use the JSON API, which
gives clean meanings/POS/is_common/jlpt tags directly.

Caching: this client only fetches -- it has no opinion on whether a fetch
is "needed". Callers (see enrichment/jobs.py) check DB state first and only
call these functions for kanji/words that are genuinely missing data, which
is what makes re-runs a no-op ("never re-fetch the same entry").
"""

import asyncio
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

WORDS_API_URL = "https://jisho.org/api/v1/search/words"
KANJI_SEARCH_URL_TEMPLATE = "https://jisho.org/search/{char}%20%23kanji"

_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


@dataclass
class JishoWordSense:
    english_definitions: list[str]
    parts_of_speech: list[str]


@dataclass
class JishoWordResult:
    word: str | None
    reading: str | None
    is_common: bool
    jlpt: list[str]
    senses: list[JishoWordSense]


@dataclass
class JishoKanjiResult:
    meanings: list[str]
    kun_yomi: list[str]
    on_yomi: list[str]


def _retry_config():
    return retry(
        retry=retry_if_exception_type(_RETRYABLE),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )


class JishoClient:
    def __init__(self, min_delay_seconds: float | None = None):
        self._min_delay = (
            min_delay_seconds if min_delay_seconds is not None else settings.jisho_min_delay_seconds
        )
        self._client = httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "dispatcher-n3-tool/0.1"})
        self._last_request_at: float = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_delay - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = asyncio.get_event_loop().time()

    async def search_words(self, keyword: str) -> list[JishoWordResult]:
        await self._throttle()

        @_retry_config()
        async def _do() -> httpx.Response:
            resp = await self._client.get(WORDS_API_URL, params={"keyword": keyword})
            resp.raise_for_status()
            return resp

        resp = await _do()
        payload = resp.json()

        results: list[JishoWordResult] = []
        for entry in payload.get("data", []):
            japanese = entry.get("japanese", [{}])
            first = japanese[0] if japanese else {}
            senses = [
                JishoWordSense(
                    english_definitions=s.get("english_definitions", []),
                    parts_of_speech=s.get("parts_of_speech", []),
                )
                for s in entry.get("senses", [])
            ]
            results.append(
                JishoWordResult(
                    word=first.get("word"),
                    reading=first.get("reading"),
                    is_common=bool(entry.get("is_common", False)),
                    jlpt=entry.get("jlpt", []),
                    senses=senses,
                )
            )
        return results

    async def fetch_kanji(self, character: str) -> JishoKanjiResult | None:
        await self._throttle()

        url = KANJI_SEARCH_URL_TEMPLATE.format(char=character)

        @_retry_config()
        async def _do() -> httpx.Response:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return resp

        resp = await _do()
        soup = BeautifulSoup(resp.text, "html.parser")

        meanings_el = soup.select_one(".kanji-details__main-meanings")
        meanings = (
            [m.strip() for m in meanings_el.get_text().split(",") if m.strip()]
            if meanings_el
            else []
        )

        kun_yomi = _extract_readings(soup, "kun_yomi")
        on_yomi = _extract_readings(soup, "on_yomi")

        if not meanings and not kun_yomi and not on_yomi:
            return None

        return JishoKanjiResult(meanings=meanings, kun_yomi=kun_yomi, on_yomi=on_yomi)


def _extract_readings(soup: BeautifulSoup, css_class: str) -> list[str]:
    dl = soup.select_one(f"dl.{css_class}")
    if dl is None:
        return []
    dd = dl.select_one(".kanji-details__main-readings-list")
    if dd is None:
        return []
    return [a.get_text().strip() for a in dd.select("a") if a.get_text().strip()]
