"""What a slicer update's outcome means, in words an admin can act on (#71).

Turns the HTTP status of PATCH /slicers/{id} into what happened and what to do
next, keeping Uplynk's own message alongside.
Docs: https://docs.uplynk.com/reference/update_live_cloud_slicer
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus

# The scope updates are known to work with, then the spelling in Uplynk's docs.
# Saved scopes only say what the console will ask for; Uplynk still decides.
WRITE_SCOPE = "video.services.ingest.slicer.cloudslicer.live:write"
WRITE_SCOPES = frozenset({WRITE_SCOPE, "video.services.slicer.cloudslicer.live:write"})

_MESSAGE_KEYS = ("msg", "description", "detail", "title", "error")


def has_write_scope(scp: str | None) -> bool:
    """True if the saved comma-separated scopes include a write scope."""
    return any(s.strip() in WRITE_SCOPES for s in (scp or "").split(","))


@dataclass(frozen=True)
class StatusExplanation:
    """An update outcome for the results page and audit log."""

    heading: str  # "HTTP 422 Unprocessable Entity", or "No response from Uplynk"
    meaning: str
    action: str
    uplynk_message: str | None = None


def uplynk_message(body: dict | str | None) -> str | None:
    """Uplynk's own error text, if the response carried readable text."""
    if isinstance(body, dict):
        for key in _MESSAGE_KEYS:
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:300]
        return None
    text = (body or "").strip()
    # Gateways answer with an HTML page, which says nothing useful here.
    return text[:300] if text and not text.startswith("<") else None


def _heading(status_code: int) -> str:
    try:
        return f"HTTP {status_code} {HTTPStatus(status_code).phrase}"
    except ValueError:
        return f"HTTP {status_code}"


def explain_status(
    status_code: int,
    body: dict | str | None,
    *,
    account_label: str,
    running: bool,
) -> StatusExplanation:
    """Explain one PATCH response."""
    meaning, action = _explanation(status_code, account_label, running)
    message = None if 200 <= status_code < 300 else uplynk_message(body)
    return StatusExplanation(_heading(status_code), meaning, action, message)


def explain_no_response(detail: str) -> StatusExplanation:
    """Explain a PATCH that got no HTTP response at all."""
    return StatusExplanation(
        "No response from Uplynk",
        "The request didn't get an answer, so the update may or may not have applied.",
        _RETRY_RECHECKS,
        detail[:300],
    )


_RETRY_RECHECKS = (
    "Retry when Uplynk is reachable. A retry reads the current settings first, "
    "so nothing is sent if the update already applied."
)


def _explanation(status_code: int, account_label: str, running: bool) -> tuple[str, str]:
    if 200 <= status_code < 300:
        if running:
            return (
                "Uplynk accepted the update.",
                "The slicer is restarting to load the new settings. "
                "Follow its progress on the Dashboard.",
            )
        return (
            "Uplynk accepted the update.",
            "The slicer stays stopped and loads these settings at its next start.",
        )
    if status_code == 400:
        return (
            "Uplynk couldn't process the request, so nothing changed.",
            "Check the values you entered against Uplynk's message.",
        )
    if status_code == 401:
        return (
            f"Uplynk didn't accept the key saved for {account_label} for updates, "
            "so nothing changed.",
            "An out-of-date key can keep working for reads while updates are refused. "
            "Download the current .env from Settings → Scoped API Keys in the Uplynk CMS, "
            f"upload it for {account_label} on the Uplynk Accounts page, then retry.",
        )
    if status_code == 403:
        return (
            f"The key saved for {account_label} isn't allowed to make this change, "
            "so nothing changed.",
            f"Check that the key has {WRITE_SCOPE} in the Uplynk CMS, "
            f"upload its current .env for {account_label}, then retry.",
        )
    if status_code == 404:
        return (
            f"Uplynk couldn't find this slicer in {account_label}, so nothing changed.",
            "It may have been deleted or renamed. Sync the account on the "
            "Uplynk Accounts page to refresh the list.",
        )
    if status_code == 422:
        return (
            "Uplynk understood the request but rejected a value, so nothing changed.",
            "Check the encoding profile GUID, the slicer version and the configuration "
            "lines against Uplynk's message.",
        )
    if status_code == 429:
        return (
            "Uplynk is limiting requests right now, so this update wasn't applied.",
            "Wait a minute, then retry.",
        )
    if status_code >= 500:
        return (
            "Uplynk had a problem on its side, so the update may or may not have applied.",
            _RETRY_RECHECKS,
        )
    return (
        "Uplynk refused the request, so nothing changed.",
        "Check Uplynk's message, and the update docs for this status code.",
    )
