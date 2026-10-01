import argparse
import logging
import sys
import warnings
import webbrowser
from dataclasses import replace
from pathlib import Path

from ankery.config import (
    TRANSPORTS,
    Config,
    ConfigError,
    build_deck_builder,
    sync_collection,
    sync_note_types,
)
from ankery import signin
from ankery.hints import parse_term
from ankery.providers.base import ProviderError
from ankery.sinks.base import SinkError


SYNC_COMMAND = "sync-note-types"
LOGIN_COMMAND = "login"
LOGOUT_COMMAND = "logout"
STATUS_COMMAND = "status"

# How long option 2 of the login prompt waits for the browser redirect.
LOGIN_TIMEOUT = 300.0


def _shared_parser() -> argparse.ArgumentParser:
    """Options accepted both when adding terms and by the sync command."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--config",
        help="path to a config TOML (overrides ANKERY_CONFIG; "
        "default ~/.config/ankery/config.toml)",
    )
    parser.add_argument(
        "--packs-dir", help="user pack directory; a pack here overrides the bundled one"
    )
    parser.add_argument("--anki-url", help="base URL of the AnkiConnect endpoint")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ankery",
        parents=[_shared_parser()],
        description="Look up terms and add them to an Anki deck.",
        epilog=f"commands:\n"
        f"  {SYNC_COMMAND}  push the pack's card templates and styling to Anki "
        f"(see: ankery {SYNC_COMMAND} -h)\n"
        f"  {LOGIN_COMMAND}            sign in with ChatGPT for llm_backend chatgpt\n"
        f"  {LOGOUT_COMMAND}           sign out of ChatGPT\n"
        f"  {STATUS_COMMAND}           show the ChatGPT sign-in and its models\n\n"
        f"To add a term spelled like a command, put -- before it: "
        f"ankery -- {SYNC_COMMAND}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("terms", nargs="+", help="one or more terms to add")
    parser.add_argument(
        "--auth",
        help="path to an auth TOML holding the api key (overrides ANKERY_AUTH; "
        "default ~/.config/ankery/auth.toml)",
    )
    chain = parser.add_mutually_exclusive_group()
    chain.add_argument(
        "--provider",
        metavar="NAMES",
        help="comma-separated providers in fallback order, overriding the pack's "
        "default chain.",
    )
    chain.add_argument(
        "--llm",
        action="store_true",
        help="shorthand for --provider llm: ask the LLM only, with no fallback",
    )
    parser.add_argument("--deck", help="destination deck")
    parser.add_argument(
        "--pack",
        help="pack to load, keyed by code (e.g. de); taken literally, "
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
        "--notes-dir",
        help="directory of extra note layouts (*.toml) merged over the pack's "
        "notes by category",
    )
    parser.add_argument("--note-type", help="Anki note type")
    parser.add_argument(
        "--llm-backend",
        choices=sorted(TRANSPORTS),
        help="LLM transport: chat-completions (an OpenAI-compatible server) or "
        "chatgpt (a ChatGPT plan, via Sign in with ChatGPT)",
    )
    parser.add_argument(
        "--llm-url",
        help="base URL of the chat-completions endpoint (the chatgpt endpoint is fixed)",
    )
    parser.add_argument("--llm-model", help="model name sent to the LLM provider")
    parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="add the note even if Anki considers it a duplicate",
    )
    writes = parser.add_mutually_exclusive_group()
    writes.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="look up and render notes without writing anything to Anki; prints "
        "the -v preview (unless -q) and skips note type provisioning, so no "
        "running Anki is needed",
    )
    writes.add_argument(
        "--sync",
        action="store_true",
        help="after adding, ask Anki to sync its collection with AnkiWeb",
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


def build_sync_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"ankery {SYNC_COMMAND}",
        parents=[_shared_parser()],
        description="Create the pack's missing note types and overwrite the card "
        "templates and styling of existing ones with the pack's (discards edits made "
        "in Anki). Fields are never changed.",
    )
    parser.add_argument(
        "--pack",
        required=True,
        help="pack whose note types to sync, keyed by code (e.g. de); taken "
        "literally, not normalized",
    )
    return parser


def build_signin_parser(command: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"ankery {command}", description=description)
    parser.add_argument(
        "--config",
        help="path to a config TOML (overrides ANKERY_CONFIG; "
        "default ~/.config/ankery/config.toml)",
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


def _shared_overrides(args: argparse.Namespace) -> dict[str, object]:
    """Config overrides from the options both parsers define, plus --pack."""
    overrides: dict[str, object] = {}
    if args.pack is not None:
        # Kept as given — the pack code is the operator's literal choice and must
        # not be rewritten (a pack named `english` stays `english`, not `en`).
        overrides["pack"] = args.pack
    if args.packs_dir is not None:
        overrides["packs_dir"] = Path(args.packs_dir).expanduser()
    if args.anki_url is not None:
        overrides["anki_url"] = args.anki_url
    return overrides


def _config_path(args: argparse.Namespace) -> Path | None:
    return Path(args.config).expanduser() if args.config else None


def _config_from_args(args: argparse.Namespace) -> Config:
    overrides = _shared_overrides(args)
    if args.provider:
        overrides["providers"] = tuple(p.strip() for p in args.provider.split(","))
    elif args.llm:
        overrides["providers"] = ("llm",)
    if args.deck is not None:
        overrides["deck"] = args.deck
    if args.var:
        # Replaces config.toml's [variables] whole, not per key: one source of values.
        overrides["variables"] = _parse_vars(args.var)
    if args.note_type is not None:
        overrides["note_type"] = args.note_type
    if args.notes_dir is not None:
        overrides["notes_dir"] = Path(args.notes_dir).expanduser()
    if args.llm_backend is not None:
        overrides["llm_backend"] = args.llm_backend
    if args.llm_url is not None:
        overrides["llm_base_url"] = args.llm_url
    if args.llm_model is not None:
        overrides["llm_model"] = args.llm_model
    if args.allow_duplicate:
        overrides["allow_duplicate"] = True

    auth = Path(args.auth).expanduser() if args.auth else None

    config = Config.load(path=_config_path(args), auth_path=auth)
    return replace(config, **overrides) if overrides else config


def _sync_main(argv: list[str]) -> int:
    args = build_sync_parser().parse_args(argv)
    try:
        config = Config.load(path=_config_path(args), with_auth=False)
    except ConfigError as exc:
        _error(str(exc))
        return 2
    config = replace(config, **_shared_overrides(args))
    try:
        synced = sync_note_types(config)
    except ConfigError as exc:
        _error(str(exc))
        return 2
    except SinkError as exc:
        _error(f"note type sync failed: {exc}")
        return 1
    for name in synced.created:
        print(f"created note type: {name}")
    for name, parts in synced.updated.items():
        print(f"updated note type: {name} ({', '.join(parts)})")
    return 0


def _signin_setup(command: str, description: str, argv: list[str]):
    """Parse a sign-in command's argv and load its config and token store.
    Returns (config, store, record), or an exit code."""
    args = build_signin_parser(command, description).parse_args(argv)
    try:
        config = Config.load(path=_config_path(args), with_auth=False)
    except ConfigError as exc:
        _error(str(exc))
        return 2
    store = signin.TokenStore(signin.tokens_path())
    try:
        record = store.load()
    except signin.SignInError as exc:
        _error(str(exc))
        return 1
    return config, store, record


def _print_models(access_token: str, timeout: float) -> bool:
    try:
        models = signin.list_models(access_token, timeout=timeout)
    except signin.SignInError as exc:
        _error(f"could not list models: {exc}")
        return False
    print("models:")
    for slug in models:
        print(f"  {slug}")
    return True


def _account(record: dict) -> str:
    return record.get("email") or record["subject"]


def _ask_redirect(auth: signin.Authorization, server: signin.CallbackServer | None) -> str | None:
    """Run the login prompt; return the redirect URL, or None if cancelled."""
    menu = ["1. Paste the redirect URL"]
    if server is not None:
        menu.append("2. Open the link in a browser")
    menu.append("3. Cancel")
    while True:
        print("\n".join(menu))
        choice = input("> ").strip()
        if choice == "1":
            return input("Redirect URL: ")
        if choice == "2" and server is not None:
            if not webbrowser.open(auth.browser_url):
                print("Could not start a browser; open the link above yourself.")
            print("Waiting for the browser sign-in (Ctrl-C cancels)...")
            return server.wait(LOGIN_TIMEOUT)
        if choice == "3":
            return None
        print(f"Unknown choice {choice!r}.")


def _login_main(argv: list[str]) -> int:
    setup = _signin_setup(LOGIN_COMMAND, "Sign in with ChatGPT for llm_backend chatgpt.", argv)
    if isinstance(setup, int):
        return setup
    config, store, record = setup

    auth = signin.begin(record)
    print("Sign in to ChatGPT at:")
    print(auth.url)
    print()
    print("After signing in, the browser goes to a 127.0.0.1 page. If that page "
          "does not load, copy its URL from the address bar and paste it here.")
    server = None
    if signin.can_open_browser():
        try:
            server = signin.CallbackServer(auth.state)
        except OSError as exc:
            _error(f"port {signin.CALLBACK_PORT} is in use ({exc.strerror}); "
                   "cannot wait for the browser, paste the redirect URL instead")
    try:
        url = _ask_redirect(auth, server)
    except (EOFError, KeyboardInterrupt):
        url = None
    except signin.SignInError as exc:
        _error(f"sign-in failed: {exc}")
        return 1
    finally:
        if server is not None:
            server.close()
    if url is None:
        print("\nsign-in cancelled", file=sys.stderr)
        return 1

    try:
        record = signin.complete(auth, url, timeout=config.llm_timeout)
        with store.lock():
            store.save(record)
    except signin.SignInError as exc:
        _error(f"sign-in failed: {exc}")
        return 1
    print(f"signed in as {_account(record)}")
    _print_models(record["access_token"], config.llm_timeout)
    return 0


def _logout_main(argv: list[str]) -> int:
    setup = _signin_setup(LOGOUT_COMMAND, "Sign out of ChatGPT: revoke and delete "
                          "the stored tokens. The app registration is kept.", argv)
    if isinstance(setup, int):
        return setup
    config, store, record = setup
    if not signin.is_signed_in(record):
        print("not signed in")
        return 0
    try:
        answer = input(f"Sign out {_account(record)}? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("logout cancelled", file=sys.stderr)
        return 1
    try:
        signin.revoke(record, timeout=config.llm_timeout)
    except signin.SignInError as exc:
        warnings.warn(f"{exc}; deleting the local tokens anyway")
    try:
        with store.lock():
            store.save(signin.signed_out(record))
    except signin.SignInError as exc:
        _error(str(exc))
        return 1
    print(f"signed out {_account(record)}")
    return 0


def _status_main(argv: list[str]) -> int:
    setup = _signin_setup(STATUS_COMMAND, "Show the ChatGPT sign-in and the models "
                          "its plan offers.", argv)
    if isinstance(setup, int):
        return setup
    config, store, record = setup
    if not signin.is_signed_in(record):
        print(f"not signed in; run `ankery {LOGIN_COMMAND}`")
        return 1
    tokens = signin.StoredTokens(store, timeout=config.llm_timeout)
    try:
        access_token = tokens.access_token()
        record = store.load()
    except (ProviderError, signin.SignInError) as exc:
        _error(str(exc))
        return 1
    expiry = signin.expires_at(record).strftime("%Y-%m-%d %H:%M UTC")
    print(f"signed in as {_account(record)}")
    print(f"access token expires {expiry} (refreshed automatically)")
    return 0 if _print_models(access_token, config.llm_timeout) else 1


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


def _report_added(result, term: str, level: int, *, dry_run: bool = False) -> None:
    """Print one added/previewed term at the given verbosity: nothing at 0, the
    term at 1, plus note id, note type, and the saved fields at 2+."""
    if level < 1:
        return
    label = term if result.term == term else f"{term} -> {result.term}"
    if level == 1:
        print(f"{label}: added")
        return
    if dry_run:
        print(f"{label}: would add ({result.note_type})")
    else:
        print(f"{label}: added (note {result.note_id}, {result.note_type})")
    for name, value in result.fields.items():
        print(f"  {name}: {value}")


def _add_main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    # 0 = quiet, 1 = default, 2 = note content (-v), 3 = engine trace (-vv).
    level = 0 if args.quiet else 1 + min(args.verbose, 2)
    if args.dry_run and not args.quiet:
        level = max(level, 2)  # the preview is the point of a dry run; -q still wins
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
    # A dry run touches no Anki at all: no note type provisioning either.
    if not args.dry_run:
        try:
            created = builder.verify_note_types()
        except SinkError as exc:
            _error(f"note type setup failed: {exc}")
            return 1
        if level >= 1:
            for name in created or []:
                print(f"created note type: {name}")

    exit_code = 0
    for raw_term in args.terms:
        try:
            term, category_hint = parse_term(raw_term, builder.category_names)
        except ValueError as exc:
            _error(f"{raw_term!r}: {exc}")
            exit_code = 1
            continue
        try:
            if args.dry_run:
                result = builder.preview(term, category_hint=category_hint)
            else:
                result = builder.add_term(term, category_hint=category_hint)
        except ProviderError as exc:
            _error(f"{term}: lookup failed: {exc}")
            exit_code = 1
        except SinkError as exc:
            _error(f"{term}: could not add note: {exc}")
            exit_code = 1
        else:
            if result is None:
                _error(f"{term}: not found")
                exit_code = 1
            else:
                _report_added(result, term, level, dry_run=args.dry_run)
    if args.sync:
        try:
            sync_collection(config)
        except SinkError as exc:
            _error(f"sync failed: {exc}")
            return 1
        if level >= 1:
            print("sync requested")
    return exit_code


COMMANDS = {
    SYNC_COMMAND: _sync_main,
    LOGIN_COMMAND: _login_main,
    LOGOUT_COMMAND: _logout_main,
    STATUS_COMMAND: _status_main,
}


def main(argv: list[str] | None = None) -> int:
    warnings.showwarning = _show_warning
    argv = sys.argv[1:] if argv is None else list(argv)
    # Only the first token selects a command, so `ankery -- sync-note-types` adds
    # that literal term.
    command = COMMANDS.get(argv[0]) if argv else None
    if command is not None:
        return command(argv[1:])
    return _add_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
