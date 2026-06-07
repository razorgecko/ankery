"""Engine-shipped neutral assets: the catch-all note, the prompt templates, and
the fallback card styling.

Pack-shaped but not a pack — no `pack.toml`, no `[category]`, never selected. The
loaders below skip category parsing, so these load without going through the pack
category contract.
"""

import functools
from pathlib import Path

from ankery.notedef import NoteDefinition, load_notes_from_dir

class DefaultsError(Exception):
    """Raised when the engine-shipped defaults home is incomplete or malformed."""


DEFAULTS_DIR = Path(__file__).parent
STYLE_PATH = DEFAULTS_DIR / "style.css"
NOTES_DIR = DEFAULTS_DIR / "notes"
PROMPTS_DIR = DEFAULTS_DIR / "prompts"
SYSTEM_TEMPLATE_PATH = PROMPTS_DIR / "system.j2"
USER_TEMPLATE_PATH = PROMPTS_DIR / "user.j2"


def default_style() -> str:
    """The fallback card styling used when a pack ships no style.css of its own."""
    return STYLE_PATH.read_text("utf-8")


def default_system_template() -> str:
    """The engine-shipped system-prompt template (Jinja)."""
    return SYSTEM_TEMPLATE_PATH.read_text("utf-8")


def default_user_template() -> str:
    """The engine-shipped user-turn template (Jinja)."""
    return USER_TEMPLATE_PATH.read_text("utf-8")


def default_catch_all() -> NoteDefinition:
    """The single neutral catch-all note.

    A packaging regression that drops or duplicates it fails here rather than at
    run time.
    """
    notes = load_notes_from_dir(NOTES_DIR)
    if len(notes) != 1:
        raise DefaultsError(
            f"defaults/notes must hold exactly one catch-all note; found {len(notes)}."
        )
    return notes[0]


@functools.cache
def catch_all_model_name() -> str:
    """Name of the engine-owned catch-all model (e.g. "Ankery Basic"), read from
    the catch-all note asset.

    Cached because it feeds a dataclass field default, hit on every `Config()`
    construction.
    """
    return default_catch_all().name
