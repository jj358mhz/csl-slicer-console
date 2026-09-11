"""Access-control decorators for admin routes."""

from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import abort
from flask_login import current_user, login_required


def admin_required(view: Callable) -> Callable:
    """Require an authenticated admin user for the view."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)

    return wrapped
