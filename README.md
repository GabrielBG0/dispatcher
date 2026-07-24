# dispatcher

A local web app for building a weekly N3 vocabulary/kanji study plan for a
Japanese study group. It takes a vocabulary master list and a per-week kanji
teaching schedule, picks the vocab words each week that actually teach that
week's target kanji (skipping ahead of the schedule when needed, avoiding
words the class has already seen), and exports the result as Anki decks and
a printable kanji reference PDF.

Stack: FastAPI + SQLAlchemy + SQLite on the backend, React + Vite on the
frontend. Everything runs locally — no external accounts, no cloud services
except read-only lookups against Jisho.org and the KanjiVG project for
enrichment data.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) for the backend (Python is pinned via
  `uv python pin`, currently 3.13 — don't use system Python/pip/venv/poetry)
- Node.js (18+) and npm for the frontend

## Setup

```bash
# backend
cd backend
uv sync
uv run playwright install chromium   # one-time, needed for PDF export

# frontend
cd ../frontend
npm install
```

## Running it

**Development** (hot-reload on both sides, two terminals):

```bash
# terminal 1 — backend on http://127.0.0.1:8000
cd backend
uv run uvicorn app.main:app --reload --port 8000

# terminal 2 — frontend on http://localhost:5173
cd frontend
npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api/*` to the backend, so the
UI and API behave as one origin during development.

**Production-style** (single process serves the built UI):

```bash
cd frontend && npm run build     # writes frontend/dist
cd ../backend
uv run uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000` — FastAPI serves the built frontend directly
(with SPA deep-link fallback, so refreshing on `/batches` etc. works) and
the API under the same origin.

The SQLite database (`backend/data/dispatcher.db`) and the KanjiVG stroke
cache are created automatically on first run — no migrations to run.

## Concepts

- **Vocab** — the N3 word list. Each row has a kanji form, a hiragana
  reading, a meaning, and a part of speech. Status is `available`,
  `assigned` (in a batch), or `seen_in_class` (already taught, excluded
  from selection).
- **Kanji schedule** — which kanji the class is learning in which week
  (batch 1..N), independent of vocab.
- **Batch** — one week's draft or finalized set of vocab words, generated
  to cover that week's target kanji.
- **Target-linked vs. filler words** — a word is target-linked if it
  contains at least one of the week's target kanji; filler words round the
  batch out to its weekly target size.
- **needs_kanji_reading** — a word gets a dedicated kanji-reading card only
  if it's target-linked *and* contains no "orphan" kanji (a kanji the class
  hasn't learned yet and isn't a target this week) — otherwise the reading
  card would test a character nobody's been taught.
- **Skip-ahead guard** — a word is only eligible if every kanji in it is
  either a target this week, already known (seen in a past batch or the
  seen-in-class baseline), or itself a target in this batch — so the tool
  never surfaces a word that silently depends on a kanji from three weeks
  from now.
- **Study config** — start date, total weeks, how many are "new card" weeks
  vs. review-buffer weeks, and the daily-minimum word count used to compute
  each week's target size and whether the plan is behind pace.

## Usage guide

The UI has four screens, meant to be used roughly in this order the first
time: **Import → Dashboard (study config) → Batch Review → Export**, then
Batch Review/Export weekly after that.

### 1. Import

Upload the three source files. Each upload is idempotent — re-uploading the
same file re-parses it and upserts rows without creating duplicates, so
it's safe to re-import after fixing a source file.

| File | Format | What it does |
|---|---|---|
| N3 vocab list | `.xls` | The vocab master list (kanji form, hiragana reading, meaning). Rows with a blank meaning are queued for enrichment. |
| Kanji weekly schedule | `.xlsx` | Which kanji belong to which week's batch. Drives the target kanji for every batch. |
| Genki Anki export | `.tsv` or `.txt` | Optional. Builds the "already seen in class" baseline, so those kanji/words are excluded from selection. Parsing is format-agnostic — it works against any real Anki "Notes in Plain Text" export (single deck or a full multi-deck collection export) *or* a plain one-item-per-line list. It's tolerant of real export quirks: quoted fields containing a literal tab/newline (parsed with `csv`, not naive line-splitting), HTML tags and entities, and a combined `kanji（reading）` field, which is split so matching can prefer the exact kanji spelling and only fall back to the reading for genuinely kana-only entries — an ambiguous reading shared by two different kanji spellings (e.g. 空く vs 開く, both あく) is skipped rather than guessed at. Every CJK character encountered is recorded as known (`pre_n3` coverage) regardless of match. `backend/seed/anki_collection_export.tsv` is the real committed baseline (Gabriel's full Anki collection export — kanji, vocab, verbs, adjectives, adverbs, grammar); re-upload it any time the DB is reset. Safe to skip entirely if the class hasn't covered anything yet — everything just starts as `available`. |

Each upload reports rows parsed, rows inserted/updated/skipped, and any
per-row warnings (e.g. an unparseable line) inline — nothing fails silently.

Below the uploads, three **enrichment** jobs backfill data the source files
don't already have, by calling Jisho.org and the KanjiVG project:

- **Kanji stroke data (KanjiVG)** — stroke-order SVG paths for the kanji
  PDF.
- **Kanji meanings/readings (Jisho)** — on'yomi/kun'yomi readings and
  meanings for kanji missing them.
- **Vocab word meanings (Jisho)** — fills in meaning/part-of-speech for
  vocab rows that came in with a blank meaning.

Each job shows a live progress bar (`completed/total`, polled every 1.5s)
and is safe to re-run — already-enriched rows are skipped. If a job
finishes with items Jisho or KanjiVG had no data for, a warning banner
reports how many, so those rows can be reviewed instead of silently staying
blank.

A frequency signal from a BCCWJ corpus subset (used only as a tie-break
during word selection — shorter/more common words are preferred) is loaded
automatically from `backend/seed/` and needs no import step.

### 2. Dashboard

- **Study config** — set the start date, new-card weeks, review-buffer
  weeks, and daily minimum word count, then Save. This must be set before
  generating batches; a `not set` pill shows if it's missing.
- **Overview** — total vocab, how many are still available vs. assigned to
  a batch vs. already marked seen-in-class, how many weeks remain, and an
  on-pace/behind-pace indicator — flips to "behind pace" once any generated
  batch needed more than `daily_minimum × 7` words to hit its weekly target.
- **Batch history** — every batch generated so far, its status
  (draft/finalized), the weekly target used, and word count.

### 3. Batch Review

Enter a batch number and click **Generate draft** to run the selection
algorithm for that week (uses target kanji from the schedule, current
vocab availability, and the skip-ahead/coverage rules above). The result
panel shows the weekly target used, how many words were selected, and
whether the batch is behind pace, plus any warnings — most notably a
target kanji with **zero eligible covering words**, which needs a manual
fix (e.g. importing more vocab) rather than disappearing unnoticed.

Once a draft exists:

- **Target kanji coverage** — every target kanji for the week as a chip,
  with how many selected words cover it; an uncovered chip (0 words) is
  visually flagged.
- **Word list** — every selected word, tagged target-linked (with which
  kanji it covers) or filler. While the batch is a draft you can:
  - toggle the **reading card** checkbox per word (overrides the automatic
    `needs_kanji_reading` result)
  - **swap** a word for another eligible candidate
  - **remove** a word
  - **add** a word from the eligible-replacement pool via the dropdown
- **Regenerate draft** re-runs selection from scratch (only available while
  still a draft).
- **Finalize** locks the batch, marks its words `assigned`, and records
  kanji coverage for future skip-ahead checks. **Un-finalize** reverses
  that — the batch goes back to draft and its words become available
  again (used if a mistake was found post-finalization).

### 4. Export

Works on any batch, though it's meant for finalized ones:

- **Vocab Anki deck** — download as one combined TSV, or check "split by
  part of speech" for separate `verbs.tsv` / `adjectives.tsv` /
  `adverbs.tsv` / `general_vocab.tsv` files. Each row is
  `front\tback\ttags`, e.g. `例えば（たとえば）\tfor example\tjlpt::n3
  source::n3_supplement`; kana-only words use just the kana as the front
  (no redundant reading in parentheses).
- **Kanji reading deck** — one TSV, `kanji\treading\ttags`, containing only
  the words flagged `needs_kanji_reading` in that batch.
- **Weekly kanji PDF** — one page per target kanji: stroke-order diagram
  (from KanjiVG), on'yomi/kun'yomi readings, and meanings, for handing out
  or presenting in class. **Check for missing data** first — it lists any
  target kanji still missing stroke data or reading/meaning enrichment
  before you download, so a gap in the PDF isn't a surprise mid-lesson.

## API

Every screen is a thin client over a JSON API mounted under `/api`; the
full interactive schema is at `http://127.0.0.1:8000/docs` (Swagger UI)
whenever the backend is running.

| Area | Routes |
|---|---|
| Import | `POST /api/imports/vocab-list`, `/kanji-schedule`, `/anki-export`; `POST /api/imports/enrich/{vocab-words,kanji-meanings,kanjivg}`; `GET /api/imports/jobs/{id}` |
| Batches | `POST /api/batches/{n}/generate`; `GET /api/batches/{n}`; `GET /api/batches/{n}/eligible-replacements`; `POST/DELETE /api/batches/{n}/words/{vocab_id}`; `POST .../toggle-reading`, `.../swap`; `POST /api/batches/{n}/finalize`, `/unfinalize` |
| Export | `GET /api/exports/{n}/vocab-tsv`, `/kanji-tsv`, `/pdf`, `/pdf/warnings` |
| Config | `GET/PUT /api/config` |
| Dashboard | `GET /api/dashboard/overview` |

## Development

```bash
cd backend
uv run pytest           # full backend test suite
uv run pytest -q tests/test_selection   # just the selection-algorithm tests
```

Selection logic (`app/selection/`) and card formatting (`app/export/`) are
pure functions with no DB access, tested directly against fixtures; a
`services/` layer wires them to the real database. Enrichment tests mock
Jisho/KanjiVG over HTTP (`respx`) — no live network calls in the suite.

Config is environment-overridable via `DISPATCHER_`-prefixed variables
(see `backend/app/config.py`), e.g. `DISPATCHER_DATABASE_URL`.

## Repository layout

```
backend/
  app/            FastAPI app: models, ingestion parsers, enrichment
                   clients, the selection algorithm, export formatters,
                   routers, services
  seed/            Committed source data: N3 vocab list, kanji schedule,
                   and a filtered BCCWJ frequency subset
  scripts/         One-off data-prep scripts
  tests/
frontend/
  src/
    pages/         The four screens (Import, Batch Review, Export, Dashboard)
    api/           Typed fetch wrappers, one module per router
```
