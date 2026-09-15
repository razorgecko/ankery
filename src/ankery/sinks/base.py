from collections.abc import Iterable
from typing import NamedTuple, Protocol, runtime_checkable

from ankery.notedef import NoteDefinition


class SinkError(Exception):
    """Failed to write a note (transport error or application-level error from the target)."""


class SyncResult(NamedTuple):
    """Outcome of syncing note types: the names created and each updated name
    mapped to the parts written."""

    created: list[str]
    updated: dict[str, list[str]]


@runtime_checkable
class AnkiSink(Protocol):
    def add_note(
        self,
        *,
        deck: str,
        note_type: str,
        fields: dict[str, str],
        tags: list[str] | None = None,
    ) -> int:
        """Create a note and return its Anki note id."""
        ...

    def verify_note_types(
        self,
        definitions: Iterable[NoteDefinition],
        *,
        default_css: str = "",
        catch_all: str | None = None,
    ) -> list[str]:
        """Create missing note types and return the names created; raise SinkError
        if an existing type has wrong fields."""
        ...

    def sync_note_types(
        self,
        definitions: Iterable[NoteDefinition],
        *,
        default_css: str = "",
    ) -> SyncResult:
        """Create missing note types and overwrite templates and styling of existing
        ones. Raise SinkError, before writing anything, if an existing type's fields
        or card types differ."""
        ...
