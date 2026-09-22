"""Authenticated REST API for the FFmpeg streaming lifecycle."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.models.user import User
from features.auth.middleware import get_current_active_user
from features.streaming.schemas import (
    StartStreamRequest,
    StreamResponse,
    StreamStatusResponse,
    StopStreamResponse,
)
from features.streaming.service import StreamingService, StreamNotFoundError, StreamStartError

router = APIRouter(prefix="/api/streams", tags=["streams"])


@router.post("", response_model=StreamResponse, status_code=status.HTTP_201_CREATED)
async def start_stream(
    request: StartStreamRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> StreamResponse:
    try:
        return await StreamingService(db).start(request, current_user)
    except StreamStartError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stream could not be started",
        ) from exc


@router.get("", response_model=list[StreamResponse])
async def list_streams(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[StreamResponse]:
    return await StreamingService(db).list(current_user)


@router.get("/{stream_id}", response_model=StreamStatusResponse)
async def get_stream_status(
    stream_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> StreamStatusResponse:
    try:
        return await StreamingService(db).status(stream_id, current_user)
    except StreamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found") from exc


@router.post("/{stream_id}/stop", response_model=StopStreamResponse)
async def stop_stream(
    stream_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> StopStreamResponse:
    try:
        return await StreamingService(db).stop(stream_id, current_user)
    except StreamNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found") from exc