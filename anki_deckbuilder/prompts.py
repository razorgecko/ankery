"""Prompt text and the German inflection-key convention.

The `inflections` dict on `WordInfo` is intentionally untyped, so consistency
comes from pinning the key vocabulary here and instructing the LLM to use it.
Keys are kept lowercase and underscore-separated.

Why these key sets (German):

- Verbs store the FULL present-tense paradigm (all six persons). Per-person
  stem irregularity (modals like mögen -> mag/magst/mögen, and sein/haben/
  werden/wissen) cannot be recovered from the 3rd-person form alone, so every
  person is stored even for regular verbs. The other tenses need only their
  principal part: preterite endings are regular across persons even for strong
  verbs (sah, sahst, sah, sahen), and the perfect is a fixed participle plus an
  auxiliary.
- Nouns store gender (as the article) plus the genitive singular and plural.
  The full 4-case x 2-number table is derivable from these; the genitive
  singular also flags weak/n-declension nouns (der Junge -> des Jungen).
- The lemma (`word`) stays a clean citation form: infinitive for verbs,
  nominative singular for nouns, no article and no separable-prefix parentheses
  (we never conjugate ourselves, so the boundary marker buys nothing; the
  `separable` flag and the inflected forms carry that information).
"""

GERMAN_INFLECTION_KEYS: dict[str, list[str]] = {
    "noun": [
        "genitive_sg",  # e.g. "Buches"
        "plural",  # e.g. "Bücher"
    ],
    "verb": [
        "present_1sg",  # ich  -> "sehe"
        "present_2sg",  # du   -> "siehst"
        "present_3sg",  # er/sie/es -> "sieht"
        "present_1pl",  # wir  -> "sehen"
        "present_2pl",  # ihr  -> "seht"
        "present_3pl",  # sie/Sie -> "sehen"
        "preterite",  # 1st/3rd sg -> "sah"
        "perfect",  # auxiliary + participle -> "hat gesehen"
        "auxiliary",  # "haben" or "sein"
        "imperative_sg",  # "sieh" (optional; irregular for e->ie/i verbs)
    ],
    "adjective": [
        "comparative",  # "größer"
        "superlative",  # "am größten"
    ],
}

SYSTEM_PROMPT = """\
You are a lexicographer building Anki vocabulary cards. Given a single word in \
the source language, return ONLY a JSON object (no prose, no markdown fences) \
describing it.

General rules:
- `word`: the citation/dictionary form. For verbs use the infinitive; for nouns \
the nominative singular. Do NOT include an article and do NOT mark separable \
prefixes with parentheses (write "einkaufen", never "(ein)kaufen").
- `part_of_speech`: one of "noun", "verb", "adjective", "adverb", etc.
- `definitions` and `examples`: written in the SOURCE language.
- `translations`: a JSON array of strings written in the TARGET language, \
e.g. ["house", "home"]. Always an array, never an object keyed by language.
- `gender`: for nouns, the definite article "der" / "die" / "das". null otherwise.
- `separable`: for verbs, true if it is a separable-prefix verb (einkaufen), \
false otherwise. null for non-verbs.
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
- Fill ALL SIX present-tense forms even when the verb is regular.
- `preterite` is the 1st/3rd person singular form (e.g. "sah").
- `perfect` includes the auxiliary and participle (e.g. "hat gesehen").
- `auxiliary` is "haben" or "sein".
- For separable verbs, give the inflected forms as they actually appear \
("kauft ein", "hat eingekauft").
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
