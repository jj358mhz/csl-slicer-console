"""Client for the Uplynk v4 API — slicer discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


class UplynkAPIError(RuntimeError):
    """Raised when the Uplynk v4 API returns an error or invalid response."""


@dataclass(frozen=True)
class DiscoveredSlicer:
    """A slicer as returned by the v4 list endpoint.

    Only the fields we care about for storage; the raw response has more.
    """

    slicer_id: str
    slicer_api_url: str
    region: str | None
    protocol: str | None
    plugin_id: str | None
    plugin_version: str | None
    state: str | None
    description: str | None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "DiscoveredSlicer":
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
        )


class UplynkDiscoveryClient:
    """Minimal client for GET /api/v4/ingest/cloud-slicers/live/slicers.

    Uses Scoped API Key (Bearer) auth.
    """

    LIST_ENDPOINT = "/api/v4/ingest/cloud-slicers/live/slicers"

    def __init__(
        self,
        api_base: str,
        scoped_api_key: str,
        timeout: int = 15,
        session: requests.Session | None = None,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.scoped_api_key = scoped_api_key
        self.timeout = timeout
        self._session = session or requests.Session()

    def list_slicers(self) -> list[DiscoveredSlicer]:
        """Fetch and parse all live cloud slicers for this account."""
        url = f"{self.api_base}{self.LIST_ENDPOINT}"
        headers = {
            "Authorization": f"Bearer {self.scoped_api_key}",
            "Accept": "application/json",
        }

        try:
            response = self._session.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as e:
            raise UplynkAPIError(f"Request to Uplynk failed: {e}") from e

        if response.status_code == 401:
            raise UplynkAPIError("Uplynk API rejected the scoped API key (401)")
        if response.status_code == 403:
            raise UplynkAPIError(
                "Scoped API key lacks required scope "
                "(video.services.slicer.cloudslicer.live:read)"
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
