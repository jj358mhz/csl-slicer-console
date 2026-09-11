"""Client for Uplynk CSL slicer control endpoints.

Uses SHA1-signed body (legacy API key auth). Ports the call_vdms function
from the original csl-slicer-cli.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass

import requests


class CSLError(RuntimeError):
    """Raised when a CSL slicer control call fails."""


# The four control methods your CLI supported.
SLICER_METHODS: dict[str, str] = {
    "blackout": "/blackout",
    "content_start": "/content_start",
    "state": "/state",
    "status": "/status",
}


@dataclass(frozen=True)
class CSLResult:
    """Outcome of a single CSL slicer control call."""

    status_code: int
    body: dict | str  # dict if JSON, str if plaintext
    ok: bool

    @property
    def summary(self) -> str:
        """One-line human-readable summary for the UI/audit log."""
        if not self.ok:
            snippet = str(self.body)[:200]
            return f"HTTP {self.status_code}: {snippet}"
        if isinstance(self.body, dict):
            # Prefer common fields if present
            for key in ("state", "status", "message", "result"):
                if key in self.body:
                    return f"OK — {key}={self.body[key]}"
            return "OK"
        return f"OK — {str(self.body)[:200]}"


def _sign(api_key: str, method_path: str) -> dict:
    """Build the signed body Uplynk CSL expects."""
    secret = hashlib.sha1(api_key.encode()).hexdigest()
    timestamp = int(time.time())
    cnonce = secrets.randbelow(10**9)
    sig_input = f"{method_path}:{timestamp}:{cnonce}:{secret}"
    sig = base64.b64encode(hashlib.sha1(sig_input.encode()).digest()).decode()
    return {"timestamp": timestamp, "cnonce": cnonce, "sig": sig}


def call_slicer(
    base_uri: str,
    method_path: str,
    api_key: str,
    *,
    timeout: int = 10,
    session: requests.Session | None = None,
) -> CSLResult:
    """POST a signed request to a CSL slicer control endpoint.

    - base_uri: the slicer_api_url stored on the Slicer row
    - method_path: one of '/blackout', '/content_start', '/state', '/status'
    - api_key: the legacy Uplynk API key (already decrypted)
    """
    if method_path not in SLICER_METHODS.values():
        raise CSLError(f"Unknown method path: {method_path}")

    url = base_uri.rstrip("/") + method_path
    body = _sign(api_key, method_path)
    http = session or requests.Session()

    try:
        response = http.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise CSLError(f"CSL request failed: {e}") from e

    try:
        parsed: dict | str = response.json()
    except ValueError:
        parsed = response.text

    return CSLResult(
        status_code=response.status_code,
        body=parsed,
        ok=200 <= response.status_code < 300,
    )
