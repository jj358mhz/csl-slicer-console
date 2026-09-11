"""Sync discovered slicers from the Uplynk v4 API into our database."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from flask import current_app

from app.crypto import decrypt
from app.models import Slicer, UplynkAccount, db
from app.uplynk.discovery import DiscoveredSlicer, UplynkAPIError, UplynkDiscoveryClient


@dataclass
class SyncResult:
    """Summary of a single sync run."""

    account_id: int
    account_label: str
    created: int = 0
    updated: int = 0
    deactivated: int = 0
    error: str | None = None

    @property
    def total_seen(self) -> int:
        return self.created + self.updated


def sync_account(account: UplynkAccount) -> SyncResult:
    """Sync one Uplynk account's slicers into the DB.

    - New slicer_ids are created.
    - Existing slicer_ids have their metadata refreshed.
    - Slicers previously seen but missing from this response are marked inactive.
    """
    result = SyncResult(account_id=account.id, account_label=account.label)

    if not account.has_scoped_key:
        result.error = (
            "No scoped API key configured — upload the .env file from the "
            "Uplynk CMS to enable discovery."
        )
        return result

    try:
        private_b64 = decrypt(account.scoped_private_b64_encrypted)
    except Exception as e:
        result.error = f"Failed to decrypt scoped private key: {e}"
        return result

    client = UplynkDiscoveryClient(
        api_base=current_app.config["UPLYNK_API_BASE"],
        kid=account.scoped_kid,
        sub=account.scoped_sub,
        private_b64=private_b64,
        scp=account.scoped_scp,
    )

    try:
        discovered = client.list_slicers()
    except UplynkAPIError as e:
        result.error = str(e)
        return result

    _apply_sync(account, discovered, result)
    account.last_synced_at = datetime.now(timezone.utc)
    db.session.commit()
    return result


def _apply_sync(
    account: UplynkAccount,
    discovered: list[DiscoveredSlicer],
    result: SyncResult,
) -> None:
    """Upsert discovered slicers, deactivate ones no longer seen."""
    now = datetime.now(timezone.utc)

    existing = {
        s.slicer_id: s
        for s in db.session.query(Slicer)
        .filter_by(uplynk_account_id=account.id)
        .all()
    }

    seen_ids: set[str] = set()

    for ds in discovered:
        seen_ids.add(ds.slicer_id)
        current = existing.get(ds.slicer_id)
        if current is None:
            db.session.add(
                Slicer(
                    uplynk_account_id=account.id,
                    slicer_id=ds.slicer_id,
                    slicer_api_url=ds.slicer_api_url,
                    region=ds.region,
                    protocol=ds.protocol,
                    plugin_id=ds.plugin_id,
                    plugin_version=ds.plugin_version,                    last_state=ds.state,
                    description=ds.description,
                    is_active=True,
                    last_seen_at=now,
                )
            )
            result.created += 1
        else:
            current.slicer_api_url = ds.slicer_api_url
            current.region = ds.region
            current.protocol = ds.protocol
            current.plugin_id = ds.plugin_id
            current.plugin_version = ds.plugin_version
            current.last_state = ds.state
            current.description = ds.description
            current.is_active = True
            current.last_seen_at = now
            result.updated += 1

    for slicer_id, slicer in existing.items():
        if slicer_id not in seen_ids and slicer.is_active:
            slicer.is_active = False
            result.deactivated += 1
