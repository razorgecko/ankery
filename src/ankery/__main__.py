import argparse
import logging
import sys
import warnings
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from ankery.config import Config, ConfigError, build_deck_builder
from ankery.providers.base import ProviderError
from ankery.sinks.base import SinkError


def split_category_hint(raw: str) -> tuple[str, str | None]:
    """Split a `word:cat` token into (word, raw_hint); no colon -> (word, None).

    The category hint is everything after the last colon, e.g. `schnell:adj` or
    `Bank:noun`. A colon is glob-safe, so words need no shell quoting.
    """
    word, sep, hint = raw.rpartition(":")
    if not sep:
        return raw.strip(), None
    return word.strip(), hint.strip()


def resolve_category_hint(hint: str, category_names: Sequence[str]) -> str:
    """Resolve a category hint to one canonical pack category by exact-then-prefix match.

    Raises ValueError if the hint is empty, matches nothing, or is an ambiguous
    prefix of more than one declared category.
    """
    if not hint:
        raise ValueError("empty category hint after colon")
    lowered = hint.lower()
    exact = [name for name in category_names if name.lower() == lowered]
    if exact:
        return exact[0]
    prefix = [name for name in category_names if name.lower().startswith(lowered)]
    if len(prefix) == 1:
        return prefix[0]
    known = ", ".join(sorted(category_names))
    if not prefix:
        raise ValueError(f"unknown category {hint!r}; this pack knows: {known}")
    raise ValueError(
        f"ambiguous category {hint!r}; matches: {', '.join(sorted(prefix))}"
    )


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
        "--pack",
        help="language pack to load, keyed by code (e.g. de); taken literally, "
        "not normalized",
    )
    parser.add_argument(
        "--var",
        metavar="KEY=VALUE",
        action="append",
        help="set a pack variable (e.g. --var target_language=en); repeatable. "
        "The pack declares and consumes these.",
    )
    parser.add_argument(
        "--packs-dir", help="user pack directory; a pack here overrides the bundled one"
    )
    parser.add_argument(
        "--notes-dir",
        help="directory of extra note layouts (*.toml) merged over the pack's "
        "notes by category",
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
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="no normal output; errors still go to stderr",
    )
    output.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="-v also prints each note's id, type, and saved content; "
        "-vv adds an engine trace on stderr (providers tried, requests, prompts)",
    )
    return parser


def _parse_vars(tokens: list[str]) -> dict[str, str]:
    """Parse repeated `KEY=VALUE` flag tokens into a dict; the value is kept as
    given. A token with no `=` is an error. A repeated key wins last."""
    variables: dict[str, str] = {}
    for token in tokens:
        key, sep, value = token.partition("=")
        if not sep:
            raise ConfigError(f"--var expects KEY=VALUE, got {token!r}")
        variables[key.strip()] = value
    return variables


def _config_from_args(args: argparse.Namespace) -> Config:
    overrides: dict[str, object] = {}
    if args.provider:
        overrides["providers"] = tuple(p.strip() for p in args.provider.split(","))
    if args.deck is not None:
        overrides["deck"] = args.deck
    if args.pack is not None:
        # Kept as given — the pack code is the operator's literal choice and must
        # not be rewritten (a pack named `english` stays `english`, not `en`).
        overrides["pack"] = args.pack
    if args.var:
        overrides["variables"] = _parse_vars(args.var)
    if args.note_type is not None:
        overrides["note_type"] = args.note_type
    if args.packs_dir is not None:
        overrides["packs_dir"] = Path(args.packs_dir).expanduser()
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


def _error(message: str) -> None:
    print(f"ankery: error: {message}", file=sys.stderr)


def _setup_trace() -> None:
    """Route engine logs to stderr at DEBUG. Scoped to the "ankery" logger tree so
    third-party loggers (httpx) stay quiet; pack code joins by naming its logger
    under "ankery." (see the netzverb provider)."""
    log = logging.getLogger("ankery")
    # Replace, don't stack: repeated main() calls (tests, library use) would
    # otherwise duplicate every line and hold stale stderr streams.
    for handler in list(log.handlers):
        if getattr(handler, "_ankery_trace", False):
            log.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("ankery: trace: %(message)s"))
    handler._ankery_trace = True
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)


def _report_added(result, word: str, level: int) -> None:
    """Print one added word at the given verbosity: nothing at 0, the word at 1,
    plus note id, note type, and the saved fields at 2+."""
    if level < 1:
        return
    label = word if result.word == word else f"{word} -> {result.word}"
    if level == 1:
        print(f"{label}: added")
        return
    print(f"{label}: added (note {result.note_id}, {result.note_type})")
    for name, value in result.fields.items():
        print(f"  {name}: {value}")


def main(argv: list[str] | None = None) -> int:
    warnings.showwarning = _show_warning
    args = build_parser().parse_args(argv)
    # 0 = quiet, 1 = default, 2 = note content (-v), 3 = engine trace (-vv).
    level = 0 if args.quiet else 1 + min(args.verbose, 2)
    if level >= 3:
        _setup_trace()
    try:
        config = _config_from_args(args)
    except ConfigError as exc:
        _error(str(exc))
        return 2
    try:
        builder = build_deck_builder(config)
    except ConfigError as exc:
        _error(str(exc))
        return 2
    try:
        created = builder.verify_note_types()
    except SinkError as exc:
        _error(f"note type setup failed: {exc}")
        return 1
    if level >= 1:
        for name in created or []:
            print(f"created note type: {name}")

    exit_code = 0
    for raw_word in args.words:
        word, raw_hint = split_category_hint(raw_word)
        if not word:
            _error("empty word, skipping")
            exit_code = 1
            continue
        category_hint: str | None = None
        if raw_hint is not None:
            try:
                category_hint = resolve_category_hint(raw_hint, builder.category_names)
            except ValueError as exc:
                _error(f"{raw_word}: {exc}")
                exit_code = 1
                continue
        try:
            result = builder.add_word(word, category_hint=category_hint)
        except ProviderError as exc:
            _error(f"{word}: lookup failed: {exc}")
            exit_code = 1
        except SinkError as exc:
            _error(f"{word}: could not add note: {exc}")
            exit_code = 1
        else:
            if result is None:
                _error(f"{word}: not found")
                exit_code = 1
            else:
                _report_added(result, word, level)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
