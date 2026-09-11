"""Parse the .env file downloaded from Uplynk's Scoped API Keys UI."""

from __future__ import annotations

from dataclasses import dataclass


class ScopedEnvParseError(ValueError):
    """Raised when the uploaded .env file is missing required fields or malformed."""


REQUIRED_FIELDS = ("KID", "SUB", "PRIVATE_B64", "SCP")


@dataclass(frozen=True)
class ScopedEnv:
    """Fields extracted from an Uplynk scoped-key .env file.

    ROOT_URL is captured but not required — the app stores its own UPLYNK_API_BASE.
    """

    kid: str
    sub: str
    private_b64: str
    scp: str  # comma-separated scopes
    root_url: str | None = None


def parse_scoped_env(content: str | bytes) -> ScopedEnv:
    """Parse an Uplynk scoped-key .env file.

    Accepts str or bytes. Ignores blank lines and lines beginning with '#'.
    Values may be surrounded by single or double quotes; these are stripped.
    """
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ScopedEnvParseError("File is not valid UTF-8") from e

    values: dict[str, str] = {}
    for lineno, raw in enumerate(content.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ScopedEnvParseError(f"Line {lineno} is not KEY=VALUE format")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value

    missing = [f for f in REQUIRED_FIELDS if not values.get(f)]
    if missing:
        raise ScopedEnvParseError(f"Missing required field(s): {', '.join(missing)}")

    return ScopedEnv(
        kid=values["KID"],
        sub=values["SUB"],
        private_b64=values["PRIVATE_B64"],
        scp=values["SCP"],
        root_url=values.get("ROOT_URL") or None,
    )
