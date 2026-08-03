"""Thin client for the AnkiConnect addon's local HTTP API
(https://foosoft.net/projects/anki-connect/). Anki must be running with the
AnkiConnect addon installed for any of this to work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_URL = "http://127.0.0.1:8765"
ANKICONNECT_VERSION = 6


class AnkiConnectError(Exception):
    pass


@dataclass
class AnkiNote:
    note_id: int
    model_name: str
    # field name -> value, in the note type's declared field order
    fields: dict[str, str]
    # deck(s) the note's card(s) currently live in -- almost always one deck,
    # since these decks use a plain 2-field note type with a single card.
    deck_names: list[str] = field(default_factory=list)
    card_ids: list[int] = field(default_factory=list)

    @property
    def field_names(self) -> list[str]:
        return list(self.fields.keys())

    def field_value(self, position: int) -> str | None:
        names = self.field_names
        if position >= len(names):
            return None
        return self.fields[names[position]]


class AnkiConnectClient:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 10.0) -> None:
        self._url = url
        self._timeout = timeout

    def invoke(self, action: str, **params: Any) -> Any:
        payload = {"action": action, "version": ANKICONNECT_VERSION, "params": params}
        try:
            resp = httpx.post(self._url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise AnkiConnectError(
                f"Could not reach AnkiConnect at {self._url}. "
                "Make sure Anki is open and the AnkiConnect add-on is installed."
            ) from exc
        data = resp.json()
        if data.get("error"):
            raise AnkiConnectError(str(data["error"]))
        return data.get("result")

    def ping(self) -> bool:
        try:
            version = self.invoke("version")
        except AnkiConnectError:
            return False
        return isinstance(version, int)

    def deck_names(self) -> list[str]:
        return self.invoke("deckNames")

    def model_names(self) -> list[str]:
        return self.invoke("modelNames")

    def _notes_for_query(self, query: str) -> list[AnkiNote]:
        note_ids = self.invoke("findNotes", query=query)
        if not note_ids:
            return []
        infos = self.invoke("notesInfo", notes=note_ids)

        all_card_ids = [cid for info in infos for cid in info.get("cards", [])]
        deck_by_card: dict[int, str] = {}
        if all_card_ids:
            cards_info = self.invoke("cardsInfo", cards=all_card_ids)
            deck_by_card = {c["cardId"]: c["deckName"] for c in cards_info}

        notes: list[AnkiNote] = []
        for info in infos:
            ordered_fields = sorted(info["fields"].items(), key=lambda kv: kv[1]["order"])
            card_ids = info.get("cards", [])
            deck_names = sorted({deck_by_card[cid] for cid in card_ids if cid in deck_by_card})
            notes.append(
                AnkiNote(
                    note_id=info["noteId"],
                    model_name=info["modelName"],
                    fields={name: data["value"] for name, data in ordered_fields},
                    deck_names=deck_names,
                    card_ids=card_ids,
                )
            )
        return notes

    def notes_in_deck(self, deck: str) -> list[AnkiNote]:
        return self._notes_for_query(f'deck:"{deck}"')

    def notes_matching(self, query: str) -> list[AnkiNote]:
        return self._notes_for_query(query)

    def all_notes(self) -> list[AnkiNote]:
        """All notes in the whole collection, regardless of deck. Anki's own
        duplicate detection on addNote is collection-wide per note type (not
        scoped to a deck), so duplicate/update checks here must match that
        scope or a word sitting in a different deck looks like a false "new
        word" until addNote rejects it as a duplicate.
        """
        return self._notes_for_query("deck:*")

    def change_deck(self, card_ids: list[int], deck: str) -> None:
        if not card_ids:
            return
        self.invoke("changeDeck", cards=card_ids, deck=deck)

    def update_note_fields(self, note_id: int, field_names: list[str], values: list[str]) -> None:
        fields = {name: value for name, value in zip(field_names, values)}
        self.invoke("updateNoteFields", note={"id": note_id, "fields": fields})

    def delete_notes(self, note_ids: list[int]) -> None:
        if not note_ids:
            return
        self.invoke("deleteNotes", notes=note_ids)

    def add_note(self, deck: str, model_name: str, field_names: list[str], values: list[str], tags: list[str]) -> int:
        fields = {name: value for name, value in zip(field_names, values)}
        return self.invoke(
            "addNote",
            note={
                "deckName": deck,
                "modelName": model_name,
                "fields": fields,
                "tags": tags,
            },
        )
