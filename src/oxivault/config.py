"""Configuration settings for oxivault."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VaultConfig(BaseModel):
    """Pydantic configuration model for a vault."""

    store_type: str = Field(default="local", description="Store backend: local, memory, s3, git")
    store_uri: str = Field(default=".", description="URI or path of the vault store")
    data_ns: str | None = Field(default=None, description="Default namespace for instance data")
    max_read_bytes: int = Field(default=10 << 20, gt=0, description="Max bytes per object")
    max_context_bytes: int = Field(default=4 << 20, gt=0, description="Max bytes for context documents")
    max_snapshot_bytes: int = Field(default=256 << 20, gt=0, description="Max total snapshot bytes")
