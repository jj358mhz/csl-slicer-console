"""Tests for the CSL slicer control client."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from app.uplynk.csl import SLICER_METHODS, CSLError, CSLResult, call_slicer


def _mock_response(status_code=200, json_data=None, text=""):
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.text = text or (str(json_data) if json_data else "")
    if json_data is not None:
        mock.json.return_value = json_data
    else:
        mock.json.side_effect = ValueError("no json")
    return mock


def test_slicer_methods_match_cli():
    assert set(SLICER_METHODS) == {"blackout", "content_start", "state", "status"}
    assert SLICER_METHODS["blackout"] == "/blackout"


def test_call_slicer_success_json():
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _mock_response(200, {"state": "running"})
    result = call_slicer(
        "https://ingest.example.com/slicer1", "/state", "test-key", session=session
    )
    assert result.ok is True
    assert result.status_code == 200
    assert result.body == {"state": "running"}
    assert "running" in result.summary


def test_call_slicer_success_text():
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _mock_response(200, text="OK")
    result = call_slicer(
        "https://ingest.example.com/slicer1", "/status", "test-key", session=session
    )
    assert result.ok is True
    assert result.body == "OK"


def test_call_slicer_signs_body():
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _mock_response(200, {})
    call_slicer("https://ingest.example.com/slicer1", "/blackout", "test-key", session=session)
    _, kwargs = session.post.call_args
    body = kwargs["json"]
    assert set(body) == {"timestamp", "cnonce", "sig"}
    assert isinstance(body["timestamp"], int)
    assert isinstance(body["cnonce"], int)
    assert len(body["sig"]) > 0


def test_call_slicer_url_composition():
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _mock_response(200, {})
    call_slicer("https://ingest.example.com/slicer1/", "/blackout", "k", session=session)
    args, _ = session.post.call_args
    # Trailing slash on base_uri is stripped; single slash between base and path
    assert args[0] == "https://ingest.example.com/slicer1/blackout"


def test_call_slicer_http_error_result():
    session = MagicMock(spec=requests.Session)
    session.post.return_value = _mock_response(500, text="server error")
    result = call_slicer("https://x/y", "/state", "k", session=session)
    assert result.ok is False
    assert result.status_code == 500
    assert "500" in result.summary


def test_call_slicer_network_error_raises():
    session = MagicMock(spec=requests.Session)
    session.post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(CSLError, match="CSL request failed"):
        call_slicer("https://x/y", "/state", "k", session=session)


def test_call_slicer_rejects_unknown_method():
    with pytest.raises(CSLError, match="Unknown method"):
        call_slicer("https://x/y", "/not-a-real-method", "k")


def test_result_summary_variants():
    assert CSLResult(200, {"state": "S"}, True).summary == "OK — state=S"
    assert CSLResult(200, "plain text", True).summary == "OK — plain text"
    assert "500" in CSLResult(500, "bad", False).summary
