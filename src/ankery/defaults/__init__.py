"""Engine-shipped neutral assets: the catch-all note, the prompt templates, and
the fallback card styling.

This directory is **pack-shaped but is not a pack**: it has no `pack.toml` and no
`[category]` routing dimension, and it is never selected by `source_language`. It
is the guaranteed terminus for every neutral slot — shipped in-package and
complete — so resolution of any slot is at most `pack-or-default` (the prompt adds
one more operator layer on top), never a third hard-coded fallback hiding in a
function.

It is loaded by the *subset* loaders below (style, notes, prompt templates) which
deliberately skip category parsing; loading it as a real pack would fail the
`[category]` contract, which is exactly why it lives here and not under `packs/`.
"""

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
    """The engine-shipped system-prompt chrome template (Jinja); the prompt terminus."""
    return SYSTEM_TEMPLATE_PATH.read_text("utf-8")


def default_user_template() -> str:
    """The engine-shipped user-turn template (Jinja)."""
    return USER_TEMPLATE_PATH.read_text("utf-8")


def default_catch_all() -> NoteDefinition:
    """The single neutral catch-all note — the guaranteed routing terminus.

    Loaded by file path like any note, but shipped in-package and complete, so it
    always exists; a packaging regression that drops or duplicates it fails here
    rather than at run time.
    """
    notes = load_notes_from_dir(NOTES_DIR)
    if len(notes) != 1:
        raise DefaultsError(
            f"defaults/notes must hold exactly one catch-all note; found {len(notes)}."
        )
    return notes[0]
