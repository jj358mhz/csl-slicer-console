"""Canonical state vocabularies for Uplynk CSL slicers.

Uplynk exposes two overlapping vocabularies for slicer state, from two
different endpoints, and this module documents both and maps between them.

RETRIEVE VOCABULARY (v4 API, X-Auth-Uplynk-JWT scoped-key auth)
    Endpoint: GET /api/v4/ingest/cloud-slicers/live/slicers/{id}
    Field:    status.state (string)
    Values:   Stopped, Initializing, Ready, Slicing, Blackout,
              ReplacingContent, AdBreak, Stopping

    This is what `poll_slicer_state()` writes to `slicer.last_state`, and
    what the tile badge renders. Covers both operational states (Slicing,
    AdBreak, ReplacingContent, Blackout) and CSL orchestration lifecycle
    (Stopped, Initializing, Ready, Stopping) — the latter describe the
    cloud-slicer instance itself, not the running daemon.
    Docs: https://docs.uplynk.com/reference/retrieve_live_cloud_slicer

CONTROL VOCABULARY (SHA1-signed body, legacy api_key auth)
    Endpoint: POST {slicer_api_url}/state
    Fields:   state_name (string) and state (int 0-3), paired
    Values:   Capture (0), Ad (1), Replace (2), Blackout (3)

    This is what `call_slicer()` returns from the SHA1 control endpoint,
    which lands in `AuditEvent.response_snippet` only — never rendered as
    a badge. Only exists while the daemon is running, so it doesn't cover
    the CSL lifecycle states.
    Docs: https://docs.uplynk.com/reference/state

Reconciliation
    The four operational states have direct 1:1 mappings, but only
    `Blackout` spells identically in both vocabularies. The four CSL
    lifecycle states in the retrieve vocab have no control equivalent.

    Where each vocabulary surfaces:
    - Tile badges           → retrieve vocab (via slicer.last_state)
    - Audit log snippets    → control vocab (via AuditEvent.response_snippet)
    - Future diagnostic UI  → control vocab (not yet built; see issue #11)
"""

from __future__ import annotations


class RetrieveState:
    """Values of `status.state` from the v4 retrieve/list endpoints.

    Reference these constants instead of hard-coding strings so that
    typos surface at import time rather than at a failed comparison.
    """

    STOPPED = "Stopped"
    INITIALIZING = "Initializing"
    READY = "Ready"
    SLICING = "Slicing"
    BLACKOUT = "Blackout"
    REPLACING_CONTENT = "ReplacingContent"
    AD_BREAK = "AdBreak"
    STOPPING = "Stopping"


KNOWN_RETRIEVE_STATES: frozenset[str] = frozenset(
    {
        RetrieveState.STOPPED,
        RetrieveState.INITIALIZING,
        RetrieveState.READY,
        RetrieveState.SLICING,
        RetrieveState.BLACKOUT,
        RetrieveState.REPLACING_CONTENT,
        RetrieveState.AD_BREAK,
        RetrieveState.STOPPING,
    }
)


class ControlState:
    """Values of `state_name` from the SHA1 POST /state control endpoint."""

    CAPTURE = "Capture"
    AD = "Ad"
    REPLACE = "Replace"
    BLACKOUT = "Blackout"


KNOWN_CONTROL_STATES: frozenset[str] = frozenset(
    {
        ControlState.CAPTURE,
        ControlState.AD,
        ControlState.REPLACE,
        ControlState.BLACKOUT,
    }
)


# Paired numeric `state` field returned alongside `state_name`.
CONTROL_STATE_INT: dict[str, int] = {
    ControlState.CAPTURE: 0,
    ControlState.AD: 1,
    ControlState.REPLACE: 2,
    ControlState.BLACKOUT: 3,
}


# Retrieve → Control mapping. Retrieve-only lifecycle states map to None
# because they have no equivalent in the SHA1 control vocabulary.
RETRIEVE_TO_CONTROL: dict[str, str | None] = {
    RetrieveState.SLICING: ControlState.CAPTURE,
    RetrieveState.AD_BREAK: ControlState.AD,
    RetrieveState.REPLACING_CONTENT: ControlState.REPLACE,
    RetrieveState.BLACKOUT: ControlState.BLACKOUT,
    RetrieveState.STOPPED: None,
    RetrieveState.INITIALIZING: None,
    RetrieveState.READY: None,
    RetrieveState.STOPPING: None,
}