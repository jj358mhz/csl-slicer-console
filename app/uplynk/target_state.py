"""Client for the Uplynk v4 API — slicer target-state (Start/Stop).

Uses the same Scoped API Key (XAuth/JWT) auth as discovery, but requires
the `video.services.slicer.cloudslicer.live:write` scope on the key.

The v4 update endpoint accepts a partial-update PATCH; the only field we
set is target_state (Ready|Stopped), which the CSL orchestrator uses to
start or stop the slicer instance.

See:
- https://docs.uplynk.com/reference/update_live_cloud_slicer
- app.uplynk.states.RetrieveState (Ready and Stopped are also retrieve values)
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from app.uplynk.discovery import UplynkAPIError, build_jwt

UPDATE_ENDPOINT = "/api/v4/ingest/cloud-slicers/live/slicers/{slicer_id}"


# Maps the UI action name → the target_state value the API expects.
# Same shape as SLICER_METHODS in csl.py so routes.py can dispatch by lookup.
TARGET_STATE_METHODS: dict[str, str] = {
    "start": "Ready",
    "stop": "Stopped",
}


@dataclass(frozen=True)
class TargetStateResult:
    """Outcome of a single set-target-state call.

    Shaped like CSLResult so the audit-write path in service.py can treat
    them the same. body is dict on JSON responses, str on plaintext.
    """

    status_code: int
    body: dict | str
    ok: bool

    @property
    def summary(self) -> str:
        """One-line human-readable summary for the UI/audit log."""
        if not self.ok:
            snippet = str(self.body)[:200]
            return f"HTTP {self.status_code}: {snippet}"
        if isinstance(self.body, dict):
            # The 200 response echoes the whole slicer object; target_state
            # is the field the user actually cares about.
            ts = self.body.get("target_state")
            if ts:
                return f"OK — target_state={ts}"
            return "OK"
        return f"OK — {str(self.body)[:200]}"


def set_slicer_target_state(
    api_base: str,
    slicer_id: str,
    target_state: str,
    *,
    kid: str,
    sub: str,
    private_b64: str,
    scp: str,
    timeout: int = 15,
    session: requests.Session | None = None,
) -> TargetStateResult:
    """PATCH the v4 slicer with a new target_state.

    - api_base: e.g. "https://services.uplynk.com" (from app config)
    - slicer_id: the Uplynk slicer ID (e.g. "up_west_pa1")
    - target_state: one of TARGET_STATE_METHODS.values() ("Ready" or "Stopped")
    - kid/sub/private_b64/scp: scoped-key material (already decrypted)

    Raises UplynkAPIError on network failure. HTTP-level failures (4xx/5xx)
    return a TargetStateResult with ok=False so the audit path can persist
    them uniformly.
    """
    token = build_jwt(kid, sub, private_b64, scp)
    url = f"{api_base.rstrip('/')}{UPDATE_ENDPOINT.format(slicer_id=slicer_id)}"
    headers = {
        "X-Auth-Uplynk-Jwt": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = {"target_state": target_state}

    http = session or requests.Session()
    try:
        response = http.patch(url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise UplynkAPIError(f"Request to Uplynk failed: {e}") from e

    try:
        parsed: dict | str = response.json()
    except ValueError:
        parsed = response.text

    return TargetStateResult(
        status_code=response.status_code,
        body=parsed,
        ok=200 <= response.status_code < 300,
    )
