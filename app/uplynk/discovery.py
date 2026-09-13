"""Client for the Uplynk v4 API — slicer discovery.

Uses Uplynk's Scoped API Key (XAuth) authentication: build an ES256-signed JWT
per request from KID/SUB/PRIVATE_B64/SCP, send it in X-Auth-Uplynk-Jwt.
See https://docs.uplynk.com/reference/scoped-api-keys.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import jwt
import requests
from cryptography.hazmat.primitives import serialization


class UplynkAPIError(RuntimeError):
    """Raised when the Uplynk v4 API returns an error or invalid response."""


@dataclass(frozen=True)
class DiscoveredSlicer:
    """A slicer as returned by the v4 list/retrieve endpoints."""

    slicer_id: str
    slicer_api_url: str
    region: str | None
    protocol: str | None
    plugin_id: str | None
    plugin_version: str | None
    state: str | None
    description: str | None
    connection_mode: str | None  # "push" | "pull" | None. Only meaningful for SRT.

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> DiscoveredSlicer:
        plugin = item.get("plugin") or {}
        status = item.get("status") or {}
        return cls(
            slicer_id=item["id"],
            slicer_api_url=item["slicer_api_url"],
            region=item.get("region"),
            protocol=item.get("protocol"),
            plugin_id=plugin.get("id"),
            plugin_version=plugin.get("version"),
            state=status.get("state"),
            description=item.get("description"),
            connection_mode=item.get("connection_mode"),
        )


def build_jwt(
    kid: str,
    sub: str,
    private_b64: str,
    scp: str,
    ttl_seconds: int = 300,
) -> str:
    """Build an ES256-signed JWT for Uplynk scoped-key auth.

    - kid/sub/private_b64/scp come from the .env file downloaded from Uplynk.
    - ttl_seconds sets the token lifetime (default 5 min per Uplynk docs).
    """
    try:
        private_bytes = base64.b64decode(private_b64)
    except (ValueError, TypeError) as e:
        raise UplynkAPIError(f"Invalid PRIVATE_B64 (not valid base64): {e}") from e

    try:
        private_key = serialization.load_pem_private_key(private_bytes, password=None)
    except Exception as e:
        raise UplynkAPIError(f"Could not load private key: {e}") from e

    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + ttl_seconds,
        "sub": sub,
        "scp": [s.strip() for s in scp.split(",") if s.strip()],
    }
    headers = {"kid": kid, "typ": "JWT"}

    return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)


class UplynkDiscoveryClient:
    """Minimal client for GET /api/v4/ingest/cloud-slicers/live/slicers.

    Uses Scoped API Key (XAuth/JWT) auth. Requires the
    `video.services.ingest.cloudslicer.live:read` scope on the key.
    """

    LIST_ENDPOINT = "/api/v4/ingest/cloud-slicers/live/slicers"
    RETRIEVE_ENDPOINT = "/api/v4/ingest/cloud-slicers/live/slicers/{slicer_id}"

    def __init__(
        self,
        api_base: str,
        kid: str,
        sub: str,
        private_b64: str,
        scp: str,
        timeout: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.kid = kid
        self.sub = sub
        self.private_b64 = private_b64
        self.scp = scp
        self.timeout = timeout
        self._session = session or requests.Session()

    def list_slicers(self) -> list[DiscoveredSlicer]:
        """Fetch and parse all live cloud slicers for this account."""
        token = build_jwt(self.kid, self.sub, self.private_b64, self.scp)
        url = f"{self.api_base}{self.LIST_ENDPOINT}"
        headers = {
            "X-Auth-Uplynk-Jwt": token,
            "Accept": "application/json",
        }

        try:
            response = self._session.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise UplynkAPIError(f"Request to Uplynk failed: {e}") from e

        if response.status_code == 401:
            raise UplynkAPIError("Uplynk API rejected the JWT (401) — check KID/SUB/PRIVATE_B64")
        if response.status_code == 403:
            raise UplynkAPIError(
                "JWT lacks required scope (video.services.ingest.cloudslicer.live:read)"
            )
        if response.status_code != 200:
            raise UplynkAPIError(
                f"Uplynk API returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise UplynkAPIError("Uplynk API returned non-JSON response") from e

        items = payload.get("items")
        if not isinstance(items, list):
            raise UplynkAPIError("Uplynk API response missing 'items' list")

        try:
            return [DiscoveredSlicer.from_api(item) for item in items]
        except KeyError as e:
            raise UplynkAPIError(f"Uplynk API item missing required field: {e}") from e

    def retrieve_slicer(self, slicer_id: str) -> DiscoveredSlicer:
        """Fetch and parse a single live cloud slicer's current state.

        Uses the v4 retrieve endpoint. Same auth as `list_slicers`.
        """
        token = build_jwt(self.kid, self.sub, self.private_b64, self.scp)
        url = f"{self.api_base}{self.RETRIEVE_ENDPOINT.format(slicer_id=slicer_id)}"
        headers = {
            "X-Auth-Uplynk-Jwt": token,
            "Accept": "application/json",
        }

        try:
            response = self._session.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise UplynkAPIError(f"Request to Uplynk failed: {e}") from e

        if response.status_code == 401:
            raise UplynkAPIError("Uplynk API rejected the JWT (401) — check KID/SUB/PRIVATE_B64")
        if response.status_code == 403:
            raise UplynkAPIError(
                "JWT lacks required scope (video.services.slicer.cloudslicer.live:read)"
            )
        if response.status_code == 404:
            raise UplynkAPIError(f"Slicer {slicer_id!r} not found")
        if response.status_code != 200:
            raise UplynkAPIError(
                f"Uplynk API returned HTTP {response.status_code}: {response.text[:200]}"
            )

        try:
            payload = response.json()
        except ValueError as e:
            raise UplynkAPIError("Uplynk API returned non-JSON response") from e

        try:
            return DiscoveredSlicer.from_api(payload)
        except KeyError as e:
            raise UplynkAPIError(f"Uplynk API response missing required field: {e}") from e
