from collections import Counter

from tests.conftest import SEED_DIR

from app.ingestion.kanji_schedule_parser import parse_kanji_schedule

SCHEDULE_PATH = SEED_DIR / "n3_kanji_merged.xlsx"


def test_parses_real_kanji_schedule_row_count():
    result = parse_kanji_schedule(SCHEDULE_PATH)
    # Confirmed by direct inspection: 792 kanji, all with a batch number.
    assert len(result.rows) == 792
    assert not result.warnings


def test_batches_span_1_through_16():
    result = parse_kanji_schedule(SCHEDULE_PATH)
    batches = Counter(r.batch_number for r in result.rows)
    assert set(batches) == set(range(1, 17))
    # Confirmed by direct inspection: 49-50 kanji per batch.
    assert all(49 <= count <= 50 for count in batches.values())


def test_difficulty_rank_captured():
    result = parse_kanji_schedule(SCHEDULE_PATH)
    ai = next(r for r in result.rows if r.kanji == "愛")
    assert ai.difficulty_rank == 526
    assert ai.batch_number == 11
