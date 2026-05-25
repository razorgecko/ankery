from pydantic import BaseModel, ConfigDict, Field, field_validator


class WordInfo(BaseModel):
    """Structured information about a word.

    The contract every layer speaks: providers produce it, the manager maps it
    to note fields, the sink writes it to Anki. It is also the schema the LLM
    provider is asked to fill, so it doubles as the validation boundary for
    untrusted model output. Unknown keys are ignored rather than rejected.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    word: str = Field(min_length=1)
    definitions: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    translations: list[str] = Field(default_factory=list)
    pronunciation: str | None = None
    part_of_speech: str | None = None

    # Language pair. Affects prompting (e.g. "define this German word") and
    # disambiguates which language `translations` are written in.
    source_language: str | None = None
    target_language: str | None = None

    # Grammar. `gender` is the article for nouns ("der"/"die"/"das"); None when
    # not applicable. `separable` flags German separable verbs (einkaufen);
    # None means not applicable to this word.
    gender: str | None = None
    separable: bool | None = None

    # Part-of-speech-appropriate inflected forms keyed by a canonical label.
    # The key vocabulary is pinned per language/POS in prompts.py so the LLM
    # fills consistent keys (e.g. "plural", "present_3sg") rather than ad-hoc
    # ones. Kept as a flat dict so the type stays language-neutral and the
    # Anki-field mapping stays simple.
    inflections: dict[str, str] = Field(default_factory=dict)

    audio_url: str | None = None

    source: str = Field(min_length=1)

    @field_validator("translations", mode="before")
    @classmethod
    def _coerce_translations(cls, value: object) -> object:
        """Accept the language-keyed dict that some models emit for translations.

        The contract is a flat list (translations are already disambiguated by
        `target_language`), but a model told to translate "into the target
        language" will sometimes key the result by language code, e.g.
        {"en": "house"} or {"en": ["house", "home"]}. Flatten such a dict to its
        values here so the quirk is absorbed at the boundary rather than failing
        validation. A list (the expected shape) passes straight through; any
        other type is left for the field's own type-checking to reject.
        """
        if isinstance(value, dict):
            flattened: list[str] = []
            for item in value.values():
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            return flattened
        return value
