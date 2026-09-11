"""Public routes: dashboard, history, and health check."""

from __future__ import annotations

from itertools import groupby

from flask import jsonify, render_template
from flask_login import current_user, login_required

from app.main import bp
from app.models import AuditEvent, Slicer, UplynkAccount, db


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

    groups = [
        (label, list(items))
        for label, items in groupby(slicers, key=lambda s: s.uplynk_account.label)
    ]
    return render_template("main/index.html", groups=groups, total=len(slicers))


@bp.route("/history")
@login_required
def history() -> str:
    events = (
        db.session.query(AuditEvent)
        .filter_by(user_id=current_user.id)
        .order_by(AuditEvent.timestamp.desc())
        .limit(200)
        .all()
    )
    return render_template("main/history.html", events=events)


@bp.route("/health")
def health():
    return jsonify(status="ok")
