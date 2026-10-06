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


class ViewerJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_session_id: uuid.UUID


class ViewerSessionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ViewerSessionResponse(BaseModel):
    viewer_session_id: uuid.UUID
    stream_id: uuid.UUID
    client_session_id: uuid.UUID
    joined_at: datetime
    lease_expires_at: datetime
    left_at: datetime | None
    is_active: bool


class StreamChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    body: str = Field(min_length=1, max_length=2000)


class StreamChatMessageResponse(BaseModel):
    id: uuid.UUID
    stream_id: uuid.UUID
    user_id: uuid.UUID
    body: str
    created_at: datetime


class StreamSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    creator_id: uuid.UUID


class StreamSubscriptionResponse(BaseModel):
    id: uuid.UUID
    stream_id: uuid.UUID
    user_id: uuid.UUID
    creator_id: uuid.UUID
    created_at: datetime
