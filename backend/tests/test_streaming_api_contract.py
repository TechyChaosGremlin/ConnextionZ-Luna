"""Public contract checks for the initial streaming REST API."""

from app.main import app
from features.streaming.schemas import StartStreamRequest, StreamResponse


def test_streaming_openapi_exposes_required_operations() -> None:
    paths = app.openapi()["paths"]

    assert "post" in paths["/api/streams"]
    assert "get" in paths["/api/streams"]
    assert "get" in paths["/api/streams/{stream_id}"]
    assert "post" in paths["/api/streams/{stream_id}/stop"]


def test_start_contract_uses_input_source_and_platforms() -> None:
    schema = StartStreamRequest.model_json_schema()

    assert set(schema["properties"]) == {"input_source", "platforms"}
    assert "input_url" not in schema["properties"]


def test_stream_response_never_serializes_sources_or_credentials() -> None:
    properties = StreamResponse.model_json_schema()["properties"]

    assert "input_source" not in properties
    assert "input_url" not in properties
    assert "stream_key" not in properties
    assert "access_token" not in properties
    assert "refresh_token" not in properties