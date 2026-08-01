---
marp: true
theme: default
paginate: true
title: Dispatcher — How the Study Plan Gets Built
---

<!--
Template for presenting Dispatcher to friends/non-engineers.
Renders as slides with Marp (VS Code "Marp for VS Code" extension, or
`npx @marp-team/marp-cli docs/presentation.md -o deck.pdf`).
Also reads fine as plain markdown if you never touch Marp — each `---`
is just a section break.

Stats and examples are pulled live from the current dispatcher.db (as of
2026-08-01) — re-run the queries in this repo's data before presenting if
it's been a while, since Batch Review/Export keeps moving these numbers.
Trim slides that don't matter for your audience.
-->

# Dispatcher
### Turning a vocab list + a kanji schedule into a weekly Anki study plan

A local tool for the N3 study group — no cloud, no accounts, runs on
your own machine.

---

## The problem it solves

Studying for JLPT N3 needs two things lined up every week:

- **Which kanji** the class is learning this week
- **Which vocab words** actually use those kanji — so a new word never
  leans on a character nobody's learned yet

Doing this by hand every week — cross-referencing a spreadsheet against a
**4,667-word** vocab list — doesn't scale. Dispatcher automates the
matching and produces ready-to-import Anki decks + a printable kanji
handout.

---

## What comes out the other end

Every week, three files:

1. **Vocab Anki deck** (`.tsv`) — this week's new words, front/back cards
2. **Kanji reading deck** (`.tsv`) — dedicated reading drills for words
   whose kanji reading actually needs practicing
3. **Kanji reference PDF** — one page per new kanji: stroke order,
   readings, meanings — handed out or projected in class

All three are generated from the same underlying selection, so they stay
in sync with each other week to week.

---

## The pipeline, end to end

```
 source files                  the database                weekly output
┌───────────────┐        ┌─────────────────────┐       ┌──────────────────┐
│ N3 vocab list │        │                      │       │ Vocab Anki deck  │
│ Kanji schedule│──import─▶  vocab + kanji rows  │       │ Kanji reading    │
│ Anki export   │        │  (SQLite)            │──────▶│ deck             │
│ (baseline)    │        │                      │select │ Kanji PDF        │
└───────────────┘        └─────────┬────────────┘ batch └──────────────────┘
                                    │
                          enrichment (Jisho, KanjiVG)
                          fills in meanings, readings,
                          stroke data
```

Three stages, each a screen in the app: **Import → Batch Review → Export**
(with Vocab Review and a Dashboard alongside for cleanup/config).

---

## Stage 1 — Getting data into the DB

Three source files, each idempotent to import (re-upload any time, safe):

| File | What it gives us |
|---|---|
| **N3 vocab list** (`.xls`) | The word list itself — kanji, hiragana reading, meaning |
| **Kanji weekly schedule** (`.xlsx`) | Which kanji belong to which week |
| **Genki Anki export** (`.tsv`) | Everything the class has *already* seen — the "don't repeat, don't get ahead of this" baseline |

The Anki export is Gabriel's real, full Anki collection export — every
kanji, vocab word, verb, adjective, grammar point already studied gets
parsed out and recorded, so the tool knows what's fair game from day one.
Right now that baseline plus two finalized weeks add up to **563 words
marked seen-in-class** and **794 assigned** to a batch, out of 4,667 total.

---

## Stage 1 — Filling in the gaps (enrichment)

The raw vocab list doesn't come with everything we need. Four background
jobs call out to **Jisho.org** and the **KanjiVG** project (read-only, no
accounts) to backfill:

- Kanji **stroke-order data** — for the PDF diagrams
- Kanji **meanings & readings**
- Vocab word **meanings** (for rows that came in blank)
- Kanji spellings for words that were stored **kana-only** (そう → 僧)

Each job is safe to re-run — already-filled rows are skipped — and
reports anything it couldn't confidently match, so gaps get reviewed
instead of silently staying blank.

---

## Stage 1 — Cleaning duplicates

Turns out the shipped word list had ~1,100 duplicate rows (an accidental
second import baked into the source data). A dedup screen finds rows with
identical spelling **and overlapping meaning**, and lets you pick which
copy survives.

Words that just *look* alike but mean different things — 貸し "loan" vs
菓子 "pastry", both かし — are never flagged. Same spelling + unrelated
meaning = real homophone, not a duplicate.

---

## Stage 2 — The core idea: target-linked vs. filler

Every week has a set of **target kanji** (from the schedule). A vocab
word is:

- **target-linked** — it contains at least one of this week's target
  kanji → this is *why* it's in the deck
- **filler** — it doesn't touch any target kanji, but rounds the batch
  out to a healthy weekly word count

Filler exists because "only the words that teach new kanji" is often too
few words to hit a reasonable weekly pace.

**Real filler example from batch 1:** 頭 (あたま, "head") — none of its
kanji are targets that week, so it's not there to teach anything new,
just to help fill out the week's 266-word quota.

---

## Stage 2 — The skip-ahead guard

The rule that keeps the plan honest:

> A word is only eligible this week if **every** kanji in it is either
> — a target kanji this week,
> — already known (taught in a past week, or in the seen-in-class
>   baseline), **or**
> — itself a target kanji this week.

If a word needs *any* kanji from three weeks in the future, it's excluded
— full stop. No exceptions. This is what stops a "fun word" from
secretly requiring a character the class hasn't touched yet.

**Real example:** 具 (used in furniture, tools, equipment) is scheduled
for week 11. So even a common, everyday word like 家具 (かぐ, "furniture")
is excluded from every batch before then — no matter how useful it'd be
in week 1.

---

## Stage 2 — Does this word get a reading drill?

A word gets a dedicated **kanji reading card** only if:

1. it's target-linked (it teaches a kanji this week), **and**
2. it has **zero orphan kanji** — no character in it that's neither a
   target this week nor already known

Otherwise the reading card would be testing a character nobody's been
taught yet — worse than useless, actively confusing.

---

## Stage 2 — How many words per week?

```
pacing_floor   = daily_minimum × 7
weekly_target  = max(pacing_floor, ceil(remaining_words / remaining_weeks))
```

- `daily_minimum` is a config knob — currently set to **38 words/day**
- The second term auto-speeds-up the plan if we're behind schedule, so
  the whole list still finishes by the study end date
- If a batch needed *more* than the pacing floor to hit its target, the
  dashboard flags the plan as **behind pace**

**Our actual numbers:** `pacing_floor = 38 × 7 = 266`. Batches 1, 2, and
3 have each landed on a weekly target of exactly **266 words** — the
pacing floor is currently the binding constraint, so the plan is on pace.

---

## Stage 2 — Picking the actual words

For each target kanji, the algorithm needs at least one word covering it
(a **set-cover pass**), then tops up to the weekly target:

1. **Cover every target kanji** — cheapest, cleanest eligible word first;
   if nothing fresh covers it, fall back to a seen-in-class word (flagged
   with a warning) rather than leaving the kanji uncovered
2. **Reinforce** — keep adding more target-linked words until quota
3. **Fill** — top up with non-target-linked eligible words

Tie-break order when multiple words qualify: **fewer orphan kanji →
more frequent/shorter word → stable ID**. Common, simple words win over
obscure ones.

**Real example — batch 1, target kanji 兄:** two eligible words covered
it, 兄 (あに, "older brother") and お兄さん (おにいさん, "older
brother / young man"). The shorter, more common one — 兄 — was picked to
lead the coverage; both remain in the pool as candidates if you want to
swap.

---

## Stage 2 — What a warning looks like

If a target kanji has **zero eligible covering words**, that's not
silently dropped — it surfaces as a warning with a diagnosed cause:

- *no word in the source list even contains this kanji*
- *blocked by a future kanji* (and which one, and which week unblocks it)
- *the only candidates are already used elsewhere*

This is the signal that something needs a human — usually "go find more
vocab for this kanji."

---

## Stage 3 — Turning a selection into cards

Formatting is a pure function: `(kanji, reading, meaning) → front/back`.
Real examples pulled straight from batch 1:

**Vocab card:**
```
Front: 兄（あに）              Back: older brother / elder brother
```

**Kanji reading card** (only for words with zero orphan kanji):
```
Front: 兄                      Back: あに
```

**Kana-only word** (front is just the kana — no redundant reading):
```
Front: インタビュー            Back: interview (on television, in a
                                newspaper, etc.)
```

**"Usually kana" word** (kanji spelling exists but isn't the common
form, so the kana leads):
```
Front: かもしれない（かも知れない）    Back: may / might / perhaps
```

---

## Stage 3 — Keeping the two decks in sync

Both TSVs share **one sort order**:

`needs_kanji_reading words → other target-linked words → filler`
(alphabetical by reading within each group)

Since the kanji-reading deck is *only* `needs_kanji_reading` words, it
ends up as the exact leading run of the combined vocab deck. Studying
both decks at the same pace on a fresh import means you never see a
kanji-reading card before you've met that word's meaning card.

---

## Human review, every step of the way

Nothing here is fire-and-forget — a batch stays a **draft** until you
approve it:

- swap a word for another eligible one
- manually add/remove words
- toggle a reading card on/off
- **Finalize** locks it in and updates the "already known" record for
  future weeks — **Un-finalize** reverses that if a mistake slipped
  through

---

## The stack

- **Backend:** FastAPI + SQLAlchemy + SQLite — pure Python, no external
  services except read-only Jisho/KanjiVG lookups
- **Frontend:** React + Vite + TypeScript
- Runs entirely on one machine: `uv run uvicorn ...` + `npm run dev`,
  or a single production-style process serving both

The selection algorithm and card formatter are **pure functions** — no
database, no network — so the "what word gets picked" logic is directly
unit-tested against fixtures, independent of everything else.

---

## By the numbers

- **4,667** vocab words in the master list
- **959** kanji tracked, scheduled across **16** weekly batches
- **563** words already known from the Genki baseline import
- **794** words assigned across batches 1–3 (**2** finalized, **1** draft)
- **3,292** words still available, waiting to be picked
- **266** words/week at the current pace (daily minimum: 38)

*(live numbers as of this writing — re-check the Dashboard screen before
presenting, since Batch Review/Export keeps moving these)*

---

## Demo

Live walkthrough: **Batch Review for week 3** (currently a draft, target
266 words) → tweak a word or two → **Export** → open the resulting
`.tsv` in a text editor / import into Anki.

---

## Questions?

Thanks for listening — happy to dig into any part of the pipeline in
more detail: selection algorithm, enrichment, or the Anki deck format.
