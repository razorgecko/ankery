"""Prompt text and the canonical inflection-key vocabulary for the LLM provider.

The `inflections` dict on `WordInfo` is intentionally untyped, so consistency
comes from pinning the key vocabulary in the system prompt and instructing the
LLM to use exactly those keys. Keys are lowercase, underscore-separated
grammatical labels (`genitive_sg`, `present_3sg`, ...) that the note recipes in
`recipes.py` read back out.

The prompt is parameterised by the source/target language passed per request
(`build_user_prompt`); grammatical rules are stated in terms of "the source
language" rather than baking in any one language's articles or auxiliaries.
"""

SYSTEM_PROMPT = """\
You are a lexicographer building Anki vocabulary cards. Given a single word in \
the source language, return ONLY a JSON object (no prose, no markdown fences) \
describing it.

General rules:
- `word`: the citation/dictionary form (the infinitive for verbs, the \
nominative singular for nouns). Give the bare lemma: no article, and no \
parentheses around optional or detachable affixes.
- `part_of_speech`: one of "noun", "verb", "adjective", "adverb", etc.
- `definitions` and `examples`: written in the SOURCE language.
- `translations`: a JSON array of strings written in the TARGET language, \
e.g. ["house", "home"]. Always an array, never an object keyed by language.
- `gender`: for nouns in languages that mark grammatical gender, the article \
that expresses it. null otherwise.
- `separable`: for verbs in languages with separable/detachable prefixes, true \
if this verb has one. null for non-verbs and for languages without them.
- `pronunciation`: IPA if known, else null.
- Leave any field you are unsure about null or empty rather than guessing.

The `inflections` object must use EXACTLY the canonical keys below for the \
word's part of speech (omit a key only if it genuinely does not apply):

NOUN keys:
  genitive_sg, plural

VERB keys:
  present_1sg, present_2sg, present_3sg, present_1pl, present_2pl, present_3pl, \
preterite, perfect, auxiliary, imperative_sg

ADJECTIVE keys:
  comparative, superlative

Verb specifics:
- Fill ALL SIX present-tense forms even when the verb is regular, since \
per-person stem changes cannot be recovered from a single form.
- `preterite` is the 1st/3rd person singular form.
- `perfect` is the full compound form (auxiliary + participle).
- `auxiliary` is the perfect-tense auxiliary verb the source language uses.
- For separable/prefixed verbs, give the inflected forms as they actually \
appear in a sentence.
"""


def build_user_prompt(
    word: str,
    source_language: str,
    target_language: str,
) -> str:
    """Render the per-word instruction for the LLM provider."""
    return (
        f"Source language: {source_language}\n"
        f"Target language: {target_language}\n"
        f"Word: {word}"
    )
