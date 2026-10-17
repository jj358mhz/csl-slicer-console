"""Tests for the captured slicer configuration (#49)."""

from __future__ import annotations

import json

from app.uplynk.slicer_config import SlicerConfig, parse_configuration

SAMPLE_ITEM = {
    "id": "slicer30158",
    "protocol": "RIST",
    "rist_profile": "MAIN",
    "region": "us-east-1",
    "slicer_version": "23.04.1",
    "encoding_profile_id": "abc123",
    "source_ip": "203.0.113.7",
    "target_state": "Ready",
    "streaming_url": "rist://ingest.example.com:5000",
    "slicer_api_url": "https://ingest.example.com:443/slicer30158",
    "passphrase": "s3cret",
    "plugin": {"id": "tennis-scte35", "version": "1.0"},
    "created_at": "2026-01-02T03:04:05Z",
    "description": "Court 1",
    "configuration": "port: 5000\ndelay: 2.0\n",
}


def test_parse_configuration_splits_on_first_colon_only():
    rows = parse_configuration("url: rtmp://host:1935/live\nport: 5000")
    assert rows == [("url", "rtmp://host:1935/live"), ("port", "5000")]


def test_parse_configuration_keeps_unparseable_lines_and_skips_blanks():
    rows = parse_configuration("a: 1\n\njust some text\n: no key\n")
    assert rows == [("a", "1"), (None, "just some text"), (None, ": no key")]


def test_parse_configuration_empty_value_keeps_key():
    assert parse_configuration("flag:") == [("flag", "")]


def test_parse_configuration_none():
    assert parse_configuration(None) == []


def test_from_api_reads_plugin_and_fields():
    cfg = SlicerConfig.from_api(SAMPLE_ITEM)
    assert cfg.plugin_id == "tennis-scte35"
    assert cfg.plugin_version == "1.0"
    assert cfg.encoding_profile_id == "abc123"
    assert cfg.passphrase == "s3cret"
    assert cfg.settings == [("port", "5000"), ("delay", "2.0")]


def test_from_api_missing_or_null_plugin():
    assert SlicerConfig.from_api({"plugin": None}).plugin_id is None
    assert SlicerConfig.from_api({}).plugin_version is None


def test_json_round_trip_ignores_unknown_keys():
    cfg = SlicerConfig.from_api(SAMPLE_ITEM)
    raw = json.loads(cfg.to_json())
    raw["added_in_a_later_release"] = "x"
    assert SlicerConfig.from_json(json.dumps(raw)) == cfg
