import json

import pytest

from app.models.batch import Batch
from app.models.kanji import Kanji
from app.models.kanji_coverage import KanjiCoverage
from app.models.vocab import Vocab
from app.services import export_service


def _seed_finalized_batch(db):
    db.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))

    ai = Kanji(kanji="愛", meanings="love, affection", kun_yomi="いと.しい", on_yomi="アイ")
    ai.stroke_data = json.dumps(["M1,1", "M2,2"])
    db.add(ai)
    db.flush()
    db.add(KanjiCoverage(kanji_id=ai.id, coverage_source="n3_batch", batch_number=1))

    inu = Kanji(kanji="犬")  # scheduled elsewhere, not this batch's target
    db.add(inu)
    db.flush()
    db.add(KanjiCoverage(kanji_id=inu.id, coverage_source="n3_batch", batch_number=2))

    db.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet dog", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=True,
        )
    )
    db.add(
        Vocab(
            kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=False,
        )
    )
    # A word from a *different* batch that also happens to contain 愛 --
    # must NOT show up on 愛's PDF page (spec: only this batch's words).
    db.add(
        Vocab(
            kanji_form="愛情", hiragana_form="あいじょう", meaning="affection", part_of_speech="general",
            status="assigned", assigned_batch=2, needs_kanji_reading=True,
        )
    )
    db.commit()


def test_export_vocab_combined(db_session):
    _seed_finalized_batch(db_session)
    files = export_service.export_vocab(db_session, batch_n=1, split_by_pos=False)
    assert set(files) == {"Japanese Complete Vocab.tsv"}
    lines = files["Japanese Complete Vocab.tsv"].strip("\n").split("\n")
    assert len(lines) == 2  # only batch 1's two words, not the batch-2 one


def test_export_vocab_split_by_pos(db_session):
    _seed_finalized_batch(db_session)
    files = export_service.export_vocab(db_session, batch_n=1, split_by_pos=True)
    assert set(files) == {"Japanese Vocabulary.tsv"}
    assert files["Japanese Vocabulary.tsv"].count("\n") == 2


def test_export_kanji_readings_only_includes_needs_reading_rows(db_session):
    _seed_finalized_batch(db_session)
    content = export_service.export_kanji_readings(db_session, batch_n=1)
    lines = content.strip("\n").split("\n")
    assert len(lines) == 1
    assert lines[0].startswith("愛犬\tあいけん")


def test_export_rejects_non_finalized_batch(db_session):
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    db_session.commit()
    with pytest.raises(export_service.ExportServiceError):
        export_service.export_vocab(db_session, batch_n=2, split_by_pos=False)


def test_build_kanji_pdf_pages_includes_only_this_batchs_words(db_session):
    _seed_finalized_batch(db_session)
    pages, warnings = export_service.build_kanji_pdf_pages(db_session, batch_n=1)

    assert len(pages) == 1
    page = pages[0]
    assert page.kanji == "愛"
    word_forms = {w.kanji_form for w in page.words}
    assert word_forms == {"愛犬"}  # not 愛情, which belongs to batch 2
    assert page.stroke_paths == ["M1,1", "M2,2"]
    assert not warnings  # this kanji has both stroke data and enrichment data


def test_build_kanji_pdf_pages_warns_on_missing_stroke_or_enrichment_data(db_session):
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    bare = Kanji(kanji="猫")  # no meanings/readings/stroke_data at all
    db_session.add(bare)
    db_session.flush()
    db_session.add(KanjiCoverage(kanji_id=bare.id, coverage_source="n3_batch", batch_number=1))
    db_session.commit()

    pages, warnings = export_service.build_kanji_pdf_pages(db_session, batch_n=1)
    assert len(pages) == 1
    kinds = {w.detail for w in warnings}
    assert "no KanjiVG stroke data cached" in kinds
    assert "no Jisho enrichment data" in kinds
