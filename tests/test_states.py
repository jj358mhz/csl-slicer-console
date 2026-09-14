"""Tests for the canonical state vocabulary module."""

from __future__ import annotations

from app.uplynk.states import (
    CONTROL_STATE_INT,
    KNOWN_CONTROL_STATES,
    KNOWN_RETRIEVE_STATES,
    RETRIEVE_TO_CONTROL,
    ControlState,
    RetrieveState,
)


def test_retrieve_states_match_uplynk_docs():
    """The 8 values documented at docs.uplynk.com/reference/retrieve_live_cloud_slicer."""
    assert {
        "Stopped",
        "Initializing",
        "Ready",
        "Slicing",
        "Blackout",
        "ReplacingContent",
        "AdBreak",
        "Stopping",
    } == KNOWN_RETRIEVE_STATES


def test_control_states_match_uplynk_docs():
    """The 4 state_name values documented at docs.uplynk.com/reference/state."""
    assert {"Capture", "Ad", "Replace", "Blackout"} == KNOWN_CONTROL_STATES


def test_control_state_int_is_canonical():
    """state_name → state int mapping matches the SHA1 /state response schema."""
    assert CONTROL_STATE_INT == {
        "Capture": 0,
        "Ad": 1,
        "Replace": 2,
        "Blackout": 3,
    }


def test_control_state_int_covers_every_known_control_state():
    """Every ControlState has a paired numeric value — no drift between the two."""
    assert set(CONTROL_STATE_INT.keys()) == KNOWN_CONTROL_STATES


def test_retrieve_to_control_covers_every_known_retrieve_state():
    """Every RetrieveState is present in the mapping (mapped or explicit None)."""
    assert set(RETRIEVE_TO_CONTROL.keys()) == KNOWN_RETRIEVE_STATES


def test_operational_retrieve_states_map_to_control():
    """The four operational retrieve states map to their control equivalents."""
    assert RETRIEVE_TO_CONTROL[RetrieveState.SLICING] == ControlState.CAPTURE
    assert RETRIEVE_TO_CONTROL[RetrieveState.AD_BREAK] == ControlState.AD
    assert RETRIEVE_TO_CONTROL[RetrieveState.REPLACING_CONTENT] == ControlState.REPLACE
    assert RETRIEVE_TO_CONTROL[RetrieveState.BLACKOUT] == ControlState.BLACKOUT


def test_lifecycle_retrieve_states_have_no_control_equivalent():
    """The four CSL orchestration lifecycle states map to None."""
    assert RETRIEVE_TO_CONTROL[RetrieveState.STOPPED] is None
    assert RETRIEVE_TO_CONTROL[RetrieveState.INITIALIZING] is None
    assert RETRIEVE_TO_CONTROL[RetrieveState.READY] is None
    assert RETRIEVE_TO_CONTROL[RetrieveState.STOPPING] is None


def test_blackout_is_the_only_identical_spelling():
    """Only 'Blackout' appears verbatim in both vocabularies."""
    identical = KNOWN_RETRIEVE_STATES & KNOWN_CONTROL_STATES
    assert identical == {"Blackout"}
