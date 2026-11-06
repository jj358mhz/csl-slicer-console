"""A slicer's configuration, as captured from the v4 retrieve endpoint.

The slicer loads its configuration when it starts, so the console captures
it once per run (see app.slicers.service) and serves the stored copy rather
than re-querying Uplynk.
Docs: https://docs.uplynk.com/reference/retrieve_live_cloud_slicer
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from typing import Any


@dataclass(frozen=True)
class SlicerConfig:
    """The configuration fields of one retrieve response."""

    protocol: str | None = None
    rist_profile: str | None = None
    region: str | None = None
    slicer_version: str | None = None
    encoding_profile_id: str | None = None
    source_ip: str | None = None
    target_state: str | None = None
    description: str | None = None
    created_at: str | None = None
    streaming_url: str | None = None
    slicer_api_url: str | None = None
    passphrase: str | None = None
    plugin_id: str | None = None
    plugin_version: str | None = None
    # Line-delimited `key: value` settings; see parse_configuration().
    configuration: str | None = None

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> SlicerConfig:
        plugin = item.get("plugin") or {}
        return cls(
            protocol=item.get("protocol"),
            rist_profile=item.get("rist_profile"),
            region=item.get("region"),
            slicer_version=item.get("slicer_version"),
            encoding_profile_id=item.get("encoding_profile_id"),
            source_ip=item.get("source_ip"),
            target_state=item.get("target_state"),
            description=item.get("description"),
            created_at=item.get("created_at"),
            streaming_url=item.get("streaming_url"),
            slicer_api_url=item.get("slicer_api_url"),
            passphrase=item.get("passphrase"),
            plugin_id=plugin.get("id"),
            plugin_version=plugin.get("version"),
            configuration=item.get("configuration"),
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> SlicerConfig:
        # Ignore keys this version doesn't know, so a stored row written by a
        # newer release still loads after a rollback.
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in json.loads(raw).items() if k in known})

    @property
    def settings(self) -> list[tuple[str | None, str]]:
        return parse_configuration(self.configuration)


def parse_configuration(text: str | None) -> list[tuple[str | None, str]]:
    """Split the `configuration` string into (key, value) rows, in order.

    Each line splits on its first `:` only, so values that contain colons
    (URLs, host:port) stay whole. A line with no key comes back as
    (None, line) so it's shown as-is rather than dropped. Blank lines are
    skipped.
    """
    rows: list[tuple[str | None, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key:
            rows.append((key, value.strip()))
        else:
            rows.append((None, line))
    return rows


def normalize_configuration(text: str | None) -> str:
    """The configuration with browser line endings and edge whitespace removed.

    Form fields submit CRLF, so compare and send this form, not the raw text.
    """
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip("\n")


def _is_description(line: str) -> bool:
    key, sep, _ = line.partition(":")
    return bool(sep) and key.strip() == "description"


def without_description(text: str | None) -> str:
    """The configuration minus its `description` line: what a batch's box shows (#71)."""
    lines = normalize_configuration(text).split("\n")
    return "\n".join(line for line in lines if not _is_description(line))


def _line_key(line: str) -> str:
    """What identifies a line: its key, or the whole line if it has none."""
    key, sep, _ = line.partition(":")
    return key.strip() if sep and key.strip() else line.strip()


def _lines_by_key(text: str) -> dict[str, str]:
    return {_line_key(line): line for line in text.split("\n") if line.strip()}


def apply_line_edits(current: str | None, before: str, after: str) -> str:
    """Apply the edits between `before` and `after` to a slicer's own configuration.

    Slicers in a batch legitimately differ (libraries, call_sign, pids...), and
    Uplynk replaces the whole configuration, so only the lines the admin changed,
    added or removed may differ from what the slicer already has. A changed line
    only applies where the slicer has that key, so it can't switch a setting on
    by accident. A line added as new goes to every slicer. Description lines are
    never touched.
    """
    old = _lines_by_key(without_description(before))
    new = _lines_by_key(without_description(after))
    changed = {key: line for key, line in new.items() if old.get(key) != line}
    removed = old.keys() - new.keys()

    result = []
    placed = set()
    for line in normalize_configuration(current).split("\n"):
        key = _line_key(line)
        if key in removed:
            continue
        if key in changed:
            if key not in placed:
                result.append(changed[key])
                placed.add(key)
            continue
        result.append(line)
    result += [line for key, line in changed.items() if key not in old and key not in placed]
    return normalize_configuration("\n".join(result))
