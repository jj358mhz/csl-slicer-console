"""Tests for the Uplynk v4 discovery client (JWT/XAuth auth)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import jwt
import pytest
import requests

from app.uplynk.discovery import (
    DiscoveredSlicer,
    UplynkAPIError,
    UplynkDiscoveryClient,
    build_jwt,
)

SAMPLE_ITEM = {
    "@id": "/api/v4/ingest/cloud-slicers/live/slicers",
    "@type": "LiveCloudSlicer",
    "id": "slicer30158",
    "slicer_api_url": "https://ingest-prod-0-us-east-1-3.csl.uplynk.net:443/slicer30158",
    "region": "us-east-1",
    "protocol": "SRT",
    "connection_mode": "pull",
    "status": {"state": "Stopped"},
    "plugin": {"id": "tennis-scte35", "version": "1.0"},
    "description": "Test slicer",
}


def _mock_response(status_code=200, json_data=None, text=""):
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.text = text or (str(json_data) if json_data else "")
    if json_data is not None:
        mock.json.return_value = json_data
    else:
        mock.json.side_effect = ValueError("no json")
    return mock


def _client(ec_keypair, response):
    session = MagicMock(spec=requests.Session)
    session.get.return_value = response
    return UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        kid="test-kid",
        sub="test-sub",
        private_b64=ec_keypair["private_b64"],
        scp="video.services.ingest.cloudslicer.live:read",
        session=session,
    )


def test_build_jwt_produces_valid_es256(ec_keypair):
    token = build_jwt(
        kid="k1",
        sub="owner-abc",
        private_b64=ec_keypair["private_b64"],
        scp="video.services.ingest.cloudslicer.live:read,video.services.assets:read",
    )
    decoded = jwt.decode(
        token,
        ec_keypair["public_key"],
        algorithms=["ES256"],
    )
    assert decoded["sub"] == "owner-abc"
    assert "video.services.ingest.cloudslicer.live:read" in decoded["scp"]
    assert "video.services.assets:read" in decoded["scp"]
    assert "iat" in decoded
    assert "exp" in decoded

    header = jwt.get_unverified_header(token)
    assert header["kid"] == "k1"
    assert header["alg"] == "ES256"


def test_build_jwt_invalid_base64_raises():
    with pytest.raises(UplynkAPIError, match="base64"):
        build_jwt(kid="k", sub="s", private_b64="!!!not-b64!!!", scp="x")


def test_build_jwt_valid_b64_but_not_a_key():
    junk = base64.b64encode(b"nope").decode()
    with pytest.raises(UplynkAPIError, match="Could not load private key"):
        build_jwt(kid="k", sub="s", private_b64=junk, scp="x")


def test_discovered_slicer_from_api():
    ds = DiscoveredSlicer.from_api(SAMPLE_ITEM)
    assert ds.slicer_id == "slicer30158"
    assert ds.region == "us-east-1"
    assert ds.plugin_id == "tennis-scte35"
    assert ds.state == "Stopped"
    assert ds.connection_mode == "pull"


def test_discovered_slicer_connection_mode_absent_when_not_srt():
    item = {**SAMPLE_ITEM, "protocol": "RTMP"}
    del item["connection_mode"]
    ds = DiscoveredSlicer.from_api(item)
    assert ds.protocol == "RTMP"
    assert ds.connection_mode is None


def test_discovered_slicer_missing_optional_fields():
    ds = DiscoveredSlicer.from_api({"id": "s1", "slicer_api_url": "u"})
    assert ds.region is None
    assert ds.plugin_id is None
    assert ds.thumb_url is None


def test_discovered_slicer_parses_thumb_url():
    item = {**SAMPLE_ITEM, "thumb_url": "http://cf.cdn.uplynk.com/slices/upl123.jpg"}
    ds = DiscoveredSlicer.from_api(item)
    assert ds.thumb_url == "http://cf.cdn.uplynk.com/slices/upl123.jpg"


def test_discovered_slicer_thumb_url_absent_is_none():
    ds = DiscoveredSlicer.from_api(SAMPLE_ITEM)
    assert ds.thumb_url is None


def test_discovered_slicer_thumb_url_empty_string_becomes_none():
    """Uplynk shouldn't send an empty thumb_url, but normalize it if it does."""
    item = {**SAMPLE_ITEM, "thumb_url": ""}
    ds = DiscoveredSlicer.from_api(item)
    assert ds.thumb_url is None


def test_list_slicers_success(ec_keypair):
    client = _client(ec_keypair, _mock_response(200, {"items": [SAMPLE_ITEM]}))
    result = client.list_slicers()
    assert len(result) == 1
    assert isinstance(result[0], DiscoveredSlicer)


def test_list_slicers_sends_xauth_header(ec_keypair):
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _mock_response(200, {"items": []})
    client = UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        kid="k1",
        sub="s1",
        private_b64=ec_keypair["private_b64"],
        scp="x:read",
        session=session,
    )
    client.list_slicers()
    _, kwargs = session.get.call_args
    assert "X-Auth-Uplynk-Jwt" in kwargs["headers"]
    assert "Authorization" not in kwargs["headers"]
    # Header contents should be a valid JWT
    header = jwt.get_unverified_header(kwargs["headers"]["X-Auth-Uplynk-Jwt"])
    assert header["kid"] == "k1"


def test_list_slicers_401_raises(ec_keypair):
    client = _client(ec_keypair, _mock_response(401, text="bad token"))
    with pytest.raises(UplynkAPIError, match="401"):
        client.list_slicers()


def test_list_slicers_403_mentions_scope(ec_keypair):
    client = _client(ec_keypair, _mock_response(403, text="forbidden"))
    with pytest.raises(UplynkAPIError, match="scope"):
        client.list_slicers()


def test_list_slicers_500_raises(ec_keypair):
    client = _client(ec_keypair, _mock_response(500, text="server error"))
    with pytest.raises(UplynkAPIError, match="500"):
        client.list_slicers()


def test_list_slicers_network_error_raises(ec_keypair):
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.ConnectionError("boom")
    client = UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        kid="k",
        sub="s",
        private_b64=ec_keypair["private_b64"],
        scp="x",
        session=session,
    )
    with pytest.raises(UplynkAPIError, match="Request to Uplynk failed"):
        client.list_slicers()


def test_retrieve_slicer_success(ec_keypair):
    client = _client(ec_keypair, _mock_response(200, SAMPLE_ITEM))
    result = client.retrieve_slicer("slicer30158")
    assert isinstance(result, DiscoveredSlicer)
    assert result.slicer_id == "slicer30158"
    assert result.state == "Stopped"
    assert result.connection_mode == "pull"


def test_retrieve_slicer_hits_correct_url(ec_keypair):
    session = MagicMock(spec=requests.Session)
    session.get.return_value = _mock_response(200, SAMPLE_ITEM)
    client = UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        kid="k1",
        sub="s1",
        private_b64=ec_keypair["private_b64"],
        scp="x:read",
        session=session,
    )
    client.retrieve_slicer("my-slicer")
    args, _ = session.get.call_args
    assert args[0] == (
        "https://services.uplynk.com/api/v4/ingest/cloud-slicers/live/slicers/my-slicer"
    )


def test_retrieve_slicer_401_raises(ec_keypair):
    client = _client(ec_keypair, _mock_response(401, text="bad token"))
    with pytest.raises(UplynkAPIError, match="401"):
        client.retrieve_slicer("s1")


def test_retrieve_slicer_403_mentions_scope(ec_keypair):
    client = _client(ec_keypair, _mock_response(403, text="forbidden"))
    with pytest.raises(UplynkAPIError, match="scope"):
        client.retrieve_slicer("s1")


def test_retrieve_slicer_404_names_slicer(ec_keypair):
    client = _client(ec_keypair, _mock_response(404, text="not found"))
    with pytest.raises(UplynkAPIError, match="ghost-slicer"):
        client.retrieve_slicer("ghost-slicer")


def test_retrieve_slicer_malformed_json_raises(ec_keypair):
    client = _client(ec_keypair, _mock_response(200, json_data=None, text="not json"))
    with pytest.raises(UplynkAPIError, match="non-JSON"):
        client.retrieve_slicer("s1")


def test_retrieve_slicer_missing_required_field_raises(ec_keypair):
    client = _client(ec_keypair, _mock_response(200, {"protocol": "SRT"}))
    with pytest.raises(UplynkAPIError, match="missing required field"):
        client.retrieve_slicer("s1")


def test_retrieve_slicer_network_error_raises(ec_keypair):
    session = MagicMock(spec=requests.Session)
    session.get.side_effect = requests.ConnectionError("boom")
    client = UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        kid="k",
        sub="s",
        private_b64=ec_keypair["private_b64"],
        scp="x",
        session=session,
    )
    with pytest.raises(UplynkAPIError, match="Request to Uplynk failed"):
        client.retrieve_slicer("s1")
