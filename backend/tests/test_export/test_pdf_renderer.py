from pathlib import Path

from pypdf import PdfReader

from app.export.pdf_renderer import KanjiPageData, KanjiPageWord, render_html, render_pdf

SAMPLE_PAGE = KanjiPageData(
    kanji="愛",
    meanings="love, affection, favourite",
    kun_yomi="いと.しい・かな.しい",
    on_yomi="アイ",
    stroke_paths=["M1,1 L2,2", "M3,3 L4,4", "M5,5 L6,6"],
    words=[
        KanjiPageWord(kanji_form="愛犬", hiragana_form="あいけん", meaning="pet dog"),
        KanjiPageWord(kanji_form="愛情", hiragana_form="あいじょう", meaning="affection"),
    ],
)


def test_render_html_has_one_svg_step_per_stroke():
    html = render_html([SAMPLE_PAGE])
    assert html.count("<svg") == len(SAMPLE_PAGE.stroke_paths)


def test_render_html_cumulative_strokes_per_step():
    html = render_html([SAMPLE_PAGE])
    # The first stroke-box svg should contain exactly 1 <path>, the last
    # should contain all 3 (cumulative), so total <path> count across all
    # boxes is 1+2+3 = 6.
    assert html.count("<path") == 6


def test_render_html_includes_kun_on_meanings_and_words():
    html = render_html([SAMPLE_PAGE])
    assert "愛" in html
    assert "love, affection, favourite" in html
    assert "いと.しい・かな.しい" in html
    assert "アイ" in html
    assert "愛犬（あいけん） pet dog" in html
    assert "愛情（あいじょう） affection" in html


def test_render_html_multiple_kanji_pages():
    other = KanjiPageData(
        kanji="私", meanings="I, me", kun_yomi="わたし", on_yomi="シ", stroke_paths=["M1,1"], words=[]
    )
    html = render_html([SAMPLE_PAGE, other])
    assert html.count('class="kanji-page"') == 2


def test_render_pdf_produces_a_real_pdf_file(tmp_path: Path):
    output_path = tmp_path / "week.pdf"
    render_pdf([SAMPLE_PAGE], output_path)

    assert output_path.exists()
    data = output_path.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 1000


def test_render_pdf_produces_one_physical_page_per_kanji(tmp_path: Path):
    # Real-world scale check: this caught a false alarm during manual
    # testing where a naive `file`-command page-count heuristic misreported
    # a correctly-paginated 50-page PDF as only 8 pages. pypdf reads the
    # actual page tree, so this is the trustworthy check.
    other = KanjiPageData(
        kanji="私", meanings="I, me", kun_yomi="わたし", on_yomi="シ", stroke_paths=["M1,1"], words=[]
    )
    third = KanjiPageData(
        kanji="時", meanings="time, hour", kun_yomi="とき", on_yomi="ジ", stroke_paths=["M1,1", "M2,2"], words=[]
    )
    output_path = tmp_path / "multi.pdf"
    render_pdf([SAMPLE_PAGE, other, third], output_path)

    reader = PdfReader(output_path)
    assert len(reader.pages) == 3
    assert "愛" in reader.pages[0].extract_text()
    assert "私" in reader.pages[1].extract_text()
    assert "時" in reader.pages[2].extract_text()
