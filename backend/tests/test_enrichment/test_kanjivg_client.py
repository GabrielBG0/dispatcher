from tests.conftest import FIXTURES_DIR

from app.enrichment.kanjivg_client import (
    build_stroke_index,
    stroke_paths_from_json,
    stroke_paths_to_json,
)

FIXTURE_PATH = FIXTURES_DIR / "kanjivg_sample.xml.gz"


def test_builds_index_for_all_fixture_kanji():
    index = build_stroke_index(FIXTURE_PATH)
    assert set(index) == {"愛", "私", "一"}


def test_stroke_order_and_count():
    index = build_stroke_index(FIXTURE_PATH)
    assert len(index["愛"]) == 13
    assert len(index["一"]) == 1
    assert all(p for p in index["愛"])  # every stroke has a real path d=


def test_json_roundtrip():
    index = build_stroke_index(FIXTURE_PATH)
    encoded = stroke_paths_to_json(index["私"])
    decoded = stroke_paths_from_json(encoded)
    assert decoded == index["私"]
