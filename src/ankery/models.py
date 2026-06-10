from pydantic import BaseModel, ConfigDict, Field, field_validator


class Entry(BaseModel):
    """One structured entry. Names no domain: the typed core carries identity,
    routing, and provenance; everything domain-specific lives in the open
    `properties`/`collections` bags the active pack declares."""

    model_config = ConfigDict(str_strip_whitespace=True)

    term: str = Field(min_length=1)
    # One value from the pack's declared category vocabulary (e.g. "noun").
    category: str | None = None

    # Provenance: the pack that produced this and the variables it was produced under.
    pack: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)

    # Scalar pack-defined properties, keyed by labels the pack declares.
    properties: dict[str, str] = Field(default_factory=dict)
    # List-valued pack-defined properties, keyed by labels the pack declares.
    collections: dict[str, list[str]] = Field(default_factory=dict)

    audio_url: str | None = None

    source: str = Field(min_length=1)

    @field_validator("collections", mode="before")
    @classmethod
    def _coerce_collections(cls, value: object) -> object:
        """Coerce each collection value to a list: a dict flattens to its concatenated
        values, a bare string wraps to a one-item list, a list passes through."""
        if not isinstance(value, dict):
            return value
        coerced: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(item, dict):
                flattened: list[str] = []
                for sub in item.values():
                    flattened.extend(sub if isinstance(sub, list) else [sub])
                coerced[key] = flattened
            elif isinstance(item, str):
                coerced[key] = [item]
            else:
                coerced[key] = item
        return coerced
