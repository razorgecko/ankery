import argparse
import os
import sys
from dataclasses import replace
from pathlib import Path

from anki_deckbuilder.config import Config, ConfigError, build_deck_builder
from anki_deckbuilder.providers.base import ProviderError
from anki_deckbuilder.sinks.base import SinkError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="anki_deckbuilder",
        description="Look up words and add them to an Anki deck.",
    )
    parser.add_argument("words", nargs="+", help="one or more words to add")
    parser.add_argument(
        "--config",
        help="path to a config TOML (overrides ANKIDECK_CONFIG; "
        "default ~/.config/anki_deckbuilder/config.toml)",
    )
    parser.add_argument(
        "--auth",
        help="path to an auth TOML holding the api key (overrides ANKIDECK_AUTH; "
        "default ~/.config/anki_deckbuilder/auth.toml)",
    )
    parser.add_argument(
        "--provider",
        metavar="NAMES",
        help="comma-separated providers in fallback order (overrides "
        "ANKIDECK_PROVIDERS). Known: llm, verbformen.",
    )
    parser.add_argument("--deck", help="destination deck (overrides ANKIDECK_DECK)")
    parser.add_argument("--source-lang", help="language of the words")
    parser.add_argument("--target-lang", help="language to translate into")
    parser.add_argument("--note-type", help="Anki note type")
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="add the note even if Anki considers it a duplicate",
    )
    return parser


def _config_from_args(args: argparse.Namespace) -> Config:
    # Env is the base; explicit flags win over it.
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
    if args.allow_duplicate:
        overrides["allow_duplicate"] = True

    # Which files to load: the --config/--auth flags win over the
    # ANKIDECK_CONFIG/ANKIDECK_AUTH env vars; an unset source means Config.load
    # uses its default path for that file.
    config_path = args.config or os.environ.get("ANKIDECK_CONFIG")
    auth_path = args.auth or os.environ.get("ANKIDECK_AUTH")
    path = Path(config_path).expanduser() if config_path else None
    auth = Path(auth_path).expanduser() if auth_path else None

    config = Config.load(path=path, auth_path=auth)
    return replace(config, **overrides) if overrides else config


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = _config_from_args(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    builder = build_deck_builder(config)

    exit_code = 0
    for word in args.words:
        try:
            note_id = builder.add_word(
                word,
                source_language=config.source_language,
                target_language=config.target_language,
            )
        except ProviderError as exc:
            print(f"{word}: lookup failed: {exc}", file=sys.stderr)
            exit_code = 1
        except SinkError as exc:
            print(f"{word}: could not add note: {exc}", file=sys.stderr)
            exit_code = 1
        else:
            if note_id is None:
                print(f"{word}: not found", file=sys.stderr)
                exit_code = 1
            else:
                print(f"{word}: added (note {note_id})")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
