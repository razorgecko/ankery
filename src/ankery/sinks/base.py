from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ankery.notedef import NoteDefinition


class SinkError(Exception):
    """Failed to write a note (transport error or application-level error from the target)."""


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
    ) -> None:
        """Create missing note types; raise SinkError if an existing type has wrong fields."""
        ...
