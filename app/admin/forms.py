"""WTForms for admin UI."""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    PasswordField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, Email, Length, Optional
from wtforms.widgets import CheckboxInput, ListWidget


class UplynkAccountForm(FlaskForm):
    """Create or edit an Uplynk account.

    - Legacy API Key: paste directly (required on create).
    - Scoped API Key: upload the .env file downloaded from the Uplynk CMS.
      Optional. On edit, leave blank to keep existing.
    """

    label = StringField(
        "Workspace Username",
        validators=[DataRequired(), Length(max=100)],
        description="The workspace username as shown in the Uplynk CMS "
                    "(e.g. 'dma_meade-dev').",
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


class UserForm(FlaskForm):
    """Create or edit a user."""

    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
    )
    password = PasswordField(
        "Password",
        validators=[Optional(), Length(min=8, max=255)],
        description="At least 8 characters. Leave blank on edit to keep the current password.",
        render_kw={"autocomplete": "new-password"},
    )
    is_admin = BooleanField("Admin")
    is_active = BooleanField("Active", default=True)
    submit = SubmitField("Save")


class MultiCheckboxField(SelectMultipleField):
    """A SelectMultipleField that renders as a list of checkboxes."""

    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class UserSlicerAssignmentForm(FlaskForm):
    """Assign slicers to a user via a list of checkboxes."""

    slicer_ids = MultiCheckboxField("Slicers", coerce=int)
    submit = SubmitField("Save")

class SlicerForm(FlaskForm):
    """Manually add or edit a slicer under an Uplynk account."""

    uplynk_account_id = SelectField(
        "Uplynk Account",
        coerce=int,
        validators=[DataRequired()],
    )
    slicer_id = StringField(
        "Slicer ID",
        validators=[DataRequired(), Length(max=100)],
        description="The slicer identifier as used in the CSL URL (e.g. 'slicer30158').",
    )
    slicer_api_url = StringField(
        "Slicer API URL",
        validators=[DataRequired(), Length(max=500)],
        description="Full CSL URL, e.g. 'https://ingest-prod-0-us-east-1-3.csl.uplynk.net:443/slicer30158'.",
    )
    region = StringField(
        "Region",
        validators=[Optional(), Length(max=50)],
        description="Optional (e.g. 'us-east-1').",
    )
    protocol = StringField(
        "Protocol",
        validators=[Optional(), Length(max=50)],
        description="Optional (e.g. 'SRT', 'RTMP').",
    )
    submit = SubmitField("Save")
