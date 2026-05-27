"""German output normalization — the pack's optional post-fetch filter.

The engine calls `normalize(info)` on every provider's `WordInfo` before
routing, if this file exists (no filter.py => identity). This is where a
language's source-shape quirks are cleaned up in code, with full knowledge of
its grammar — the alternative to a fixed toolbox of transforms in the engine.

For German the one job is enforcing the bare-form contract on `features`: a
model may return "des Hauses" where the note wants "Hauses". Stripping a leading
definite article does that. It is safe to run over every feature value: the
`gender` value ("der"/"die"/"das") is a single token with nothing after it, so
it is returned unchanged; only article+form values ("des Hauses") are trimmed.
Idempotent, so re-running it on verbformen's already-bare forms is a no-op.

Imports must be absolute — the engine loads this file by path, so a relative
import has no package to resolve against.
"""

from ankery.models import WordInfo

_ARTICLES = {"der", "die", "das", "des", "dem", "den"}


def _strip_leading_article(form: str) -> str:
    head, _, rest = form.partition(" ")
    return rest if head.lower() in _ARTICLES and rest else form


def normalize(info: WordInfo) -> WordInfo:
    info.features = {
        key: _strip_leading_article(value) for key, value in info.features.items()
    }
    return info
