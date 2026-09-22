"""Public request and response contracts for the streaming lifecycle."""

from __future__ import annotations

from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.streaming import StreamPlatform, StreamSessionStatus


class StartStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    input_source: str = Field(min_length=1, max_length=2048)
    platforms: list[StreamPlatform] = Field(min_length=1, max_length=4)

    @field_validator("platforms")
    @classmethod
    def platforms_must_be_unique(cls, platforms: list[StreamPlatform]) -> list[StreamPlatform]:
        if len(platforms) != len(set(platforms)):
            raise ValueError("Streaming platforms must be unique")
        return platforms


class StreamResponse(BaseModel):
    stream_id: uuid.UUID
    status: StreamSessionStatus
    platforms: list[StreamPlatform]
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class StreamStatusResponse(BaseModel):
    stream_id: uuid.UUID
    status: StreamSessionStatus
    platforms: list[StreamPlatform]
    process_active: bool
    started_at: datetime | None = None
    ended_at: datetime | None = None


class StopStreamResponse(BaseModel):
    stream_id: uuid.UUID
    status: StreamSessionStatus