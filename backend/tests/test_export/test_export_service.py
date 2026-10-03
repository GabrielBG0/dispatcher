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
    assert set(files) == {"Japanese Complete Vocab - Batch 1.tsv"}
    lines = files["Japanese Complete Vocab - Batch 1.tsv"].strip("\n").split("\n")
    assert len(lines) == 2  # only batch 1's two words, not the batch-2 one


def test_export_vocab_split_by_pos(db_session):
    _seed_finalized_batch(db_session)
    files = export_service.export_vocab(db_session, batch_n=1, split_by_pos=True)
    assert set(files) == {"Japanese Vocabulary - Batch 1.tsv"}
    assert files["Japanese Vocabulary - Batch 1.tsv"].count("\n") == 2


def test_export_vocab_japanese_txt_is_front_only_one_per_line(db_session):
    _seed_finalized_batch(db_session)
    files = export_service.export_vocab_japanese_txt(db_session, batch_n=1)
    assert set(files) == {"Japanese Vocab - Batch 1.txt"}
    lines = files["Japanese Vocab - Batch 1.txt"].strip("\n").split("\n")
    assert lines == ["愛犬（あいけん）", "時間（じかん）"]
    for line in lines:
        assert "\t" not in line  # no meaning/tags columns, just the word


def test_export_vocab_japanese_txt_rejects_non_finalized_batch(db_session):
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    db_session.commit()
    with pytest.raises(export_service.ExportServiceError):
        export_service.export_vocab_japanese_txt(db_session, batch_n=2)


def test_export_vocab_japanese_txt_cumulative_includes_all_prior_weeks(db_session):
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    db_session.add(Batch(batch_number=2, status="finalized", weekly_target_used=126))

    ai = Kanji(kanji="愛")
    db_session.add(ai)
    inu = Kanji(kanji="犬")
    db_session.add(inu)
    db_session.flush()
    db_session.add(KanjiCoverage(kanji_id=ai.id, coverage_source="n3_batch", batch_number=1))
    db_session.add(KanjiCoverage(kanji_id=inu.id, coverage_source="n3_batch", batch_number=2))

    db_session.add(
        Vocab(
            kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=False,
        )
    )
    db_session.add(
        Vocab(
            kanji_form="犬", hiragana_form="いぬ", meaning="dog", part_of_speech="general",
            status="assigned", assigned_batch=2, needs_kanji_reading=True,
        )
    )
    db_session.commit()

    files = export_service.export_vocab_japanese_txt_cumulative(db_session, up_to_batch_n=2)
    assert set(files) == {"Japanese Vocab - Cumulative through Week 2.txt"}
    content = files["Japanese Vocab - Cumulative through Week 2.txt"]

    # This week's (week 2's) kanji section leads the file.
    assert content.index("犬") < content.index("=== Week 1 ===")
    assert "=== This week's kanji (Week 2) ===" in content
    assert "=== Week 1 ===" in content
    assert "=== Week 2 ===" in content
    assert content.index("=== Week 1 ===") < content.index("=== Week 2 ===")
    assert "時間（じかん）" in content
    assert "犬" in content

    # Each week's own section also lists that week's kanji, not just week 2's.
    week1_section = content.split("=== Week 1 ===")[1].split("=== Week 2 ===")[0]
    assert "Kanji: 愛" in week1_section
    week2_section = content.split("=== Week 2 ===")[1]
    assert "Kanji: 犬" in week2_section


def test_export_vocab_japanese_txt_cumulative_week_with_no_target_kanji(db_session):
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    db_session.add(
        Vocab(
            kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=False,
        )
    )
    db_session.commit()

    files = export_service.export_vocab_japanese_txt_cumulative(db_session, up_to_batch_n=1)
    content = files["Japanese Vocab - Cumulative through Week 1.txt"]
    assert "Kanji: (none)" in content


def test_export_vocab_japanese_txt_cumulative_skips_unfinalized_earlier_weeks(db_session):
    db_session.add(Batch(batch_number=1, status="draft", weekly_target_used=126))
    db_session.add(Batch(batch_number=2, status="finalized", weekly_target_used=126))
    db_session.add(
        Vocab(
            kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general",
            status="assigned", assigned_batch=2, needs_kanji_reading=False,
        )
    )
    db_session.commit()

    files = export_service.export_vocab_japanese_txt_cumulative(db_session, up_to_batch_n=2)
    content = files["Japanese Vocab - Cumulative through Week 2.txt"]
    assert "=== Week 1 ===" not in content
    assert "=== Week 2 ===" in content


def test_export_vocab_japanese_txt_cumulative_rejects_non_finalized_batch(db_session):
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    db_session.commit()
    with pytest.raises(export_service.ExportServiceError):
        export_service.export_vocab_japanese_txt_cumulative(db_session, up_to_batch_n=2)


def test_export_kanji_readings_only_includes_needs_reading_rows(db_session):
    _seed_finalized_batch(db_session)
    files = export_service.export_kanji_readings(db_session, batch_n=1)
    assert set(files) == {"Japanese Kanji - Batch 1.tsv"}
    lines = files["Japanese Kanji - Batch 1.tsv"].strip("\n").split("\n")
    assert len(lines) == 1
    assert lines[0].startswith("愛犬\tあいけん")


def test_export_vocab_tags_seen_in_class_fallback_words(db_session):
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    ai = Kanji(kanji="愛")
    db_session.add(ai)
    db_session.flush()
    db_session.add(KanjiCoverage(kanji_id=ai.id, coverage_source="n3_batch", batch_number=1))
    db_session.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet dog", part_of_speech="general",
            status="seen_in_class", assigned_batch=1, needs_kanji_reading=True,
        )
    )
    db_session.commit()

    files = export_service.export_vocab(db_session, batch_n=1, split_by_pos=False)
    content = files["Japanese Complete Vocab - Batch 1.tsv"]
    assert "seen_in_class_fallback" in content


def test_export_vocab_reading_deck_is_leading_run_of_vocab_deck(db_session):
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    ai = Kanji(kanji="愛")
    db_session.add(ai)
    db_session.flush()
    db_session.add(KanjiCoverage(kanji_id=ai.id, coverage_source="n3_batch", batch_number=1))
    db_session.add(
        Vocab(
            kanji_form="時間", hiragana_form="じかん", meaning="time", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=False,
        )
    )
    db_session.add(
        Vocab(
            kanji_form="愛犬", hiragana_form="あいけん", meaning="pet dog", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=True,
        )
    )
    # usually_kana row in the reading-deck tier -- its vocab front reads
    # hiragana-first ("あいねこ（愛猫）") while the reading front stays plain
    # kanji_form ("愛猫"), so alignment must hold on the underlying word, not
    # on matching rendered text.
    db_session.add(
        Vocab(
            kanji_form="愛猫", hiragana_form="あいねこ", meaning="pet cat", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=True, usually_kana=True,
        )
    )
    # Target-linked (contains 愛) but not needing a reading card -- e.g. it
    # has an orphan kanji. Should sort after the reading-deck words but
    # still ahead of the plain filler.
    db_session.add(
        Vocab(
            kanji_form="愛情", hiragana_form="あいじょう", meaning="affection", part_of_speech="general",
            status="assigned", assigned_batch=1, needs_kanji_reading=False,
        )
    )
    db_session.commit()

    vocab_files = export_service.export_vocab(db_session, batch_n=1, split_by_pos=False)
    vocab_fronts = [
        line.split("\t")[0]
        for line in vocab_files["Japanese Complete Vocab - Batch 1.tsv"].strip("\n").split("\n")
    ]
    reading_files = export_service.export_kanji_readings(db_session, batch_n=1)
    reading_fronts = [
        line.split("\t")[0] for line in reading_files["Japanese Kanji - Batch 1.tsv"].strip("\n").split("\n")
    ]

    assert reading_fronts == ["愛犬", "愛猫"]
    # Structural leading-run check: each reading-deck word's kanji_form
    # shows up in the corresponding vocab-deck row at the same position,
    # regardless of how that row's front is rendered.
    assert all(reading_fronts[i] in vocab_fronts[i] for i in range(len(reading_fronts)))
    assert vocab_fronts == [
        "愛犬（あいけん）", "あいねこ（愛猫）", "愛情（あいじょう）", "時間（じかん）",
    ]


def test_export_rejects_non_finalized_batch(db_session):
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    db_session.commit()
    with pytest.raises(export_service.ExportServiceError):
        export_service.export_vocab(db_session, batch_n=2, split_by_pos=False)


def test_get_export_preview_renders_cards_and_decks(db_session):
    _seed_finalized_batch(db_session)
    db_session.add(
        Vocab(
            kanji_form="食べる", hiragana_form="たべる", meaning="to eat", part_of_speech="verb",
            status="assigned", assigned_batch=1, needs_kanji_reading=False,
        )
    )
    db_session.commit()

    words = {w.kanji_form: w for w in export_service.get_export_preview(db_session, batch_n=1, split_by_pos=True)}

    reading_word = words["愛犬"]
    assert reading_word.vocab_card.front == "愛犬（あいけん）"
    assert reading_word.vocab_card.back == "pet dog"
    assert reading_word.vocab_card.deck == "Japanese Vocabulary"
    assert reading_word.covers_target_kanji == ["愛"]
    assert reading_word.kanji_reading_card is not None
    assert reading_word.kanji_reading_card.front == "愛犬"
    assert reading_word.kanji_reading_card.back == "あいけん"
    assert reading_word.kanji_reading_card.deck == "Japanese Kanji"

    no_reading_word = words["時間"]
    assert no_reading_word.covers_target_kanji == []
    assert no_reading_word.kanji_reading_card is None

    verb_word = words["食べる"]
    assert verb_word.vocab_card.deck == "Japanese Verbs"


def test_get_export_preview_combined_deck_name(db_session):
    _seed_finalized_batch(db_session)
    words = export_service.get_export_preview(db_session, batch_n=1, split_by_pos=False)
    assert all(w.vocab_card.deck == "Japanese Complete Vocab" for w in words)


def test_get_export_preview_front_formatting_branches(db_session):
    db_session.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    db_session.add(
        Vocab(
            kanji_form="いつも", hiragana_form="いつも", meaning="always", part_of_speech="adverb",
            status="assigned", assigned_batch=1,
        )
    )
    db_session.add(
        Vocab(
            kanji_form="時計", hiragana_form="とけい", meaning="clock", part_of_speech="general",
            status="assigned", assigned_batch=1, usually_kana=True,
        )
    )
    db_session.commit()

    words = {w.kanji_form: w for w in export_service.get_export_preview(db_session, batch_n=1)}
    assert words["いつも"].vocab_card.front == "いつも"  # kana-only: no parenthetical
    assert words["時計"].vocab_card.front == "とけい（時計）"  # usually_kana: reading leads


def test_get_export_preview_rejects_non_finalized_batch(db_session):
    db_session.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    db_session.commit()
    with pytest.raises(export_service.ExportServiceError):
        export_service.get_export_preview(db_session, batch_n=2)


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


def _seed_three_weeks_with_gap(db):
    """Weeks 1 and 3 finalized, week 2 still draft. Week 3's target kanji
    (犬) also appears in a week-1 filler word (子犬), which must stay filler
    in the cumulative export -- target-linking is per week, not a union.
    """
    db.add(Batch(batch_number=1, status="finalized", weekly_target_used=126))
    db.add(Batch(batch_number=2, status="draft", weekly_target_used=126))
    db.add(Batch(batch_number=3, status="finalized", weekly_target_used=126))

    ai = Kanji(kanji="愛", meanings="love")
    inu = Kanji(kanji="犬", meanings="dog")
    db.add_all([ai, inu])
    db.flush()
    db.add(KanjiCoverage(kanji_id=ai.id, coverage_source="n3_batch", batch_number=1))
    db.add(KanjiCoverage(kanji_id=inu.id, coverage_source="n3_batch", batch_number=3))

    for kanji_form, hiragana, batch, needs_reading in [
        ("時間", "じかん", 1, False),
        ("愛犬", "あいけん", 1, True),
        ("子犬", "こいぬ", 1, False),
        ("時計", "とけい", 2, False),  # draft week -- excluded
        ("犬", "いぬ", 3, True),
        ("朝", "あさ", 3, False),
    ]:
        db.add(
            Vocab(
                kanji_form=kanji_form, hiragana_form=hiragana, meaning="m", part_of_speech="general",
                status="assigned", assigned_batch=batch, needs_kanji_reading=needs_reading,
            )
        )
    db.commit()


def test_export_vocab_cumulative_orders_week_by_week_with_own_batch_tags(db_session):
    _seed_three_weeks_with_gap(db_session)
    files = export_service.export_vocab_cumulative(db_session, up_to_batch_n=3, split_by_pos=False)
    assert set(files) == {"Japanese Complete Vocab - Cumulative through Week 3.tsv"}
    lines = files["Japanese Complete Vocab - Cumulative through Week 3.tsv"].strip("\n").split("\n")
    fronts = [line.split("\t")[0] for line in lines]
    # Week 1 (reading tier, then filler alphabetical -- 子犬 stays filler),
    # week 2 skipped, then week 3.
    assert fronts == ["愛犬（あいけん）", "子犬（こいぬ）", "時間（じかん）", "犬（いぬ）", "朝（あさ）"]
    assert all(line.endswith("batch::1") for line in lines[:3])
    assert all(line.endswith("batch::3") for line in lines[3:])


def test_export_vocab_cumulative_split_by_pos(db_session):
    _seed_three_weeks_with_gap(db_session)
    files = export_service.export_vocab_cumulative(db_session, up_to_batch_n=3, split_by_pos=True)
    assert set(files) == {"Japanese Vocabulary - Cumulative through Week 3.tsv"}


def test_export_kanji_readings_cumulative_matches_vocab_order(db_session):
    _seed_three_weeks_with_gap(db_session)
    files = export_service.export_kanji_readings_cumulative(db_session, up_to_batch_n=3)
    assert set(files) == {"Japanese Kanji - Cumulative through Week 3.tsv"}
    lines = files["Japanese Kanji - Cumulative through Week 3.tsv"].strip("\n").split("\n")
    assert [line.split("\t")[0] for line in lines] == ["愛犬", "犬"]
    assert lines[0].endswith("batch::1")
    assert lines[1].endswith("batch::3")


def test_build_kanji_pdf_pages_cumulative_pages_per_week(db_session):
    _seed_three_weeks_with_gap(db_session)
    pages, warnings = export_service.build_kanji_pdf_pages_cumulative(db_session, up_to_batch_n=3)
    assert [p.kanji for p in pages] == ["愛", "犬"]
    # Each kanji's word list only shows its own week's words: 犬 is week 3's
    # target, so week 1's 愛犬/子犬 stay off its page.
    assert {w.kanji_form for w in pages[1].words} == {"犬"}
    assert {w.detail for w in warnings} == {"no KanjiVG stroke data cached"}


def test_cumulative_exports_reject_non_finalized_batch(db_session):
    _seed_three_weeks_with_gap(db_session)
    with pytest.raises(export_service.ExportServiceError):
        export_service.export_vocab_cumulative(db_session, up_to_batch_n=2, split_by_pos=False)
    with pytest.raises(export_service.ExportServiceError):
        export_service.export_kanji_readings_cumulative(db_session, up_to_batch_n=2)
    with pytest.raises(export_service.ExportServiceError):
        export_service.build_kanji_pdf_pages_cumulative(db_session, up_to_batch_n=2)


def test_cumulative_exports_respect_start_week(db_session):
    _seed_three_weeks_with_gap(db_session)

    vocab = export_service.export_vocab_cumulative(db_session, up_to_batch_n=3, split_by_pos=False, from_batch_n=2)
    assert set(vocab) == {"Japanese Complete Vocab - Weeks 2-3.tsv"}
    fronts = [line.split("\t")[0] for line in vocab["Japanese Complete Vocab - Weeks 2-3.tsv"].strip("\n").split("\n")]
    assert fronts == ["犬（いぬ）", "朝（あさ）"]  # week 1 excluded, draft week 2 skipped

    kanji = export_service.export_kanji_readings_cumulative(db_session, up_to_batch_n=3, from_batch_n=3)
    assert kanji["Japanese Kanji - Weeks 3-3.tsv"].startswith("犬\tいぬ")

    pages, _ = export_service.build_kanji_pdf_pages_cumulative(db_session, up_to_batch_n=3, from_batch_n=2)
    assert [p.kanji for p in pages] == ["犬"]

    txt = export_service.export_vocab_japanese_txt_cumulative(db_session, up_to_batch_n=3, from_batch_n=2)
    content = txt["Japanese Vocab - Weeks 2-3.txt"]
    assert "=== Week 1 ===" not in content
    assert "=== Week 3 ===" in content


@pytest.mark.parametrize("from_batch_n", [0, 4])
def test_cumulative_exports_reject_invalid_start_week(db_session, from_batch_n):
    _seed_three_weeks_with_gap(db_session)
    with pytest.raises(export_service.ExportServiceError):
        export_service.export_vocab_cumulative(db_session, up_to_batch_n=3, split_by_pos=False, from_batch_n=from_batch_n)
