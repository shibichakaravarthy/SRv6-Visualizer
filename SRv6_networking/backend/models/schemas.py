"""Shared snapshot envelope. Native Linux/FRR fields are retained in records."""
from typing import Any, Literal
from pydantic import BaseModel, Field


class Snapshot(BaseModel):
    schema_version: int = 1
    collection_id: str | None = None
    collected_at: str | None = None
    lab_ids: list[str] = Field(default_factory=list)
    source: str = 'live Docker inspection and fixed read-only commands'
    status: Literal['available', 'partial', 'missing', 'failed'] = 'missing'
    configured_files: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    configured_links: list[dict[str, Any]] = Field(default_factory=list)
