"""Tests for the Uplynk v4 discovery client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app.uplynk.discovery import (
    DiscoveredSlicer,
    UplynkAPIError,
    UplynkDiscoveryClient,
)

# The exact schema from the Uplynk docs.
SAMPLE_ITEM = {
    "@id": "/api/v4/ingest/cloud-slicers/live/slicers",
    "@type": "LiveCloudSlicer",
    "id": "slicer30158",
    "configuration": "some-config",
    "source_ip": "10.0.0.1",
    "slicer_version": "5.2.1",
    "encoding_profile_id": "ep-abc",
    "region": "us-east-1",
    "protocol": "SRT",
    "rist_profile": "SIMPLE",
    "streaming_url": "srt://example.com:1234",
    "slicer_api_url": "https://ingest-prod-0-us-east-1-3.csl.uplynk.net:443/slicer30158",
    "passphrase": "hidden",
    "target_state": "Ready",
    "status": {"state": "Stopped"},
    "plugin": {"id": "tennis-scte35", "version": "1.0"},
    "created_at": "2026-09-10T19:56:08.878Z",
    "description": "Test slicer",
    "thumb_url": "https://example.com/thumb.png",
}


def _mock_response(status_code: int = 200, json_data: dict | None = None, text: str = ""):
    """Build a fake requests.Response."""
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.text = text or (str(json_data) if json_data else "")
    if json_data is not None:
        mock.json.return_value = json_data
    else:
        mock.json.side_effect = ValueError("no json")
    return mock


def _client_with_response(mock_response) -> UplynkDiscoveryClient:
    session = MagicMock(spec=requests.Session)
    session.get.return_value = mock_response
    return UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        scoped_api_key="test-key",
        session=session,
    )


def test_discovered_slicer_from_api_full_item():
    ds = DiscoveredSlicer.from_api(SAMPLE_ITEM)
    assert ds.slicer_id == "slicer30158"
    assert ds.slicer_api_url.endswith("/slicer30158")
    assert ds.region == "us-east-1"
    assert ds.protocol == "SRT"
    assert ds.plugin_id == "tennis-scte35"
    assert ds.plugin_version == "1.0"
    assert ds.state == "Stopped"
    assert ds.description == "Test slicer"


def test_discovered_slicer_handles_missing_optional_fields():
    item = {"id": "s1", "slicer_api_url": "https://example.com/s1"}
    ds = DiscoveredSlicer.from_api(item)
    assert ds.slicer_id == "s1"
    assert ds.region is None
    assert ds.plugin_id is None
    assert ds.state is None


def test_list_slicers_success():
    payload = {"items": [SAMPLE_ITEM, SAMPLE_ITEM], "total_items": 2}
    client = _client_with_response(_mock_response(200, payload))
    result = client.list_slicers()
    assert len(result) == 2
    assert all(isinstance(s, DiscoveredSlicer) for s in result)


def test_list_slicers_empty():
    client = _client_with_response(_mock_response(200, {"items": [], "total_items": 0}))
    assert client.list_slicers() == []


def test_list_slicers_sends_bearer_auth():
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _mock_response(200, {"items": []})
    client = UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        scoped_api_key="secret-token-abc",
        session=session,
    )
    client.list_slicers()
    args, kwargs = session.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token-abc"


def test_list_slicers_401_raises():
    client = _client_with_response(_mock_response(401, text="unauthorized"))
    with pytest.raises(UplynkAPIError, match="401"):
        client.list_slicers()


def test_list_slicers_403_mentions_scope():
    client = _client_with_response(_mock_response(403, text="forbidden"))
    with pytest.raises(UplynkAPIError, match="scope"):
        client.list_slicers()


def test_list_slicers_500_raises():
    client = _client_with_response(_mock_response(500, text="server error"))
    with pytest.raises(UplynkAPIError, match="500"):
        client.list_slicers()


def test_list_slicers_network_error_raises():
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.ConnectionError("boom")
    client = UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        scoped_api_key="k",
        session=session,
    )
    with pytest.raises(UplynkAPIError, match="Request to Uplynk failed"):
        client.list_slicers()


def test_list_slicers_malformed_response():
    client = _client_with_response(_mock_response(200, {"total_items": 0}))
    with pytest.raises(UplynkAPIError, match="items"):
        client.list_slicers()
