from pydantic import BaseModel, ConfigDict, Field, field_validator


class WordInfo(BaseModel):
    """Structured information about a word — the contract every layer speaks.

    Providers produce it, the manager normalizes and maps it to note fields, the
    sink writes it to Anki. It is also the schema the LLM provider is asked to
    fill, so it doubles as the validation boundary for untrusted model output.
    Unknown keys are ignored rather than rejected.

    The model carries **no language knowledge**. The invariant core below is
    typed and universal; everything a particular language needs — gender, case
    forms, verb classes, kana readings, IPA — lives in the open `features` dict,
    whose key vocabulary is declared by the active language pack (see pack.py).
    The pack hands those keys to the LLM as the requested schema and the note
    templates read them back via Jinja, so the pack is the single source of truth
    and the contract stays neutral.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    word: str = Field(min_length=1)
    definitions: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    # Glosses for `examples`, aligned by position: example_translations[i] is the
    # translation of examples[i]. Sources that supply only the sentence leave
    # this empty rather than padding it, so it may be shorter than `examples`;
    # consumers index it defensively.
    example_translations: list[str] = Field(default_factory=list)
    translations: list[str] = Field(default_factory=list)
    part_of_speech: str | None = None

    # Language pair. `source_language` is the pack code (e.g. "de"); both affect
    # prompting and disambiguate which language `translations` are written in.
    source_language: str | None = None
    target_language: str | None = None

    # Open, language-defined vocabulary: inherent properties and variant forms
    # alike, keyed by labels the active pack declares per part of speech
    # (e.g. "gender", "genitive_sg", "present_3sg", "reading", "ipa"). Kept a
    # flat str->str dict so the contract names no language; the pack supplies the
    # meaning of each key and the note templates render them. Values are bare
    # forms (no leading article); a pack's normalize hook enforces that at the
    # boundary so consumers need no stripping.
    features: dict[str, str] = Field(default_factory=dict)

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
