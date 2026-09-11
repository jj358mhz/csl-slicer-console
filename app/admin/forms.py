"""WTForms for admin UI."""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class UplynkAccountForm(FlaskForm):
    """Create or edit an Uplynk account.

    - Legacy API Key: paste directly (required on create).
    - Scoped API Key: upload the .env file downloaded from the Uplynk CMS.
      Optional. On edit, leave blank to keep existing.
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
    scoped_env_file = FileField(
        "Scoped API Key (.env file)",
        validators=[
            Optional(),
            FileAllowed(["env", "txt"], "Upload the .env file from the Uplynk CMS."),
        ],
        description="Download the .env file from Settings → Scoped API Keys "
                    "in the Uplynk CMS. Required scope: "
                    "video.services.ingest.cloudslicer.live:read. "
                    "Leave blank to keep existing value.",
    )
    submit = SubmitField("Save")