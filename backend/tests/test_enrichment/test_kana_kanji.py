from app.enrichment.jisho_client import JishoWordResult, JishoWordSense
from app.enrichment.kana_kanji import (
    KanaKanjiOutcome,
    find_kanji_form,
    format_meaning_groups,
    rank_candidates,
)


def _result(word: str, reading: str, senses: list[JishoWordSense]) -> JishoWordResult:
    return JishoWordResult(word=word, reading=reading, is_common=True, jlpt=[], senses=senses)


def test_matches_unambiguous_homophone_by_meaning_overlap():
    # そう has many kana homophones on Jisho; only 僧's senses overlap the
    # row's stored meaning, so it should win outright (the そう/僧 case from
    # the spec: our DB has kanji_form="そう" but the word is really 僧).
    results = [
        _result("僧", "そう", [JishoWordSense(["monk", "priest"], ["Noun"])]),
        _result("沿う", "そう", [JishoWordSense(["to run along", "to run beside"], ["Verb"])]),
        _result("然う", "そう", [JishoWordSense(["in that way", "thus"], ["Adverb"])]),
    ]

    result = find_kanji_form("そう", "monk / priest", results)

    assert result.outcome is KanaKanjiOutcome.MATCHED
    assert result.kanji_form == "僧"
    assert result.usually_kana is False


def test_flags_usually_kana_instead_of_treating_as_plain_match():
    results = [
        _result(
            "有る",
            "ある",
            [JishoWordSense(["to exist", "to be"], ["Verb"], tags=["Usually written using kana alone"])],
        )
    ]

    result = find_kanji_form("ある", "to exist, to be", results)

    assert result.outcome is KanaKanjiOutcome.MATCHED
    assert result.kanji_form == "有る"
    assert result.usually_kana is True


def test_no_kanji_bearing_candidate_for_reading():
    # e.g. a genuine kana-only particle -- every candidate word is itself kana.
    results = [_result("から", "から", [JishoWordSense(["from"], ["Particle"])])]

    result = find_kanji_form("から", "from", results)

    assert result.outcome is KanaKanjiOutcome.NO_KANJI_CANDIDATE
    assert result.kanji_form is None


def test_no_stored_meaning_to_disambiguate():
    results = [_result("僧", "そう", [JishoWordSense(["monk", "priest"], ["Noun"])])]

    result = find_kanji_form("そう", "", results)

    assert result.outcome is KanaKanjiOutcome.NO_STORED_MEANING


def test_no_confident_match_below_threshold():
    results = [_result("僧", "そう", [JishoWordSense(["monk", "priest"], ["Noun"])])]

    result = find_kanji_form("そう", "completely unrelated gloss text", results)

    assert result.outcome is KanaKanjiOutcome.NO_CONFIDENT_MATCH


def test_ambiguous_when_multiple_candidates_clear_threshold():
    results = [
        _result("殻", "から", [JishoWordSense(["shell"], ["Noun"])]),
        _result("空", "から", [JishoWordSense(["shell"], ["Noun"])]),
    ]

    result = find_kanji_form("から", "shell", results)

    assert result.outcome is KanaKanjiOutcome.AMBIGUOUS
    assert set(result.candidate_words) == {"殻", "空"}


def test_rank_candidates_orders_by_score_and_includes_zero_scores():
    results = [
        _result("殻", "から", [JishoWordSense(["shell"], ["Noun"])]),
        _result("空", "から", [JishoWordSense(["empty"], ["Noun"])]),
        _result("から", "から", [JishoWordSense(["from"], ["Particle"])]),  # no kanji, excluded
    ]

    ranked = rank_candidates("から", "shell", results)

    assert [c.word for c in ranked] == ["殻", "空"]
    assert ranked[0].score == 1.0
    assert ranked[1].score == 0.0


def test_rank_candidates_dedupes_same_word_keeping_best_score():
    results = [
        _result("有る", "ある", [JishoWordSense(["unrelated gloss"], ["Verb"])]),
        _result(
            "有る", "ある",
            [JishoWordSense(["to exist", "to be"], ["Verb"], tags=["Usually written using kana alone"])],
        ),
    ]

    ranked = rank_candidates("ある", "to exist, to be", results)

    assert len(ranked) == 1
    assert ranked[0].word == "有る"
    assert ranked[0].score == 1.0
    assert ranked[0].usually_kana is True


def test_rank_candidates_meaning_uses_jisho_numbered_format_for_multiple_senses():
    results = [
        _result(
            "掛ける", "かける",
            [
                JishoWordSense(["to hang up"], ["Ichidan verb"]),
                JishoWordSense(["to sit"], ["Ichidan verb"]),
                JishoWordSense(["to spend (time/money)"], ["Ichidan verb"]),
            ],
        )
    ]

    ranked = rank_candidates("かける", "", results)

    assert ranked[0].meaning == "1 - to hang up. 2 - to sit"  # same format as run_vocab_word_enrichment


def test_rank_candidates_meaning_is_plain_for_a_single_sense():
    results = [_result("僧", "そう", [JishoWordSense(["monk", "priest"], ["Noun"])])]

    ranked = rank_candidates("そう", "", results)

    assert ranked[0].meaning == "monk / priest"


def test_format_meaning_groups_matches_jobs_format_meaning():
    assert format_meaning_groups([["to hang up"], ["to sit"], ["to spend"]]) == "1 - to hang up. 2 - to sit"
    assert format_meaning_groups([["monk", "priest"]]) == "monk / priest"
    assert format_meaning_groups([]) == ""
