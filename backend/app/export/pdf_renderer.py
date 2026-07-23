"""Weekly kanji PDF: one page per target kanji. Built as HTML/CSS (Jinja2
template) rendered to PDF via Playwright's headless Chromium -- see the
plan's Decision 1 for why (Chromium renders the KanjiVG stroke paths as
inline SVG natively, no PNG rasterization step needed, and it sidesteps
WeasyPrint's fragile native-library install on Windows).
"""

from dataclasses import dataclass
from pathlib import Path

import jinja2
from playwright.sync_api import sync_playwright

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass(frozen=True)
class KanjiPageWord:
    kanji_form: str
    hiragana_form: str
    meaning: str


@dataclass(frozen=True)
class KanjiPageData:
    kanji: str
    meanings: str
    kun_yomi: str
    on_yomi: str
    stroke_paths: list[str]
    words: list[KanjiPageWord]


def _stroke_steps(stroke_paths: list[str]) -> list[list[str]]:
    """Cumulative strokes per step: step i shows strokes 1..i+1, so the
    diagram reads as the kanji being built up stroke by stroke.
    """
    return [stroke_paths[: i + 1] for i in range(len(stroke_paths))]


def render_html(pages: list[KanjiPageData]) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("kanji_page.html.jinja")
    contexts = [
        {
            "kanji": p.kanji,
            "meanings": p.meanings,
            "kun_yomi": p.kun_yomi,
            "on_yomi": p.on_yomi,
            "stroke_steps": _stroke_steps(p.stroke_paths),
            "words": p.words,
        }
        for p in pages
    ]
    return template.render(pages=contexts)


def render_pdf(pages: list[KanjiPageData], output_path: Path) -> Path:
    html = render_html(pages)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.pdf(path=str(output_path), format="A4", print_background=True)
        finally:
            browser.close()

    return output_path
