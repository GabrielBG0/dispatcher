"""Shared CJK-character extraction, used by ingestion (Anki export parsing)
and selection (classifying which kanji a vocab word contains).
"""

import re

# CJK Unified Ideographs (U+4E00-U+9FFF), per spec, plus common extensions
# that show up in real vocab/kanji data: Extension A (U+3400-U+4DBF) and
# CJK Compatibility Ideographs (U+F900-U+FAFF). Written as \uXXXX escapes
# (not literal characters) so the range is unambiguous regardless of how
# this file gets copied/edited/displayed.
_CJK_PATTERN = re.compile("[一-鿿㐀-䶿豈-﫿]")

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def extract_kanji(text: str) -> set[str]:
    if not text:
        return set()
    return set(_CJK_PATTERN.findall(text))


def strip_html(text: str) -> str:
    return _HTML_TAG_PATTERN.sub("", text or "").strip()
