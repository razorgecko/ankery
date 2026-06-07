from pydantic import BaseModel, ConfigDict, Field, field_validator


class WordInfo(BaseModel):
    """Structured word data — the shared contract between providers, manager, and sink."""

    model_config = ConfigDict(str_strip_whitespace=True)

    word: str = Field(min_length=1)
    definitions: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    # May be shorter than `examples`; index defensively.
    example_translations: list[str] = Field(default_factory=list)
    translations: list[str] = Field(default_factory=list)
    # The pack's routing discriminator value (e.g. "noun"); one of its declared
    # [category] vocabulary. Picks the note; named generically so the engine
    # carries no domain knowledge.
    category: str | None = None

    # Write-only provenance: the pack that produced this and the resolved variables
    # it was produced under. The engine names neither; nothing reads them today.
    pack: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)

    # Pack-defined properties; keys declared per category value by the active pack.
    features: dict[str, str] = Field(default_factory=dict)

    audio_url: str | None = None

    source: str = Field(min_length=1)

    @field_validator("translations", mode="before")
    @classmethod
    def _coerce_translations(cls, value: object) -> object:
        """Flatten a language-keyed dict some models emit instead of a flat list."""
        if isinstance(value, dict):
            flattened: list[str] = []
            for item in value.values():
                if isinstance(item, list):
                    flattened.extend(item)
                else:
                    flattened.append(item)
            return flattened
        return value
