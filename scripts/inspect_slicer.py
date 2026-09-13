# scripts/inspect_slicer.py — now testing retrieve_slicer()
from __future__ import annotations

import sys
from dataclasses import asdict
from pprint import pprint

from sqlalchemy import select

from app import create_app
from app.crypto import decrypt
from app.models import UplynkAccount, db
from app.uplynk.discovery import UplynkDiscoveryClient


def _resolve_account(arg: str) -> UplynkAccount:
    if arg.isdigit():
        acct = db.session.get(UplynkAccount, int(arg))
    else:
        acct = db.session.scalar(select(UplynkAccount).filter_by(label=arg))
    if acct is None:
        raise SystemExit(f"No UplynkAccount matches {arg!r}")
    return acct


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: inspect_slicer.py <account> <slicer_id>", file=sys.stderr)
        return 2
    account_arg, slicer_id = sys.argv[1], sys.argv[2]
    app = create_app()
    with app.app_context():
        account = _resolve_account(account_arg)
        private_b64 = decrypt(account.scoped_private_b64_encrypted)
        client = UplynkDiscoveryClient(
            api_base=app.config["UPLYNK_API_BASE"],
            kid=account.scoped_kid,
            sub=account.scoped_sub,
            private_b64=private_b64,
            scp=account.scoped_scp,
        )
        slicer = client.retrieve_slicer(slicer_id)
        pprint(asdict(slicer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
