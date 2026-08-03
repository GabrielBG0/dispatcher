"""Tkinter GUI. Flow: pick old/new files + deck -> Compare (runs the pure
diff in matching.py, then resolves each candidate against the live Anki
collection via AnkiConnect) -> review/edit the proposed changes in a table ->
Apply. Nothing touches Anki until Apply is clicked.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from anki_patch_tool.ankiconnect import AnkiConnectClient, AnkiConnectError, AnkiNote
from anki_patch_tool.matching import MatchResult, diff_rows
from anki_patch_tool.parser import Row, parse_export_tsv

AUTO_MODEL = "Auto (detect from deck)"

ANKICONNECT_INSTALL_MSG = (
    "Could not connect to Anki.\n\n"
    "1. Make sure Anki is open.\n"
    "2. Install the AnkiConnect add-on: in Anki, go to "
    "Tools → Add-ons → Get Add-ons..., paste in the code 2055492159, "
    "then restart Anki.\n"
    "3. Click 'Retry connection' below."
)


def _default_browse_dir() -> str:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "comparation files"
        if candidate.is_dir():
            return str(candidate)
    return str(Path.home())


def _guess_deck_name(filename: str) -> str:
    stem = Path(filename).stem
    for suffix in ("_old", "_new"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _source_tag_query(rows_a: list[Row], rows_b: list[Row]) -> str | None:
    """Scopes the collection-wide Anki search to whatever "source::..." tag
    these export rows carry (e.g. "source::n3_supplement"), instead of
    searching the user's *entire* collection. Cards move between decks as
    words get reclassified (e.g. an adverb turning out to be an adjective),
    so matching must look across all decks -- but should stay confined to
    this managed study-group set, not accidentally match unrelated notes
    elsewhere in the user's collection that happen to share field text.
    Falls back to no scoping if the files don't carry such a tag.
    """
    tags: set[str] = set()
    for row in rows_a + rows_b:
        tags.update(row.tags.split())
    source_tags = sorted(t for t in tags if t.startswith("source::"))
    if not source_tags:
        return None
    return " OR ".join(f'tag:"{t}"' for t in source_tags)


@dataclass
class ReviewItem:
    match: MatchResult
    accepted: bool
    chosen_new: Row | None
    note: AnkiNote | None = None
    note_status: str = ""  # "found" | "not_found" | "ambiguous" | "elsewhere" | "" (n/a for add)
    candidate_notes: list[AnkiNote] = field(default_factory=list)
    # Set when the matched note already lives in a different deck than the
    # one being processed -- e.g. a word reclassified from adverb to
    # adjective. For update/add actions this means "also move the card
    # there"; for delete actions it means "don't delete, it just moved".
    deck_move_from: str | None = None
    # True once the user has explicitly gone through ResolveDialog for this
    # row (whichever way they decided). A row with runner-up candidates
    # that's still accepted but hasn't been through this is a "best guess"
    # that Apply must not silently act on -- see needs_attention().
    resolved: bool = False

    @property
    def action(self) -> str:
        return self.match.action

    def can_apply(self) -> bool:
        if self.action == "add":
            return self.note_status not in ("ambiguous", "already_exists")
        if self.action == "delete":
            return self.note is not None and self.note_status != "elsewhere"
        return self.note is not None

    def needs_attention(self) -> bool:
        """True when this row would currently be acted on by Apply but is
        still just a best guess -- it has runner-up candidates (a fuzzy
        update/delete match) that the user hasn't explicitly confirmed or
        overridden yet."""
        return self.accepted and bool(self.match.candidates) and not self.resolved


def _mark_deck_move(item: ReviewItem, deck: str) -> None:
    note = item.note
    if note is None or not note.deck_names or deck in note.deck_names:
        return
    other_deck = note.deck_names[0]
    if item.action == "delete":
        # Not stale -- it already lives elsewhere, most likely because it was
        # reclassified into a different deck. Don't delete it.
        item.note_status = "elsewhere"
        item.accepted = False
    item.deck_move_from = other_deck


def resolve_against_anki(matches: list[MatchResult], notes: list[AnkiNote], deck: str) -> list[ReviewItem]:
    """Cross-checks the file-level diff against the live Anki collection,
    turning each MatchResult into a ReviewItem with a resolved (or
    not-found/ambiguous) note.

    Matched primarily by the exact (front, back) pair currently sitting on a
    note -- precise, since it's literally "the note as it exists right now"
    -- falling back to front-only search only on a miss. Decks can
    legitimately reuse the same front across multiple distinct notes (e.g. a
    kanji with more than one valid reading), so a front-only match is claimed
    into `claimed_note_ids` and excluded from every other row's search this
    same run -- otherwise two different rows (say, an "unchanged" row and an
    "add" row sharing a front) could both resolve to the same note and the
    second one would silently clobber what the first one correctly left
    alone. `unchanged` matches are resolved too (to claim their note) even
    though they never produce a visible row.
    """
    notes_by_pair: dict[tuple[str, str], list[AnkiNote]] = {}
    notes_by_front0: dict[str, list[AnkiNote]] = {}
    for note in notes:
        front, back = note.field_value(0) or "", note.field_value(1) or ""
        notes_by_pair.setdefault((front, back), []).append(note)
        notes_by_front0.setdefault(front, []).append(note)

    claimed_note_ids: set[int] = set()
    items: list[ReviewItem] = []
    add_matches: list[MatchResult] = []

    for m in matches:
        if m.action == "add":
            add_matches.append(m)
            continue

        if m.action == "unchanged":
            exact = notes_by_pair.get((m.old.front, m.old.back), [])
            if len(exact) == 1:
                claimed_note_ids.add(exact[0].note_id)
            continue

        item = ReviewItem(match=m, accepted=(m.action != "delete"), chosen_new=m.new)
        exact = notes_by_pair.get((m.old.front, m.old.back), [])
        if len(exact) == 1:
            item.note, item.note_status = exact[0], "found"
        else:
            candidates = notes_by_front0.get(m.old.front, [])
            if len(candidates) == 1:
                item.note, item.note_status = candidates[0], "found"
            elif not candidates:
                item.note, item.note_status = None, "not_found"
                item.accepted = False
            else:
                item.note, item.note_status, item.candidate_notes = None, "ambiguous", candidates
                item.accepted = False
        if item.note is not None:
            claimed_note_ids.add(item.note.note_id)
            _mark_deck_move(item, deck)
        items.append(item)

    for m in add_matches:
        # The file diff only knows this word wasn't in the *old* file -- it
        # may still already exist in the live Anki collection, e.g. under a
        # different deck (reclassified) or outside what this "old" file
        # covers. Check before assuming it's really new.
        exact = notes_by_pair.get((m.new.front, m.new.back), [])
        if exact:
            # Already exists verbatim -- nothing to do, not add, not update.
            items.append(
                ReviewItem(match=m, accepted=False, chosen_new=m.new, note=exact[0], note_status="already_exists")
            )
            continue

        candidates = [n for n in notes_by_front0.get(m.new.front, []) if n.note_id not in claimed_note_ids]
        if len(candidates) == 1:
            note = candidates[0]
            synthetic_old = Row(front=note.field_value(0) or "", back=note.field_value(1) or "", tags="")
            converted = MatchResult(
                "update", synthetic_old, m.new, 1.0,
                "already exists in your Anki collection (matched by front text) -- "
                "treating as an update instead of adding a duplicate",
            )
            item = ReviewItem(match=converted, accepted=True, chosen_new=m.new, note=note, note_status="found")
            claimed_note_ids.add(note.note_id)
            _mark_deck_move(item, deck)
            items.append(item)
        elif len(candidates) > 1:
            items.append(
                ReviewItem(
                    match=m, accepted=False, chosen_new=m.new,
                    note_status="ambiguous", candidate_notes=candidates,
                )
            )
        else:
            items.append(ReviewItem(match=m, accepted=True, chosen_new=m.new))

    return items


class ResolveDialog(tk.Toplevel):
    """Lets the user pick a runner-up candidate, or mark the row for deletion,
    when the automatic match wasn't a clean exact-front hit.
    """

    def __init__(self, parent: tk.Widget, item: ReviewItem):
        super().__init__(parent)
        self.title("Resolve match")
        self.result: tuple[str, Row | None] | None = None  # ("update", row) or ("delete", None)
        self.geometry("560x360")
        self.transient(parent)
        self.grab_set()

        old = item.match.old
        ttk.Label(
            self, text=f"Old card:\n{old.front}\n{old.back}", justify="left", wraplength=520
        ).pack(anchor="w", padx=10, pady=(10, 6))
        ttk.Label(self, text=f"Reason: {item.match.reason}", wraplength=520, foreground="#555").pack(
            anchor="w", padx=10
        )

        ttk.Label(self, text="Pick the matching entry in the new file:").pack(anchor="w", padx=10, pady=(10, 2))
        self.listbox = tk.Listbox(self, height=8)
        candidates = item.match.candidates or ([item.match.new] if item.match.new else [])
        self.candidates = candidates
        for c in candidates:
            self.listbox.insert(tk.END, f"{c.front}  |  {c.back}"[:120])
        self.listbox.pack(fill="both", expand=True, padx=10)
        if item.chosen_new in candidates:
            self.listbox.selection_set(candidates.index(item.chosen_new))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=10)
        ttk.Button(btns, text="Use selected match", command=self._use_selected).pack(side="left")
        ttk.Button(btns, text="No match — delete old card instead", command=self._mark_delete).pack(
            side="left", padx=8
        )
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right")

    def _use_selected(self) -> None:
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Resolve match", "Select an entry first.", parent=self)
            return
        self.result = ("update", self.candidates[sel[0]])
        self.destroy()

    def _mark_delete(self) -> None:
        self.result = ("delete", None)
        self.destroy()


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Anki Patch Applier")
        self.geometry("980x640")

        self.client = AnkiConnectClient()
        self.items: list[ReviewItem] = []
        self.row_id_to_item: dict[str, ReviewItem] = {}

        self._build_widgets()
        self._check_connection()

    # -- layout -----------------------------------------------------------
    def _build_widgets(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        self.status_var = tk.StringVar(value="Checking connection to Anki...")
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(top, text="Retry connection", command=self._check_connection).grid(row=0, column=3, sticky="e")

        self.old_path_var = tk.StringVar()
        self.new_path_var = tk.StringVar()
        self.deck_var = tk.StringVar()
        self.model_var = tk.StringVar(value=AUTO_MODEL)

        ttk.Label(top, text="Old file:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(top, textvariable=self.old_path_var, width=60).grid(row=1, column=1, columnspan=2, sticky="we", pady=(8, 0))
        ttk.Button(top, text="Browse...", command=self._pick_old).grid(row=1, column=3, pady=(8, 0))

        ttk.Label(top, text="New file:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(top, textvariable=self.new_path_var, width=60).grid(row=2, column=1, columnspan=2, sticky="we", pady=4)
        ttk.Button(top, text="Browse...", command=self._pick_new).grid(row=2, column=3, pady=4)

        ttk.Label(top, text="Deck:").grid(row=3, column=0, sticky="w")
        self.deck_combo = ttk.Combobox(top, textvariable=self.deck_var, width=40)
        self.deck_combo.grid(row=3, column=1, sticky="w")

        ttk.Label(top, text="Note type for new cards:").grid(row=3, column=2, sticky="e")
        self.model_combo = ttk.Combobox(top, textvariable=self.model_var, width=25)
        self.model_combo.grid(row=3, column=3, sticky="w")

        ttk.Button(top, text="Compare", command=self._compare).grid(row=4, column=3, sticky="e", pady=(10, 0))
        top.columnconfigure(1, weight=1)

        # -- review table --
        table_frame = ttk.Frame(self, padding=(10, 0))
        table_frame.pack(fill="both", expand=True)

        columns = ("use", "action", "old_front", "old_back", "new_front", "new_back", "anki")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "use": "Use?",
            "action": "Action",
            "old_front": "Old Front",
            "old_back": "Old Back",
            "new_front": "New Front",
            "new_back": "New Back",
            "anki": "Anki note",
        }
        widths = {"use": 50, "action": 70, "old_front": 130, "old_back": 220, "new_front": 130, "new_back": 220, "anki": 140}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        # -- bottom bar --
        bottom = ttk.Frame(self, padding=10)
        bottom.pack(fill="x")
        self.summary_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.summary_var).pack(side="left")
        self.apply_btn = ttk.Button(bottom, text="Apply", command=self._apply, state="disabled")
        self.apply_btn.pack(side="right")

        self.log = tk.Text(self, height=8, state="disabled")
        self.log.pack(fill="x", padx=10, pady=(0, 10))

    # -- connection ---------------------------------------------------------
    def _check_connection(self) -> None:
        if self.client.ping():
            self.status_var.set("Connected to Anki ✓")
            try:
                self.deck_combo["values"] = self.client.deck_names()
                self.model_combo["values"] = [AUTO_MODEL, *self.client.model_names()]
            except AnkiConnectError:
                pass
        else:
            self.status_var.set("Not connected to Anki.")
            messagebox.showwarning("Not connected", ANKICONNECT_INSTALL_MSG)

    # -- file pickers ---------------------------------------------------------
    def _pick_old(self) -> None:
        path = filedialog.askopenfilename(initialdir=_default_browse_dir(), filetypes=[("TSV files", "*.tsv"), ("All files", "*.*")])
        if path:
            self.old_path_var.set(path)
            if not self.deck_var.get():
                self.deck_var.set(_guess_deck_name(path))

    def _pick_new(self) -> None:
        path = filedialog.askopenfilename(initialdir=_default_browse_dir(), filetypes=[("TSV files", "*.tsv"), ("All files", "*.*")])
        if path:
            self.new_path_var.set(path)
            if not self.deck_var.get():
                self.deck_var.set(_guess_deck_name(path))

    # -- compare ---------------------------------------------------------
    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, message + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _compare(self) -> None:
        old_path, new_path, deck = self.old_path_var.get(), self.new_path_var.get(), self.deck_var.get().strip()
        if not old_path or not new_path:
            messagebox.showerror("Missing files", "Pick both the old file and the new file first.")
            return
        if not deck:
            messagebox.showerror("Missing deck", "Enter or pick the deck name these cards live in.")
            return

        try:
            old_rows = parse_export_tsv(old_path)
            new_rows = parse_export_tsv(new_path)
        except OSError as exc:
            messagebox.showerror("Could not read file", str(exc))
            return

        matches = diff_rows(old_rows, new_rows)

        collection_notes: list[AnkiNote] = []
        if self.client.ping():
            scope_query = _source_tag_query(old_rows, new_rows) or "deck:*"
            try:
                collection_notes = self.client.notes_matching(scope_query)
            except AnkiConnectError as exc:
                messagebox.showwarning("Anki lookup failed", str(exc))

        self.items = resolve_against_anki(matches, collection_notes, deck)
        self._refresh_table()
        n_unchanged = sum(1 for m in matches if m.action == "unchanged")
        self._log(
            f"Compared {len(old_rows)} old rows against {len(new_rows)} new rows: "
            f"{n_unchanged} unchanged, {len(self.items)} rows need review."
        )
        self.apply_btn.configure(state="normal" if self.items else "disabled")

    # -- table rendering ---------------------------------------------------------
    def _row_values(self, item: ReviewItem) -> tuple:
        use = "[x]" if item.accepted else "[ ]"
        old_front = item.match.old.front if item.match.old else ""
        old_back = item.match.old.back if item.match.old else ""
        new_front = item.chosen_new.front if item.chosen_new else ""
        new_back = item.chosen_new.back if item.chosen_new else ""
        deck = self.deck_var.get().strip()
        if item.note_status == "found":
            anki = f"note {item.note.note_id}"
            if item.deck_move_from:
                anki += f" (move '{item.deck_move_from}' → '{deck}')"
        elif item.note_status == "not_found":
            anki = "not found — skipped"
        elif item.note_status == "ambiguous":
            anki = f"{len(item.candidate_notes)} matches — skipped"
        elif item.note_status == "elsewhere":
            anki = f"already in '{item.deck_move_from}' — no action needed"
        elif item.note_status == "already_exists":
            anki = "already exists — no action needed"
        elif item.action == "add":
            anki = "(new card)"
        else:
            anki = ""
        return (use, item.action, old_front, old_back, new_front, new_back, anki)

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.row_id_to_item.clear()
        counts = {"update": 0, "delete": 0, "add": 0}
        for item in self.items:
            row_id = self.tree.insert("", tk.END, values=self._row_values(item))
            self.row_id_to_item[row_id] = item
            if item.accepted:
                counts[item.action] = counts.get(item.action, 0) + 1
        self.summary_var.set(
            f"{counts.get('update', 0)} to update, {counts.get('add', 0)} to add, "
            f"{counts.get('delete', 0)} to delete (unchecked by default)."
        )

    def _on_tree_click(self, event: tk.Event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        row_id = self.tree.identify_row(event.y)
        if not row_id or col != "#1":  # "use" is the first column
            return
        item = self.row_id_to_item.get(row_id)
        if item is None or not item.can_apply():
            return
        item.accepted = not item.accepted
        self.tree.item(row_id, values=self._row_values(item))
        self._refresh_summary_only()

    def _refresh_summary_only(self) -> None:
        counts = {"update": 0, "delete": 0, "add": 0}
        for item in self.items:
            if item.accepted:
                counts[item.action] = counts.get(item.action, 0) + 1
        self.summary_var.set(
            f"{counts.get('update', 0)} to update, {counts.get('add', 0)} to add, "
            f"{counts.get('delete', 0)} to delete (unchecked by default)."
        )

    def _apply_resolve_result(
        self, item: ReviewItem, result: tuple[str, Row | None] | None, on_cancel_uncheck: bool
    ) -> None:
        if result is None:
            if on_cancel_uncheck:
                item.accepted = False
            return
        action, chosen = result
        if action == "delete":
            item.match = MatchResult("delete", item.match.old, None, 0.0, "manually marked for deletion")
            item.chosen_new = None
            item.accepted = False
        else:
            item.match = MatchResult("update", item.match.old, chosen, 1.0, "manually confirmed")
            item.chosen_new = chosen
            item.accepted = True
        item.resolved = True

    def _on_tree_double_click(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        item = self.row_id_to_item.get(row_id)
        if item is None or item.action == "add" or not item.match.candidates:
            return
        dialog = ResolveDialog(self, item)
        self.wait_window(dialog)
        self._apply_resolve_result(item, dialog.result, on_cancel_uncheck=False)
        self.tree.item(row_id, values=self._row_values(item))
        self._refresh_summary_only()

    def _resolve_pending_before_apply(self) -> None:
        """Cards with runner-up candidates that are still just a best guess
        (not yet confirmed via ResolveDialog) must be resolved before Apply
        acts on them -- pop each one up for the user now instead of silently
        applying a guess or silently skipping it."""
        pending = [i for i in self.items if i.needs_attention()]
        if not pending:
            return
        self._log(f"{len(pending)} change(s) need confirmation before applying...")
        for item in pending:
            dialog = ResolveDialog(self, item)
            self.wait_window(dialog)
            self._apply_resolve_result(item, dialog.result, on_cancel_uncheck=True)
        self._refresh_table()

    # -- apply ---------------------------------------------------------
    def _apply(self) -> None:
        self._resolve_pending_before_apply()

        to_delete = [i for i in self.items if i.action == "delete" and i.accepted and i.note]
        to_update = [i for i in self.items if i.action == "update" and i.accepted and i.note]
        to_add = [i for i in self.items if i.action == "add" and i.accepted]

        if not (to_delete or to_update or to_add):
            messagebox.showinfo("Nothing to do", "No changes are checked.")
            return

        if to_delete:
            if not messagebox.askyesno(
                "Confirm deletion",
                f"This will permanently delete {len(to_delete)} Anki note(s), including their "
                "review history. This cannot be undone. Continue?",
            ):
                return

        deck = self.deck_var.get().strip()
        model_name = self.model_var.get().strip()
        if to_add and (not model_name or model_name == AUTO_MODEL):
            try:
                existing = self.client.notes_in_deck(deck)
            except AnkiConnectError as exc:
                messagebox.showerror("Anki lookup failed", str(exc))
                return
            if not existing:
                messagebox.showerror(
                    "Missing note type",
                    f"'{deck}' has no existing cards to auto-detect a note type from — "
                    "pick a specific note type first.",
                )
                return
            model_name = existing[0].model_name
            self._log(f"Auto-detected note type '{model_name}' from existing cards in '{deck}'.")
        updated = deleted = added = failed = 0

        for item in to_update:
            try:
                note = item.note
                field_names = note.field_names[:2]
                self.client.update_note_fields(note.note_id, field_names, [item.chosen_new.front, item.chosen_new.back])
                if item.deck_move_from:
                    self.client.change_deck(note.card_ids, deck)
                    self._log(f"Moved {item.chosen_new.front} from '{item.deck_move_from}' to '{deck}'")
                updated += 1
            except AnkiConnectError as exc:
                failed += 1
                self._log(f"FAILED update {item.match.old.front}: {exc}")

        if to_delete:
            try:
                self.client.delete_notes([i.note.note_id for i in to_delete])
                deleted += len(to_delete)
            except AnkiConnectError as exc:
                failed += len(to_delete)
                self._log(f"FAILED delete: {exc}")

        if to_add:
            try:
                field_names = self.client.invoke("modelFieldNames", modelName=model_name)[:2]
            except AnkiConnectError as exc:
                messagebox.showerror("Note type error", str(exc))
                field_names = ["Front", "Back"]
            for item in to_add:
                row = item.chosen_new
                tags = row.tags.split() if row.tags else []
                try:
                    self.client.add_note(deck, model_name, field_names, [row.front, row.back], tags)
                    added += 1
                except AnkiConnectError as exc:
                    failed += 1
                    self._log(f"FAILED add {row.front}: {exc}")

        self._log(f"Done: {updated} updated, {added} added, {deleted} deleted, {failed} failed.")
        messagebox.showinfo("Done", f"{updated} updated, {added} added, {deleted} deleted, {failed} failed.\nSee log for details.")


def run() -> None:
    App().mainloop()
