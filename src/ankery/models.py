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
    part_of_speech: str | None = None

    source_language: str | None = None
    target_language: str | None = None

    # Language-defined grammatical properties; keys declared per-POS by the active pack.
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
