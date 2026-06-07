from pydantic import BaseModel, ConfigDict, Field, field_validator


class WordInfo(BaseModel):
    """Structured word data."""

    model_config = ConfigDict(str_strip_whitespace=True)

    word: str = Field(min_length=1)
    definitions: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    # Aligned to `examples` by index; may be shorter.
    example_translations: list[str] = Field(default_factory=list)
    translations: list[str] = Field(default_factory=list)
    # One value from the pack's declared category vocabulary (e.g. "noun").
    category: str | None = None

    # Provenance: the pack that produced this and the variables it was produced under.
    pack: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)

    # Pack-defined properties, keyed by labels the pack declares.
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
