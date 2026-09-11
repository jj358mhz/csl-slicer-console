"""WTForms for admin UI."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class UplynkAccountForm(FlaskForm):
    """Create or edit an Uplynk account.

    On edit, leave API key fields blank to keep existing values.
    """

    label = StringField(
        "Label",
        validators=[DataRequired(), Length(max=100)],
        description="Friendly name for this account (e.g. 'Meade Dev').",
    )
    workspace_id = StringField(
        "Workspace ID",
        validators=[DataRequired(), Length(max=100)],
        description="UPLYNK_WORKSPACE_ID for this account.",
    )
    legacy_api_key = PasswordField(
        "Legacy API Key",
        validators=[Optional(), Length(max=500)],
        description="Used for CSL slicer control (SHA1 signing). "
                    "Leave blank to keep existing value.",
        render_kw={"autocomplete": "off"},
    )
    scoped_api_key = PasswordField(
        "Scoped API Key",
        validators=[Optional(), Length(max=500)],
        description="v4 API Bearer token for slicer discovery. Optional. "
                    "Leave blank to keep existing value.",
        render_kw={"autocomplete": "off"},
    )
    submit = SubmitField("Save")
