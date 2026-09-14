"""Tests for the Uplynk v4 target-state client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app.uplynk.discovery import UplynkAPIError
from app.uplynk.target_state import (
    TARGET_STATE_METHODS,
    TargetStateResult,
    set_slicer_target_state,
)


def _mock_response(status_code=200, json_data=None, text=""):
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.text = text or (str(json_data) if json_data else "")
    if json_data is not None:
        mock.json.return_value = json_data
    else:
        mock.json.side_effect = ValueError("no json")
    return mock


# Valid ES256 private key for JWT signing in tests (same shape as test_discovery.py).
# Generated once, base64-encoded PEM.
_TEST_PRIVATE_B64 = (
    "LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0tCk1JR0hBZ0VBTUJNR0J5cUdTTTQ5"
    "QWdFR0NDcUdTTTQ5QXdFSEJHMHdhd0lCQVFRZ2VYbHJmOU8yQnZidkNZY2gKN0Za"
    "aWdmbTNhSlY5bXVSRWNoM1FLbllQMmVDaFJBTkNBQVRoUUt0MGQwYlpVc1lYQTF0"
    "eTFEZG52QUNKZHVWZQpNQXV0S1JVR1JYQ1BuUFhkYmpWaWFPWXR6WHZFRUw3Vjhz"
    "OWpXcE5nMHRRUUZEbTdWU2c3aVN5ZAotLS0tLUVORCBQUklWQVRFIEtFWS0tLS0t"
    "Cg=="
)


def _valid_kwargs(**overrides):
    kw = {
        "api_base": "https://services.uplynk.com",
        "slicer_id": "s1",
        "target_state": "Ready",
        "kid": "test-kid",
        "sub": "test-sub",
        "private_b64": _TEST_PRIVATE_B64,
        "scp": "video.services.slicer.cloudslicer.live:write",
    }
    kw.update(overrides)
    return kw


def test_target_state_methods_map_to_enum_values():
    """Keys are UI action names; values are the target_state strings the API accepts."""
    assert TARGET_STATE_METHODS == {"start": "Ready", "stop": "Stopped"}


def test_success_returns_ok_result():
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=200,
        json_data={"id": "s1", "target_state": "Ready"},
    )

    result = set_slicer_target_state(**_valid_kwargs(), session=session)

    assert isinstance(result, TargetStateResult)
    assert result.ok is True
    assert result.status_code == 200
    assert result.body == {"id": "s1", "target_state": "Ready"}


def test_success_summary_echoes_target_state():
    """The 200 response echoes the whole slicer object; summary picks target_state."""
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=200,
        json_data={"id": "s1", "target_state": "Stopped"},
    )

    result = set_slicer_target_state(**_valid_kwargs(target_state="Stopped"), session=session)
    assert result.summary == "OK — target_state=Stopped"


def test_url_composition_uses_slicer_id():
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=200, json_data={"target_state": "Ready"}
    )

    set_slicer_target_state(**_valid_kwargs(slicer_id="up_west_pa1"), session=session)

    call_url = session.patch.call_args.args[0]
    assert call_url == (
        "https://services.uplynk.com/api/v4/ingest/cloud-slicers/live/slicers/up_west_pa1"
    )


def test_patch_body_carries_target_state():
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=200, json_data={"target_state": "Stopped"}
    )

    set_slicer_target_state(**_valid_kwargs(target_state="Stopped"), session=session)

    body = session.patch.call_args.kwargs["json"]
    assert body == {"target_state": "Stopped"}


def test_sends_xauth_header():
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=200, json_data={"target_state": "Ready"}
    )

    set_slicer_target_state(**_valid_kwargs(), session=session)

    headers = session.patch.call_args.kwargs["headers"]
    assert "X-Auth-Uplynk-Jwt" in headers
    assert headers["Content-Type"] == "application/json"


def test_403_returns_error_result_not_raise():
    """HTTP errors don't raise — they land in the result so audit can persist them."""
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=403,
        json_data={
            "@type": "Error",
            "code": "forbidden",
            "status_code": 403,
            "title": "Forbidden",
        },
    )

    result = set_slicer_target_state(**_valid_kwargs(), session=session)

    assert result.ok is False
    assert result.status_code == 403
    assert "HTTP 403" in result.summary


def test_404_returns_error_result_not_raise():
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=404,
        json_data={
            "@type": "Error",
            "code": "not_found",
            "status_code": 404,
            "title": "Not Found",
        },
    )

    result = set_slicer_target_state(**_valid_kwargs(), session=session)
    assert result.ok is False
    assert result.status_code == 404


def test_network_error_raises_uplynk_api_error():
    """Unlike HTTP errors, connection-level failures raise."""
    session = MagicMock(spec=requests.Session)
    session.patch.side_effect = requests.ConnectionError("connection refused")

    with pytest.raises(UplynkAPIError, match="Request to Uplynk failed"):
        set_slicer_target_state(**_valid_kwargs(), session=session)


def test_non_json_response_falls_back_to_text():
    session = MagicMock(spec=requests.Session)
    session.patch.return_value = _mock_response(
        status_code=500,
        text="internal server error",
    )

    result = set_slicer_target_state(**_valid_kwargs(), session=session)
    assert result.ok is False
    assert result.body == "internal server error"
