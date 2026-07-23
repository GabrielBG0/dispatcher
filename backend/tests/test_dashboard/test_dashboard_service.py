from datetime import date

from app.models.batch import Batch
from app.models.study_config import StudyConfig
from app.models.vocab import Vocab
from app.services import dashboard_service


_counter = 0


def _make_vocab(status, batch=None):
    global _counter
    _counter += 1
    return Vocab(
        kanji_form=f"w{status}{batch}{_counter}",
        hiragana_form=f"w{status}{batch}{_counter}",
        meaning="x",
        part_of_speech="general",
        status=status,
        assigned_batch=batch,
    )


def test_overview_counts_vocab_by_status(db_session):
    db_session.add_all(
        [
            _make_vocab("seen_in_class"),
            _make_vocab("available"),
            _make_vocab("available"),
            _make_vocab("assigned", batch=1),
        ]
    )
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    db_session.commit()

    overview = dashboard_service.get_overview(db_session, today=date(2026, 7, 27))
    assert overview.words_total == 4
    assert overview.words_seen_in_class == 1
    assert overview.words_available == 2
    assert overview.words_assigned == 1


def test_overview_computes_weeks_remaining_and_study_end_date(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=16))
    db_session.commit()

    overview = dashboard_service.get_overview(db_session, today=date(2026, 7, 27))
    assert overview.study_end_date == date(2026, 11, 16)
    assert overview.weeks_remaining == 16


def test_overview_weeks_remaining_floors_at_zero_past_end_date(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), new_card_weeks=16))
    db_session.commit()

    overview = dashboard_service.get_overview(db_session, today=date(2027, 1, 1))
    assert overview.weeks_remaining == 0


def test_overview_flags_behind_pace_when_any_batch_exceeded_floor(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), daily_minimum=18))
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=200))
    db_session.commit()

    overview = dashboard_service.get_overview(db_session, today=date(2026, 7, 27))
    assert overview.behind_pace is True
    assert len(overview.batches) == 2


def test_overview_on_pace_when_all_batches_at_floor(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27), daily_minimum=18))
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    db_session.commit()

    overview = dashboard_service.get_overview(db_session, today=date(2026, 7, 27))
    assert overview.behind_pace is False


def test_overview_batch_word_counts(db_session):
    db_session.add(StudyConfig(start_date=date(2026, 7, 27)))
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    db_session.add_all([_make_vocab("assigned", batch=1), _make_vocab("assigned", batch=1)])
    db_session.commit()

    overview = dashboard_service.get_overview(db_session, today=date(2026, 7, 27))
    assert overview.batches[0].word_count == 2
