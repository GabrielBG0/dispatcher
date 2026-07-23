"""KanjiVG stroke-path source: one-time bulk download + local lookup, per the
plan's decision to avoid hundreds of individual rate-limited per-kanji
fetches.

KanjiVG publishes a single combined XML file per release (an order of
magnitude smaller / simpler to work with than the per-kanji SVG zip) via
GitHub releases. Structure (confirmed by direct inspection of release
r20250816): each kanji is a `<kanji id="kvg:kanji_{codepoint:05x}">` element
containing nested `<g>` groups; `<path>` elements appear in document order
== stroke order regardless of nesting depth, so a depth-first `.iter()` over
the kanji element yields strokes in the correct order.
"""

import gzip
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/KanjiVG/kanjivg/releases/latest"


def resolve_latest_archive_url() -> str:
    resp = httpx.get(GITHUB_LATEST_RELEASE_API, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    payload = resp.json()
    for asset in payload.get("assets", []):
        if asset["name"].endswith(".xml.gz"):
            return asset["browser_download_url"]
    raise RuntimeError("No .xml.gz asset found in latest KanjiVG release")


def download_archive(dest_path: Path, url: str | None = None) -> Path:
    """Idempotent: skips the download entirely if dest_path already exists."""
    if dest_path.exists():
        return dest_path

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    url = url or resolve_latest_archive_url()

    with httpx.stream("GET", url, timeout=60.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)
        tmp_path.replace(dest_path)

    return dest_path


def _kanji_id_to_char(kanji_id: str) -> str | None:
    # e.g. "kvg:kanji_0611b" -> "愛". Some entries have variant suffixes
    # like "kvg:kanji_09fa0-Kaisho" -- codepoint is still the first 5 hex digits.
    prefix = "kvg:kanji_"
    if not kanji_id.startswith(prefix):
        return None
    hex_part = kanji_id[len(prefix):].split("-")[0]
    try:
        return chr(int(hex_part, 16))
    except ValueError:
        return None


def build_stroke_index(xml_gz_path: Path) -> dict[str, list[str]]:
    """Parses the archive into {kanji_char: [stroke_path_d, ...]}, one entry
    per base (non-variant) kanji id -- first occurrence wins if a character
    has multiple style variants in the file.
    """
    index: dict[str, list[str]] = {}

    with gzip.open(xml_gz_path, "rt", encoding="utf-8") as f:
        tree = ET.parse(f)

    root = tree.getroot()
    ns = {"kvg": "http://kanjivg.tagaini.net"}

    for kanji_el in root.findall("kanji"):
        kanji_id = kanji_el.get("id", "")
        if "-" in kanji_id.removeprefix("kvg:kanji_"):
            continue  # skip style variants, keep the base glyph only
        char = _kanji_id_to_char(kanji_id)
        if char is None or char in index:
            continue

        # Only the kvg:-prefixed attributes are namespaced in this file;
        # element tags themselves ("kanji", "g", "path") are not.
        paths = [path_el.get("d", "") for path_el in kanji_el.iter("path")]

        if paths:
            index[char] = paths

    return index


def stroke_paths_to_json(paths: list[str]) -> str:
    return json.dumps(paths, ensure_ascii=False)


def stroke_paths_from_json(data: str) -> list[str]:
    return json.loads(data)
