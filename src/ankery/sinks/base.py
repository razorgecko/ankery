from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from ankery.notedef import NoteDefinition


class SinkError(Exception):
    """A sink failed to write a note.

    Covers transport failures (Anki not running, network error) and
    application-level failures the target reports in-band (AnkiConnect returns
    errors in the JSON body, not via HTTP status). The manager lets this
    propagate: unlike a provider miss, a sink failure means the card was not
    created and there is no fallback.
    """


@runtime_checkable
class AnkiSink(Protocol):
    """A destination that turns mapped note fields into a card.

    The seam between the manager and Anki. The only implementation now is
    AnkiConnect, but an offline .apkg writer (genanki) would slot in behind the
    same method later.
    """

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
        """Ensure each definition's note type exists in the target and matches.

        Create the model if absent; if present, require its field set and order
        to match exactly and raise SinkError otherwise — never silently mutate
        an existing model. Run once before writing notes.

        A created model whose definition sets no ``css`` is styled to match the
        ``catch_all`` model's own look when the target can report it, else
        `default_css`. A definition with its own ``css`` keeps it.
        """
        ...
