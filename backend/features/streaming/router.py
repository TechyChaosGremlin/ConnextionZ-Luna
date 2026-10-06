"""Authenticated REST API for the FFmpeg streaming lifecycle."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db_session
from app.models.user import User
from app.rate_limits import ActionRateLimitExceeded, RATE_LIMIT_MESSAGE
from features.auth.middleware import get_current_active_user
from features.streaming.audience_service import AudienceService
from features.streaming.chat_service import StreamChatService
from features.streaming.schemas import (
    StartStreamRequest,
    StreamChatMessageRequest,
    StreamChatMessageResponse,
    StreamResponse,
    StreamStatusResponse,
    StreamSubscriptionRequest,
    StreamSubscriptionResponse,
    StopStreamResponse,
    ViewerJoinRequest,
    ViewerSessionResponse,
    ViewerSessionUpdateRequest,
)
from features.streaming.service import StreamingService, StreamNotFoundError, StreamStartError
from features.streaming.subscription_service import StreamSubscriptionService

router = APIRouter(prefix="/api/streams", tags=["streams"])


@router.post("", response_model=StreamResponse, status_code=status.HTTP_201_CREATED)
async def start_stream(
    request: StartStreamRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> StreamResponse:
    try:
        return await StreamingService(db).start(request, current_user)
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found"
        ) from exc


@router.post("/{stream_id}/viewers/join", response_model=ViewerSessionResponse)
async def join_stream_viewer(
    stream_id: uuid.UUID,
    request: ViewerJoinRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> ViewerSessionResponse:
    try:
        return await AudienceService(db).join(stream_id, request.client_session_id, current_user)
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


@router.post(
    "/{stream_id}/viewers/{viewer_session_id}/heartbeat", response_model=ViewerSessionResponse
)
async def heartbeat_stream_viewer(
    stream_id: uuid.UUID,
    viewer_session_id: uuid.UUID,
    request: ViewerSessionUpdateRequest | None = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> ViewerSessionResponse:
    try:
        return await AudienceService(db).heartbeat(stream_id, viewer_session_id, current_user)
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


@router.post("/{stream_id}/viewers/{viewer_session_id}/leave", response_model=ViewerSessionResponse)
async def leave_stream_viewer(
    stream_id: uuid.UUID,
    viewer_session_id: uuid.UUID,
    request: ViewerSessionUpdateRequest | None = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> ViewerSessionResponse:
    try:
        return await AudienceService(db).leave(stream_id, viewer_session_id, current_user)
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


@router.post(
    "/{stream_id}/chat",
    response_model=StreamChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_stream_chat_message(
    stream_id: uuid.UUID,
    request: StreamChatMessageRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> StreamChatMessageResponse:
    try:
        return await StreamChatService(db).send(stream_id, request.body, current_user)
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc


@router.post("/{stream_id}/stop", response_model=StopStreamResponse)
async def stop_stream(
    stream_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> StopStreamResponse:
    try:
        return await StreamingService(db).stop(stream_id, current_user)
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except StreamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stream not found"
        ) from exc


@router.post("/{stream_id}/subscriptions", response_model=StreamSubscriptionResponse)
async def subscribe_to_stream(
    stream_id: uuid.UUID,
    request: StreamSubscriptionRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
) -> StreamSubscriptionResponse:
    try:
        return await StreamSubscriptionService(db).subscribe(
            stream_id, request.creator_id, current_user
        )
    except ActionRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=RATE_LIMIT_MESSAGE,
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
