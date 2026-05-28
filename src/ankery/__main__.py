import argparse
import sys
import warnings
from dataclasses import replace
from pathlib import Path

from ankery.config import Config, ConfigError, build_deck_builder
from ankery.providers.base import ProviderError
from ankery.sinks.base import SinkError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ankery",
        description="Look up words and add them to an Anki deck.",
    )
    parser.add_argument("words", nargs="+", help="one or more words to add")
    parser.add_argument(
        "--config",
        help="path to a config TOML (overrides ANKERY_CONFIG; "
        "default ~/.config/ankery/config.toml)",
    )
    parser.add_argument(
        "--auth",
        help="path to an auth TOML holding the api key (overrides ANKERY_AUTH; "
        "default ~/.config/ankery/auth.toml)",
    )
    parser.add_argument(
        "--provider",
        metavar="NAMES",
        help="comma-separated providers in fallback order, overriding the pack's "
        "default chain.",
    )
    parser.add_argument("--deck", help="destination deck")
    parser.add_argument(
        "--source-lang", help="language pack to load (e.g. de), keyed by code"
    )
    parser.add_argument("--target-lang", help="language to translate into")
    parser.add_argument(
        "--langs-dir", help="user pack directory; a pack here overrides the bundled one"
    )
    parser.add_argument(
        "--notes-dir",
        help="directory of extra note layouts (*.toml) merged over the pack's "
        "notes by part of speech",
    )
    parser.add_argument("--note-type", help="Anki note type")
    parser.add_argument("--llm-url", help="OpenAI-compatible base URL for the LLM provider")
    parser.add_argument("--llm-model", help="model name sent to the LLM provider")
    parser.add_argument("--anki-url", help="base URL of the AnkiConnect endpoint")
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="add the note even if Anki considers it a duplicate",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> Config:
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["providers"] = tuple(p.strip() for p in args.provider.split(","))
    if args.deck is not None:
        overrides["deck"] = args.deck
    if args.source_lang is not None:
        overrides["source_language"] = args.source_lang
    if args.target_lang is not None:
        overrides["target_language"] = args.target_lang
    if args.note_type is not None:
        overrides["note_type"] = args.note_type
    if args.langs_dir is not None:
        overrides["langs_dir"] = Path(args.langs_dir).expanduser()
    if args.notes_dir is not None:
        overrides["notes_dir"] = Path(args.notes_dir).expanduser()
    if args.llm_url is not None:
        overrides["llm_base_url"] = args.llm_url
    if args.llm_model is not None:
        overrides["llm_model"] = args.llm_model
    if args.anki_url is not None:
        overrides["anki_url"] = args.anki_url
    if args.allow_duplicate:
        overrides["allow_duplicate"] = True

    path = Path(args.config).expanduser() if args.config else None
    auth = Path(args.auth).expanduser() if args.auth else None

    config = Config.load(path=path, auth_path=auth)
    return replace(config, **overrides) if overrides else config


def _show_warning(message, category, filename, lineno, file=None, line=None) -> None:
    """CLI-friendly warning output: just the message, no file/line/source-line noise."""
    print(f"ankery: warning: {message}", file=file or sys.stderr)


def main(argv: list[str] | None = None) -> int:
    warnings.showwarning = _show_warning
    args = build_parser().parse_args(argv)
    try:
        config = _config_from_args(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    try:
        builder = build_deck_builder(config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    try:
        builder.verify_note_types()
    except SinkError as exc:
        print(f"note type setup failed: {exc}", file=sys.stderr)
        return 1

    exit_code = 0
    for raw_word in args.words:
        word = raw_word.strip()
        if not word:
            print("skipping empty word", file=sys.stderr)
            exit_code = 1
            continue
        try:
            result = builder.add_word(word)
        except ProviderError as exc:
            print(f"{word}: lookup failed: {exc}", file=sys.stderr)
            exit_code = 1
        except SinkError as exc:
            print(f"{word}: could not add note: {exc}", file=sys.stderr)
            exit_code = 1
        else:
            if result is None:
                print(f"{word}: not found", file=sys.stderr)
                exit_code = 1
            else:
                label = word if result.word == word else f"{word} -> {result.word}"
                print(f"{label}: added (note {result.note_id})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
