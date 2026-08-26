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

# Marks a vocab row as a bound affix in the source lists (e.g. kanji_form
# "~的"/hiragana_form "~てき" is the suffix -teki, not a free word;
# "OO~" would mark a prefix). Jisho's word-search API doesn't recognize the
# literal marker as part of a headword, so anything querying Jisho by
# kanji_form must search the stripped form -- confirmed by direct testing,
# "~化" returns zero results while "化" returns the intended entry. The
# marker itself stays in the stored kanji_form/hiragana_form since it's
# meaningful for a learner to see.
_AFFIX_MARKER = "~"


def extract_kanji(text: str) -> set[str]:
    if not text:
        return set()
    return set(_CJK_PATTERN.findall(text))


def strip_html(text: str) -> str:
    return _HTML_TAG_PATTERN.sub("", text or "").strip()


def strip_affix_marker(text: str) -> str:
    return text.strip(_AFFIX_MARKER) if text else text
