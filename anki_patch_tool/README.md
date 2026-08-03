# Anki Patch Applier

A small Windows desktop tool for the study group: reconciles cards you already
imported into Anki against a corrected export (meanings fixed, some card
fronts changed, some cards removed) using the [AnkiConnect](https://ankiweb.net/shared/info/2055492159)
addon, so you don't end up with stale or duplicate cards after the master
vocab/kanji lists get corrected.

It only touches your collection when you click **Apply** in the preview
screen -- nothing happens automatically.

## For end users (no Python needed)

1. Install the **AnkiConnect** addon in Anki: Tools -> Add-ons -> Get Add-ons...,
   paste in code `2055492159`, then restart Anki. Leave Anki open while you use
   this tool.
2. Double-click `AnkiPatchTool.exe`.
3. Pick the *old* file (what you originally imported) and the *new* file (the
   corrected one), confirm the deck name, and review the proposed changes.
4. Uncheck anything you don't want, then click **Apply**.

New words (only in the new file) are added; changed cards are updated in
place; cards removed from the new file are offered for deletion but
unchecked by default since deleting a card also discards its Anki review
history -- only check those you're sure about.

## For development

Backend-style Python project, managed with `uv` (same convention as
`backend/`) -- never system Python/pip/poetry/venv directly.

```bash
cd anki_patch_tool
uv sync
uv run pytest
uv run python -m anki_patch_tool.main   # launch the GUI locally
```

### Building the `.exe`

```bash
cd anki_patch_tool
uv run pyinstaller --onefile --windowed --name AnkiPatchTool src/anki_patch_tool/main.py
```

Produces `dist/AnkiPatchTool.exe` -- a single file with no external
dependencies (Python/Tk are bundled in). Copy that file anywhere and
double-click it; it does not need this repo present, except that the "pick a
file" dialogs default to `comparation files/` when run from inside the repo.
