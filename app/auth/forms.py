"""WTForms for auth."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"autocomplete": "username", "autofocus": True},
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"autocomplete": "current-password"},
    )
    remember = BooleanField("Keep me signed in")
    submit = SubmitField("Sign in")