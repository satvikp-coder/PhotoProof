"""Artifact references: content-addressed outputs produced by plugins."""

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    """Pointer to an immutable blob in the object store, identified by SHA-256."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=200)
    media_type: str = Field(min_length=1, max_length=100)
    size: int = Field(ge=0)
    producer: str
