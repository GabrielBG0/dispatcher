# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

When running tests always verify is there is already a server running befor e starting a new one.

Whenever a new important pice of information (e.g., a new directive, whay of doing things, etc...) is dicussed on chat you should add it to this file so that future conversations with Claude Code will be aware of it.

## What this is

A local web app that builds a weekly N3 vocabulary/kanji study plan for a Japanese
study group. It takes a vocab master list and a per-week kanji teaching schedule,
selects the vocab words each week that teach that week's target kanji (respecting a
"skip-ahead" guard and an already-seen-in-class baseline), and exports Anki decks
plus a printable kanji reference PDF. Everything runs locally; the only external
calls are read-only lookups against Jisho.org and the KanjiVG project for
enrichment data.

Stack: FastAPI + SQLAlchemy + SQLite (backend), React + Vite + TypeScript (frontend).

See `README.md` for the full concepts glossary, screen-by-screen usage guide, and
API route table — it's kept up to date and is the primary reference; don't
duplicate it here.

## Commands

Backend uses `uv` exclusively — never system Python, pip, poetry, or venv.

```bash
cd backend
uv sync                              # install deps
uv run playwright install chromium   # one-time, needed for PDF export
uv run uvicorn app.main:app --reload --port 8000   # dev server
uv run pytest                        # full test suite
uv run pytest -q tests/test_selection            # one test dir
uv run pytest -q tests/test_selection/test_select_batch.py::test_name  # one test
```

Frontend uses npm:

```bash
cd frontend
npm install
npm run dev       # dev server on :5173, proxies /api/* to backend :8000
npm run build     # tsc -b && vite build -> frontend/dist
npm run lint      # oxlint
```

Production-style run (single process serves built UI + API from one origin):
build the frontend first, then run uvicorn from `backend/` — FastAPI mounts
`frontend/dist` with SPA fallback (see `SpaStaticFiles` in `app/main.py`).

The SQLite DB (`backend/data/dispatcher.db`) and KanjiVG stroke cache are created
automatically on first run; there are no migrations.

Config is environment-overridable via `DISPATCHER_`-prefixed env vars
(`backend/app/config.py`), e.g. `DISPATCHER_DATABASE_URL`.

## Architecture

### Layering (backend/app/)

- `selection/` and `export/card_formatter.py` — **pure functions, no DB access**.
  `selection/select_batch.py` is the core weekly word-picking algorithm; it takes
  plain data in (candidates, coverage, schedule, target kanji, a `SelectionConfig`,
  and an explicitly injected `today: date` — never `datetime.now()`) and returns a
  `SelectionResult`. This is what makes the pacing math deterministic and directly
  testable against fixtures without touching a database.
- `services/` — wires the pure logic to the real DB session (one service per
  domain: `batch_service`, `dashboard_service`, `dedupe_service`, `export_service`,
  `import_service`, `vocab_service`).
- `routers/` — thin FastAPI route handlers, one per API area (imports, vocab,
  kanji, batches, exports, config, dashboard), calling into `services/`.
- `ingestion/` — parsers for the three import formats (vocab `.xls`, kanji
  schedule `.xlsx`, Anki export `.tsv`/`.txt`) plus `upsert.py` for idempotent
  row insertion and `bccwj_frequency_loader.py` for the frequency tie-break data.
- `enrichment/` — Jisho.org and KanjiVG HTTP clients plus `jobs.py`, which runs
  and tracks progress for the four backfill jobs (kanji stroke data, kanji
  meanings/readings, vocab meanings, kana-only kanji forms).
- `models/` — SQLAlchemy models, one file per table.
- `schemas/` — Pydantic request/response models.

Tests mirror this structure under `backend/tests/` (`test_selection/`,
`test_export/`, `test_ingestion/`, `test_enrichment/`, `test_vocab/`,
`test_dashboard/`). Enrichment tests mock Jisho/KanjiVG over HTTP with `respx` —
no live network calls in the suite.

### Key domain rules (see README "Concepts" for full detail)

- A word is **target-linked** if it contains at least one of the batch's target
  kanji; otherwise it's filler used to round out the weekly target size.
- **Skip-ahead guard**: a word is only eligible if every kanji in it is a target
  this batch, already known (from a past finalized batch or the seen-in-class
  baseline), or itself a target this batch. Implemented via
  `selection/classify.py` (`classify_word_kanji`, `has_future_kanji`,
  `orphan_kanji_count`).
- **needs_kanji_reading**: true only when a word is target-linked _and_ has zero
  orphan kanji — otherwise the reading card would test an untaught character.
- **weekly_target**: `max(pacing_floor, ceil(remaining_words / remaining_weeks))`,
  where `pacing_floor = daily_minimum * 7`; computed by
  `select_batch.compute_weekly_target`.
- Batches are **draft** until finalized. Finalizing marks selected words
  `assigned` and records kanji coverage for future skip-ahead checks;
  un-finalizing reverses both.
- **Export row order**: the vocab and kanji-reading TSVs share one sort key
  (`export/vocab_tsv.py::study_order_key`) — `needs_kanji_reading` words
  first, then other target-linked words, then filler, alphabetically by
  hiragana reading within each tier — so the reading deck (which only ever
  contains `needs_kanji_reading` words) comes out as the leading run of the
  vocab deck on a fresh Anki import. Both exporters must keep using the same
  key or the decks drift out of sync.

### Frontend (frontend/src/)

- `pages/` — one file per screen: Import, Vocab Review, Dashboard, Batch Review,
  Export. Intended workflow order is Import → Vocab Review → Dashboard → Batch
  Review → Export, then Batch Review/Export weekly thereafter.
- `api/` — typed fetch wrappers, one module per backend router
  (`batches.ts`, `vocab.ts`, `imports.ts`, `exports.ts`, `dashboard.ts`,
  `config.ts`), all going through `client.ts`.

## Data files

`backend/seed/` holds committed source data: the N3 vocab list, the kanji
schedule, a BCCWJ frequency subset (loaded automatically, no import step), and
`anki_collection_export.tsv` (the real seen-in-class baseline — safe to
re-upload any time the DB is reset). `backend/data/` holds the runtime SQLite DB
and KanjiVG cache, both gitignored and regenerated on demand.
