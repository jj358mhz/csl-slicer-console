"""Public routes: index and health check."""

from __future__ import annotations

from itertools import groupby

from flask import jsonify, render_template
from flask_login import current_user, login_required

from app.main import bp
from app.models import Slicer, UplynkAccount, db


@bp.route("/")
@login_required
def index() -> str:
    if current_user.is_admin:
        slicers = (
            db.session.query(Slicer)
            .filter_by(is_active=True)
            .join(UplynkAccount)
            .order_by(UplynkAccount.label, Slicer.slicer_id)
            .all()
        )
    else:
        slicers = sorted(
            current_user.slicers,
            key=lambda s: (s.uplynk_account.label, s.slicer_id),
        )

    # Group by account label so the template can render one section per account.
    groups = [
        (label, list(items))
        for label, items in groupby(slicers, key=lambda s: s.uplynk_account.label)
    ]
    return render_template("main/index.html", groups=groups, total=len(slicers))


@bp.route("/health")
def health():
    return jsonify(status="ok")