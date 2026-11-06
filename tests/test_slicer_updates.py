"""Tests for Slicer Updates (#71): explanations, planning, routes and audit."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
import requests

from app.crypto import decrypt, encrypt
from app.models import AdminEvent, AuditEvent, Slicer, SlicerStar, UplynkAccount, User, db
from app.slicers import updates
from app.uplynk.discovery import UplynkAPIError, UplynkDiscoveryClient
from app.uplynk.slicer_config import (
    SlicerConfig,
    apply_line_edits,
    normalize_configuration,
    without_description,
)
from app.uplynk.sync import SyncResult
from app.uplynk.target_state import TargetStateResult, patch_slicer
from app.uplynk.update_status import (
    WRITE_SCOPE,
    explain_no_response,
    explain_status,
    has_write_scope,
    uplynk_message,
)

READ_SCOPE = "video.services.ingest.slicer.cloudslicer.live:read"
GUID_A = "5343a6ce3dfb4646b8900a372b15ac1d"
GUID_B = "cd234daa8a57469faf46ff06ceada771"
SHARED = "drm_mode:studio\ngain:0.0\npids:100,101"


def _config_text(name, shared=SHARED):
    return f"description:{name}\n{shared}\n"


def _config(name="one", version="260604.03.01", profile=GUID_A, shared=SHARED, **kw):
    return SlicerConfig(
        slicer_version=version,
        encoding_profile_id=profile,
        configuration=_config_text(name, shared),
        passphrase="secret-pass",
        **kw,
    )


# ---------------------------------------------------------------------------
# HTTP explanations and the scope check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scp", "expected"),
    [
        (None, False),
        ("", False),
        (READ_SCOPE, False),
        (f"{READ_SCOPE},{WRITE_SCOPE}", True),
        (f"{READ_SCOPE} , {WRITE_SCOPE} ", True),
        ("video.services.slicer.cloudslicer.live:write", True),
        ("video.services.other:write", False),
    ],
)
def test_has_write_scope(scp, expected):
    assert has_write_scope(scp) is expected


@pytest.mark.parametrize(
    ("code", "heading", "words"),
    [
        (400, "HTTP 400 Bad Request", "nothing changed"),
        (401, "HTTP 401 Unauthorized", "Acct"),
        (403, "HTTP 403 Forbidden", WRITE_SCOPE),
        (404, "HTTP 404 Not Found", "deleted or renamed"),
        # The 422 phrase ends differently across Python versions.
        (422, "HTTP 422 Unprocessable", "encoding profile GUID"),
        (429, "HTTP 429 Too Many Requests", "Wait a minute"),
        (502, "HTTP 502 Bad Gateway", "may or may not have applied"),
        (418, "HTTP 418", "refused"),
    ],
)
def test_explains_each_failure(code, heading, words):
    explained = explain_status(code, {"msg": "from uplynk"}, account_label="Acct", running=False)
    assert explained.heading.startswith(heading)
    assert words in f"{explained.meaning} {explained.action}"
    assert explained.uplynk_message == "from uplynk"


def test_401_explains_an_out_of_date_key():
    explained = explain_status(
        401, {"error": 1, "msg": "Not entitled"}, account_label="dma_meade-dev", running=True
    )
    assert "Scoped API Keys" in explained.action
    assert "dma_meade-dev" in explained.action
    assert explained.uplynk_message == "Not entitled"


def test_success_says_whether_the_slicer_restarts():
    running = explain_status(200, {}, account_label="A", running=True)
    stopped = explain_status(200, {}, account_label="A", running=False)
    assert "restarting" in running.action
    assert "next start" in stopped.action


def test_no_response_says_it_may_have_applied():
    explained = explain_no_response("Request to Uplynk failed: timeout")
    assert "may or may not" in explained.meaning
    assert "reads the current settings first" in explained.action


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"msg": "Not entitled", "error": 1}, "Not entitled"),
        ({"description": "bad guid", "title": "Oops"}, "bad guid"),
        ({"error": 1}, None),
        ("plain words", "plain words"),
        ("<html>502</html>", None),
        ("", None),
        (None, None),
    ],
)
def test_uplynk_message(body, expected):
    assert uplynk_message(body) == expected


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def test_normalize_configuration_handles_browser_line_endings():
    assert normalize_configuration("a:1  \r\nb:2\r\n\r\n") == "a:1\nb:2"
    assert normalize_configuration(None) == ""


def test_without_description():
    text = "description: My Slicer\npids:1,2\ndescriptive:no\n"
    assert without_description(text) == "pids:1,2\ndescriptive:no"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _target():
    return updates._Target(1, "s1", "Acct", "us-east-1")


def test_plan_only_sends_what_changed():
    update = updates.UpdateRequest(
        slicer_version="260604.03.01",
        encoding_profile_id=GUID_B,
        configuration="same",
        config_base="same",
    )
    plan = updates.build_plan(_target(), "Stopped", _config(), update)
    assert plan.fields == {"encoding_profile_id": GUID_B}
    assert [c.field for c in plan.changes] == ["encoding_profile_id"]
    assert not plan.restarts


def test_plan_with_nothing_different_is_skipped_and_never_restarts():
    update = updates.UpdateRequest(
        slicer_version="260604.03.01", configuration="same", config_base="same"
    )
    plan = updates.build_plan(_target(), "Slicing", _config(), update)
    assert plan.skipped
    assert plan.running
    assert not plan.restarts


def test_batch_config_keeps_each_slicers_description():
    edited = SHARED.replace("gain:0.0", "gain:1.0")
    update = updates.UpdateRequest(configuration=edited, config_base=SHARED, batch=True)
    plan = updates.build_plan(_target(), "Stopped", _config("own-name"), update)
    assert plan.fields["configuration"] == f"description:own-name\n{edited}\n"
    assert [(d.removed, d.text) for d in plan.diff] == [(True, "gain:0.0"), (False, "gain:1.0")]


def test_batch_config_ignores_a_description_typed_into_the_box():
    typed = f"description:everyone\n{SHARED}\nnew:1"
    update = updates.UpdateRequest(configuration=typed, config_base=SHARED, batch=True)
    plan = updates.build_plan(_target(), "Stopped", _config("own-name"), update)
    assert plan.fields["configuration"] == f"description:own-name\n{SHARED}\nnew:1\n"


def test_batch_config_keeps_settings_that_differ_between_slicers():
    different = SHARED.replace("pids:100,101", "pids:200,201")
    edited = SHARED.replace("gain:0.0", "gain:1.0")
    update = updates.UpdateRequest(configuration=edited, config_base=SHARED, batch=True)
    plan = updates.build_plan(_target(), "Stopped", _config(shared=different), update)
    assert "pids:200,201" in plan.fields["configuration"]
    assert "pids:100,101" not in plan.fields["configuration"]
    assert [d.text for d in plan.diff] == ["gain:0.0", "gain:1.0"]


def test_batch_config_keeps_each_slicers_libraries():
    """The reported case: west's libraries were replaced with east's."""
    east = "cc1_desc:English\nlibraries:99c755439a5e41c6bc07008d63909c66"
    west = "cc1_desc:English\nlibraries:b411b1aa07354101a41c7823376ebc83"
    edited = east.replace("cc1_desc:English", "cc1_desc:EnglishO")
    update = updates.UpdateRequest(configuration=edited, config_base=east, batch=True)
    plan = updates.build_plan(_target(), "Stopped", _config("west", shared=west), update)
    assert plan.fields["configuration"] == (
        "description:west\ncc1_desc:EnglishO\nlibraries:b411b1aa07354101a41c7823376ebc83\n"
    )
    assert [d.text for d in plan.diff] == ["cc1_desc:English", "cc1_desc:EnglishO"]


def test_batch_config_leaves_a_slicer_alone_if_the_edit_is_already_there():
    edited = SHARED.replace("gain:0.0", "gain:1.0")
    update = updates.UpdateRequest(configuration=edited, config_base=SHARED, batch=True)
    plan = updates.build_plan(_target(), "Slicing", _config(shared=edited), update)
    assert plan.skipped
    assert not plan.restarts


@pytest.mark.parametrize(
    ("current", "before", "after", "expected"),
    [
        # Changes a line in place, keeping the slicer's order and other lines.
        ("a:1\nmine:x\nb:2", "a:1\nb:2", "a:9\nb:2", "a:9\nmine:x\nb:2"),
        # Adds a new line at the end.
        ("a:1\nmine:x", "a:1", "a:1\nnew:1", "a:1\nmine:x\nnew:1"),
        # A changed line doesn't appear on a slicer that lacks it.
        ("mine:x", "a:1", "a:9", "mine:x"),
        # A new line replaces the slicer's own if it already has that key.
        ("new:0\nmine:x", "a:1", "a:1\nnew:1", "new:1\nmine:x"),
        # Removes a line, from slicers that have it, even with a different value.
        ("a:7\nmine:x", "a:1\nb:2", "b:2", "mine:x"),
        # Nothing edited, nothing changes, even if the slicer differs.
        ("a:7\nmine:x", "a:1", "a:1", "a:7\nmine:x"),
        # Values containing colons stay whole.
        (
            "url:http://old:1\nmine:x",
            "url:http://old:1",
            "url:http://new:2",
            "url:http://new:2\nmine:x",
        ),
        # Lines with no key are matched whole.
        ("oddline\nmine:x", "oddline", "", "mine:x"),
        # A repeated key ends up once, where it first appeared.
        ("a:1\nmine:x\na:2", "a:1", "a:9", "a:9\nmine:x"),
        # Description lines are never touched, even if typed into the box.
        ("description:mine\na:1", "a:1", "description:all\na:1", "description:mine\na:1"),
        ("description:mine\na:1", "description:mine\na:1", "a:1", "description:mine\na:1"),
    ],
)
def test_apply_line_edits(current, before, after, expected):
    assert apply_line_edits(current, before, after) == expected


def test_single_slicer_config_can_change_its_description():
    current = _config_text("old").rstrip("\n")
    edited = current.replace("description:old", "description:new")
    update = updates.UpdateRequest(configuration=edited, config_base=current)
    plan = updates.build_plan(_target(), "Stopped", _config("old"), update)
    assert plan.fields["configuration"] == f"{edited}\n"


def test_plan_changes_plugin_only_when_different():
    update = updates.UpdateRequest(
        plugin_id="scte-wma", plugin_version="1.2.0", configuration="x", config_base="x"
    )
    plan = updates.build_plan(_target(), "Stopped", _config(), update)
    assert plan.fields["plugin"] == {"id": "scte-wma", "version": "1.2.0"}
    assert plan.changes[0].before == "none"

    same = _config(plugin_id="scte-wma", plugin_version="1.2.0")
    assert updates.build_plan(_target(), "Stopped", same, update).skipped


def test_request_ignores_plugin_unless_toggled():
    off = updates.UpdateRequest.from_form({"plugin": "scte-wma::1.2.0"})
    on = updates.UpdateRequest.from_form({"plugin": "scte-wma::1.2.0", "update_plugin": "1"})
    assert off.plugin_id == ""
    assert (on.plugin_id, on.plugin_version) == ("scte-wma", "1.2.0")


# ---------------------------------------------------------------------------
# Uplynk clients
# ---------------------------------------------------------------------------


def _response(status_code=200, json_data=None):
    mock = MagicMock(spec=requests.Response)
    mock.status_code = status_code
    mock.text = str(json_data)
    mock.json.return_value = json_data
    return mock


def _client(ec_keypair, session):
    return UplynkDiscoveryClient(
        api_base="https://services.uplynk.com",
        kid="k",
        sub="s",
        private_b64=ec_keypair["private_b64"],
        scp=READ_SCOPE,
        session=session,
    )


def test_patch_slicer_sends_the_given_fields(ec_keypair):
    session = MagicMock()
    session.patch.return_value = _response(200, {"id": "s1"})
    result = patch_slicer(
        "https://services.uplynk.com",
        "s1",
        {"slicer_version": "v"},
        kid="k",
        sub="s",
        private_b64=ec_keypair["private_b64"],
        scp=WRITE_SCOPE,
        session=session,
    )
    assert result.ok
    args, kwargs = session.patch.call_args
    assert args[0].endswith("/cloud-slicers/live/slicers/s1")
    assert kwargs["json"] == {"slicer_version": "v"}


def test_list_versions_filters_by_region_newest_first(ec_keypair):
    session = MagicMock()
    session.get.return_value = _response(
        200, {"items": [{"id": "240304.05.01"}, {"id": "260604.03.01"}, {"id": "250210.04.01"}]}
    )
    versions = _client(ec_keypair, session).list_versions("us-east-1")
    assert versions == ["260604.03.01", "250210.04.01", "240304.05.01"]
    assert session.get.call_args.kwargs["params"] == {"region": "us-east-1"}
    assert session.get.call_args.args[0].endswith("/slicers/versions")


def test_list_plugins_newest_version_first(ec_keypair):
    session = MagicMock()
    session.get.return_value = _response(
        200, {"items": [{"id": "scte-wma", "versions": [{"version": "1.0"}, {"version": "1.2"}]}]}
    )
    assert _client(ec_keypair, session).list_plugins() == {"scte-wma": ["1.2", "1.0"]}


def test_list_endpoint_errors_raise(ec_keypair):
    session = MagicMock()
    session.get.return_value = _response(403, {"code": "forbidden"})
    with pytest.raises(UplynkAPIError, match="HTTP 403"):
        _client(ec_keypair, session).list_plugins()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _login(client, email="test@example.com", password="test-password-123"):
    return client.post("/auth/login", data={"email": email, "password": password})


def _account(label="Acct", scp=f"{READ_SCOPE},{WRITE_SCOPE}", key="key"):
    acct = UplynkAccount(
        label=label,
        workspace_id=f"ws-{label}",
        legacy_api_key_encrypted=encrypt("k"),
        scoped_kid="kid",
        scoped_sub="sub",
        scoped_private_b64_encrypted=encrypt(key),
        scoped_scp=scp,
    )
    db.session.add(acct)
    db.session.flush()
    return acct


def _slicer(account, slicer_id, state="Stopped", region="us-east-1"):
    slicer = Slicer(
        uplynk_account_id=account.id,
        slicer_id=slicer_id,
        slicer_api_url=f"https://ingest.example.com/{slicer_id}",
        is_active=True,
        last_state=state,
        region=region,
    )
    db.session.add(slicer)
    db.session.commit()
    return slicer.id


@pytest.fixture
def uplynk(monkeypatch):
    """Fake Uplynk: settings per slicer, and scripted PATCH responses."""

    class Fake:
        settings: dict = {}
        states: dict = {}
        responses: dict = {}
        unreadable: dict = {}
        patches: list = []

    fake = Fake()
    fake.settings, fake.states, fake.responses, fake.unreadable, fake.patches = {}, {}, {}, {}, []

    def snapshot(self, slicer_id):
        if slicer_id in fake.unreadable:
            raise UplynkAPIError(fake.unreadable[slicer_id])
        return fake.states.get(slicer_id, "Stopped"), fake.settings[slicer_id]

    def patch(api_base, slicer_id, fields, **kwargs):
        fake.patches.append((slicer_id, fields))
        response = fake.responses.get(slicer_id)
        if isinstance(response, Exception):
            raise response
        if response is not None:
            return response
        body = {"id": slicer_id, "configuration": fields.get("configuration", "kept")}
        return TargetStateResult(200, body, True)

    monkeypatch.setattr(UplynkDiscoveryClient, "retrieve_slicer_snapshot", snapshot)
    monkeypatch.setattr(
        UplynkDiscoveryClient, "list_versions", lambda self, region=None: ["260604.03.01", "v2"]
    )
    monkeypatch.setattr(
        UplynkDiscoveryClient, "list_plugins", lambda self: {"scte-wma": ["1.2.0", "1.0.0"]}
    )
    monkeypatch.setattr(updates, "patch_slicer", patch)
    return fake


def _apply(client, pks, **fields):
    data = {
        "slicer_ids": pks,
        "configuration": SHARED,
        "config_base": SHARED,
        "batch": "1" if len(pks) > 1 else "",
    }
    data.update(fields)
    return client.post("/admin/slicer-updates/apply", data=data)


def test_requires_login(client):
    assert client.get("/admin/slicer-updates").status_code in (302, 401)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/admin/slicer-updates"),
        ("post", "/admin/slicer-updates/edit"),
        ("post", "/admin/slicer-updates/review"),
        ("post", "/admin/slicer-updates/apply"),
    ],
)
def test_regular_users_are_refused(app, client, uplynk, method, path):
    from app.auth.passwords import hash_password

    db.session.add(
        User(email="user@example.com", password_hash=hash_password("user-password"), is_admin=False)
    )
    db.session.commit()
    _login(client, "user@example.com", "user-password")
    assert getattr(client, method)(path).status_code == 403
    assert uplynk.patches == []


def test_nav_link_only_for_admins(app, client):
    from app.auth.passwords import hash_password

    db.session.add(
        User(email="user@example.com", password_hash=hash_password("user-password"), is_admin=False)
    )
    db.session.commit()
    _login(client, "user@example.com", "user-password")
    assert "/admin/slicer-updates" not in client.get("/").get_data(as_text=True)
    client.post("/auth/logout")
    _login(client)
    assert "/admin/slicer-updates" in client.get("/").get_data(as_text=True)


def test_select_lists_slicers_and_warns_about_read_only_keys(app, client):
    good = _account("Good")
    bad = _account("ReadOnly", scp=READ_SCOPE)
    _slicer(good, "writable")
    blocked_pk = _slicer(bad, "blocked")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert "writable" in body
    assert f"The key saved for ReadOnly doesn&#39;t list {WRITE_SCOPE}" in body
    row = body[body.index(f'value="{blocked_pk}"') :][:200]
    assert "disabled" in row


def test_edit_fetches_current_settings_and_shows_differences(app, client, uplynk):
    acct = _account()
    pks = [_slicer(acct, "a"), _slicer(acct, "b")]
    uplynk.settings = {
        "a": _config("a"),
        "b": _config("b", shared=SHARED.replace("gain:0.0", "gain:3.0")),
    }
    _login(client)
    body = client.post("/admin/slicer-updates/edit", data={"slicer_ids": pks}).get_data(
        as_text=True
    )
    assert "gain:0.0" in body
    assert "description:a" not in body
    assert "which they keep unless you edit that line" in body
    assert 'name="batch" value="1"' in body
    assert "scte-wma v1.2.0" in body
    assert ">v2</option>" in body


def test_edit_shows_the_description_for_a_single_slicer(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    _login(client)
    body = client.post("/admin/slicer-updates/edit", data={"slicer_ids": [pk]}).get_data(
        as_text=True
    )
    assert "description:a" in body
    assert 'name="batch"' not in body


def test_edit_leaves_out_blocked_accounts(app, client, uplynk):
    pk = _slicer(_account("ReadOnly", scp=READ_SCOPE), "a")
    _login(client)
    response = client.post("/admin/slicer-updates/edit", data={"slicer_ids": [pk]})
    assert response.status_code == 302


def test_review_requires_a_change(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    text = _config_text("a").rstrip("\n")
    _login(client)
    body = client.post(
        "/admin/slicer-updates/review",
        data={"slicer_ids": [pk], "configuration": text, "config_base": text},
    ).get_data(as_text=True)
    assert "Change at least one setting" in body


def test_review_refuses_to_empty_the_configuration(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    _login(client)
    body = client.post(
        "/admin/slicer-updates/review",
        data={"slicer_ids": [pk], "configuration": "  \r\n", "config_base": SHARED},
    ).get_data(as_text=True)
    assert "can&#39;t be empty" in body
    assert uplynk.patches == []


def test_review_names_restarts_and_skips(app, client, uplynk):
    acct = _account()
    running, stopped, same = _slicer(acct, "run"), _slicer(acct, "stop"), _slicer(acct, "same")
    uplynk.settings = {
        "run": _config("run"),
        "stop": _config("stop"),
        "same": _config("same", version="v2"),
    }
    uplynk.states = {"run": "Slicing", "same": "Slicing"}
    _login(client)
    body = client.post(
        "/admin/slicer-updates/review",
        data={
            "slicer_ids": [running, stopped, same],
            "slicer_version": "v2",
            "configuration": SHARED,
            "config_base": SHARED,
        },
    ).get_data(as_text=True)
    assert "1 running slicer will restart" in body
    assert f'name="restart_ids" value="{running}"' in body
    assert f'name="restart_ids" value="{same}"' not in body
    assert "No changes, so nothing is sent" in body
    assert "Apply to 2 slicers" in body
    assert uplynk.patches == []


def test_apply_continues_past_a_failure_and_explains_it(app, client, uplynk):
    acct = _account()
    pks = [_slicer(acct, "a"), _slicer(acct, "b"), _slicer(acct, "c")]
    uplynk.settings = {name: _config(name) for name in "abc"}
    uplynk.responses = {"b": TargetStateResult(401, {"error": 1, "msg": "Not entitled"}, False)}
    _login(client)
    body = _apply(client, pks, encoding_profile_id=GUID_B).get_data(as_text=True)
    assert sorted(p[0] for p in uplynk.patches) == ["a", "b", "c"]
    assert "2 updated · 1 failed" in body
    assert "HTTP 401 Unauthorized" in body
    assert "Not entitled" in body
    assert "Scoped API Keys" in body
    assert "HTTP 200 OK" in body
    assert f"{GUID_A}</span> → <span" in body
    assert "Retry 1 not updated" in body


def test_retry_keeps_batch_mode_for_a_single_failed_slicer(app, client, uplynk):
    acct = _account()
    pks = [_slicer(acct, "a"), _slicer(acct, "b")]
    uplynk.settings = {"a": _config("a"), "b": _config("b")}
    uplynk.responses = {"b": TargetStateResult(500, "oops", False)}
    _login(client)
    edited = SHARED.replace("gain:0.0", "gain:1.0")
    body = _apply(client, pks, configuration=edited).get_data(as_text=True)
    assert body.count('name="batch" value="1"') == 1

    uplynk.responses, uplynk.patches = {}, []
    _apply(client, [pks[1]], configuration=edited, batch="1")
    assert uplynk.patches[0][1]["configuration"] == f"description:b\n{edited}\n"


def test_apply_network_failure_says_it_may_have_applied(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    uplynk.responses = {"a": UplynkAPIError("Request to Uplynk failed: timeout")}
    _login(client)
    body = _apply(client, [pk], slicer_version="v2").get_data(as_text=True)
    assert "No response from Uplynk" in body
    assert "may or may not have applied" in body
    event = db.session.query(AuditEvent).one()
    assert event.status_code is None


def test_apply_retry_sends_nothing_if_it_already_applied(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a", version="v2")}
    _login(client)
    body = _apply(client, [pk], slicer_version="v2").get_data(as_text=True)
    assert uplynk.patches == []
    assert "1 skipped" in body
    assert db.session.query(AuditEvent).count() == 0


def test_apply_will_not_restart_without_confirmation(app, client, uplynk):
    pk = _slicer(_account(), "a", state="Stopped")
    uplynk.settings = {"a": _config("a")}
    uplynk.states = {"a": "Slicing"}
    _login(client)
    body = _apply(client, [pk], slicer_version="v2").get_data(as_text=True)
    assert uplynk.patches == []
    assert "started running after the review" in body


def test_apply_restarts_a_confirmed_slicer(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    uplynk.states = {"a": "Slicing"}
    _login(client)
    body = _apply(
        client, [pk], slicer_version="v2", confirm_restart="1", restart_ids=[pk]
    ).get_data(as_text=True)
    assert uplynk.patches == [("a", {"slicer_version": "v2"})]
    assert "restarting" in body
    assert db.session.get(Slicer, pk).config_capture_pending is True


def test_apply_ignores_restart_ids_without_the_checkbox(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    uplynk.states = {"a": "Slicing"}
    _login(client)
    _apply(client, [pk], slicer_version="v2", restart_ids=[pk])
    assert uplynk.patches == []


def test_apply_reports_a_slicer_it_cannot_read(app, client, uplynk):
    acct = _account()
    pks = [_slicer(acct, "a"), _slicer(acct, "b")]
    uplynk.settings = {"b": _config("b")}
    uplynk.unreadable = {"a": "Slicer 'a' not found"}
    _login(client)
    body = _apply(client, pks, slicer_version="v2").get_data(as_text=True)
    assert [p[0] for p in uplynk.patches] == ["b"]
    assert "1 updated · 1 not sent" in body
    assert "Slicer &#39;a&#39; not found" in body


def test_apply_reports_slicers_it_ran_out_of_time_for(app, client, uplynk, monkeypatch):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    monkeypatch.setattr(updates, "NETWORK_BUDGET_SECONDS", -1)
    _login(client)
    body = _apply(client, [pk], slicer_version="v2").get_data(as_text=True)
    assert uplynk.patches == []
    assert "Not reached in time" in body
    assert "Retry 1 not updated" in body


def test_apply_audits_old_and_new_values(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    edited = SHARED.replace("gain:0.0", "gain:1.0")
    _login(client)
    _apply(
        client,
        [pk],
        slicer_version="v2",
        configuration=edited,
        batch="1",
    )
    event = db.session.query(AuditEvent).one()
    assert event.method == updates.AUDIT_METHOD
    assert event.status_code == 200
    assert event.slicer_id == pk
    assert "slicer_version: 260604.03.01 → v2" in event.response_snippet
    assert "-gain:0.0; +gain:1.0" in event.response_snippet
    assert "secret-pass" not in event.response_snippet


def test_apply_stores_the_updated_settings(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    edited = _config_text("a", SHARED.replace("gain:0.0", "gain:1.0")).rstrip("\n")
    _login(client)
    _apply(client, [pk], configuration=edited, config_base=_config_text("a").rstrip("\n"), batch="")
    slicer = db.session.get(Slicer, pk)
    stored = SlicerConfig.from_json(decrypt(slicer.config_encrypted))
    assert stored.configuration == f"{edited}\n"
    assert slicer.config_source == "update"
    assert slicer.config_capture_pending is False
    assert slicer.config_caption.startswith("Updated from the console")


def test_failed_update_leaves_the_stored_settings_alone(app, client, uplynk):
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    uplynk.responses = {"a": TargetStateResult(422, {"description": "bad"}, False)}
    _login(client)
    _apply(client, [pk], slicer_version="v2")
    slicer = db.session.get(Slicer, pk)
    assert slicer.config_encrypted is None
    assert slicer.config_source is None


def test_mixed_batch_only_offers_shared_options(app, client, uplynk, monkeypatch):
    def versions(self, region=None):
        return ["v3", "v2"] if region == "us-east-1" else ["v2", "v1"]

    monkeypatch.setattr(UplynkDiscoveryClient, "list_versions", versions)
    acct = _account()
    pks = [_slicer(acct, "a"), _slicer(acct, "b", region="us-west-2")]
    uplynk.settings = {"a": _config("a"), "b": _config("b")}
    _login(client)
    body = client.post("/admin/slicer-updates/edit", data={"slicer_ids": pks}).get_data(
        as_text=True
    )
    assert ">v2</option>" in body
    assert ">v3</option>" not in body
    assert ">v1</option>" not in body


def test_edit_warns_when_options_cannot_be_listed(app, client, uplynk, monkeypatch):
    def fail(self, region=None):
        raise UplynkAPIError("Uplynk API returned HTTP 500: boom")

    monkeypatch.setattr(UplynkDiscoveryClient, "list_versions", fail)
    pk = _slicer(_account(), "a")
    uplynk.settings = {"a": _config("a")}
    _login(client)
    body = client.post("/admin/slicer-updates/edit", data={"slicer_ids": [pk]}).get_data(
        as_text=True
    )
    assert "Couldn&#39;t list versions for Acct" in body


# ---------------------------------------------------------------------------
# Organized like the Dashboard
# ---------------------------------------------------------------------------


def test_select_groups_by_workspace_with_chips_and_counts(app, client):
    first, second = _account("alpha"), _account("beta")
    _slicer(first, "a1")
    _slicer(first, "a2")
    _slicer(second, "b1")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert 'data-target="section-alpha"' in body
    assert 'data-target="section-beta"' in body
    assert '<span class="workspace-nav__chip-count">(2)</span>' in body
    assert '<section class="account-section" id="section-alpha"' in body
    assert 'class="section-selectall__cb" data-section="beta"' in body
    assert body.index("a2") < body.index("b1")
    assert 'id="collapse-all-btn"' in body
    assert "js/workspace_sections.js" in body


def test_select_has_a_floating_bar_that_starts_hidden(app, client):
    _slicer(_account(), "a")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert '<div id="batch-bar" class="batch-bar" hidden>' in body
    assert 'id="updates-continue"' in body
    assert 'id="batch-clear"' in body


def test_select_lists_your_starred_slicers_first(app, client):
    acct = _account()
    _slicer(acct, "aaa")
    starred_pk = _slicer(acct, "zzz")
    admin = db.session.query(User).filter_by(email="test@example.com").one()
    db.session.add(SlicerStar(user_id=admin.id, slicer_id=starred_pk))
    db.session.commit()
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert body.index("<strong>zzz</strong>") < body.index("<strong>aaa</strong>")
    assert 'data-search="zzz us-east-1 starred"' in body


def test_select_disables_select_all_for_a_blocked_workspace(app, client):
    _slicer(_account("ReadOnly", scp=READ_SCOPE), "a")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert 'data-section="ReadOnly" disabled' in body


def test_review_and_results_group_by_workspace(app, client, uplynk):
    pks = [_slicer(_account("alpha"), "a1"), _slicer(_account("beta"), "b1")]
    uplynk.settings = {"a1": _config("a1"), "b1": _config("b1")}
    data = {
        "slicer_ids": pks,
        "batch": "1",
        "slicer_version": "v2",
        "configuration": SHARED,
        "config_base": SHARED,
    }
    _login(client)
    review = client.post("/admin/slicer-updates/review", data=data).get_data(as_text=True)
    results = client.post("/admin/slicer-updates/apply", data=data).get_data(as_text=True)
    for body in (review, results):
        assert 'id="section-alpha"' in body
        assert 'id="section-beta"' in body
        assert 'data-target="section-beta"' in body
        # A remembered collapse must not hide a change being reviewed or reported.
        assert "initWorkspaceSections({persist: false})" in body


def test_edit_summarizes_the_selection_by_workspace(app, client, uplynk):
    pks = [_slicer(_account("alpha"), "a1"), _slicer(_account("beta"), "b1")]
    uplynk.settings = {"a1": _config("a1"), "b1": _config("b1")}
    _login(client)
    body = client.post("/admin/slicer-updates/edit", data={"slicer_ids": pks}).get_data(
        as_text=True
    )
    assert "Editing 2 slicers" in body
    assert "<strong>alpha:</strong>" in body


def test_dashboard_uses_the_shared_section_script(app, client):
    _slicer(_account(), "a")
    _login(client)
    body = client.get("/").get_data(as_text=True)
    assert "js/workspace_sections.js" in body
    assert "window.initWorkspaceSections();" in body
    assert client.get("/static/js/workspace_sections.js").status_code == 200


# ---------------------------------------------------------------------------
# Select reads live values
# ---------------------------------------------------------------------------


def test_list_slicer_snapshots_parses_state_and_settings(ec_keypair):
    session = MagicMock()
    session.get.return_value = _response(
        200,
        {
            "items": [
                {
                    "id": "s1",
                    "slicer_version": "v9",
                    "encoding_profile_id": GUID_B,
                    "status": {"state": "Ready"},
                }
            ]
        },
    )
    snapshots = _client(ec_keypair, session).list_slicer_snapshots()
    state, config = snapshots["s1"]
    assert state == "Ready"
    assert config.slicer_version == "v9"
    assert config.encoding_profile_id == GUID_B
    assert session.get.call_args.args[0].endswith("/cloud-slicers/live/slicers")


def test_select_shows_live_values_for_a_slicer_never_captured(app, client, monkeypatch):
    pk = _slicer(_account(), "never_ran", state="Stopped")
    monkeypatch.setattr(
        UplynkDiscoveryClient,
        "list_slicer_snapshots",
        lambda self: {
            "never_ran": (
                "Slicing",
                SlicerConfig(slicer_version="260604.03.01", encoding_profile_id=GUID_A),
            )
        },
    )
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert db.session.get(Slicer, pk).config_encrypted is None
    assert "260604.03.01" in body
    assert f'<td class="mono updates-row__profile">{GUID_A}</td>' in body
    assert "badge--state--slicing" in body
    assert "Couldn't read current values" not in body


def test_select_reads_accounts_with_a_read_only_key(app, client, monkeypatch):
    _slicer(_account("ReadOnly", scp=READ_SCOPE), "a")
    monkeypatch.setattr(
        UplynkDiscoveryClient,
        "list_slicer_snapshots",
        lambda self: {"a": ("Stopped", SlicerConfig(slicer_version="v9"))},
    )
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert ">v9<" in body


def test_select_falls_back_to_the_stored_copy_and_says_so(app, client, monkeypatch):
    pk = _slicer(_account("Down"), "a", state="Blackout")
    slicer = db.session.get(Slicer, pk)
    slicer.config_encrypted = encrypt(SlicerConfig(slicer_version="stored-v").to_json())
    db.session.commit()

    def fail(self):
        raise UplynkAPIError("Uplynk API returned HTTP 500: boom")

    monkeypatch.setattr(UplynkDiscoveryClient, "list_slicer_snapshots", fail)
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert "stored-v" in body
    assert "badge--state--blackout" in body
    assert "Couldn't read current values from Uplynk" in body
    assert "for: Down." in body


def test_select_warns_about_an_account_with_no_scoped_key(app, client):
    acct = UplynkAccount(label="NoKey", workspace_id="ws", legacy_api_key_encrypted=encrypt("k"))
    db.session.add(acct)
    db.session.flush()
    _slicer(acct, "a")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert "for: NoKey." in body


def test_select_uses_stored_values_for_a_slicer_missing_from_the_list(app, client, monkeypatch):
    pk = _slicer(_account(), "gone")
    slicer = db.session.get(Slicer, pk)
    slicer.config_encrypted = encrypt(SlicerConfig(slicer_version="stored-v").to_json())
    db.session.commit()
    monkeypatch.setattr(UplynkDiscoveryClient, "list_slicer_snapshots", lambda self: {})
    _login(client)
    assert "stored-v" in client.get("/admin/slicer-updates").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Sync from the tab
# ---------------------------------------------------------------------------


def test_select_has_a_sync_button_outside_the_select_form(app, client):
    acct = _account()
    _slicer(acct, "a")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert f'form="sync-{acct.id}"' in body
    select_form_end = body.index("</form>", body.index('class="updates-select-form"'))
    assert body.index(f'<form id="sync-{acct.id}"') > select_form_end
    assert f'action="/admin/uplynk-accounts/{acct.id}/sync"' in body


def test_sync_button_is_disabled_without_a_scoped_key(app, client):
    acct = UplynkAccount(label="NoKey", workspace_id="ws", legacy_api_key_encrypted=encrypt("k"))
    db.session.add(acct)
    db.session.flush()
    _slicer(acct, "a")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    button = body[body.index(f'form="sync-{acct.id}"') :][:200]
    assert "disabled" in button


def test_sync_from_the_tab_returns_there_keeping_the_selection(app, client, monkeypatch):
    acct = _account()
    pk = _slicer(acct, "a")
    monkeypatch.setattr(
        "app.uplynk.sync.sync_account",
        lambda account: SyncResult(account_id=account.id, account_label=account.label, created=2),
    )
    _login(client)
    response = client.post(
        f"/admin/uplynk-accounts/{acct.id}/sync",
        data={"return_to": "slicer_updates", "slicer_ids": [pk]},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/slicer-updates?slicer_ids={pk}")


def test_sync_from_the_accounts_page_still_returns_there(app, client, monkeypatch):
    acct = _account()
    monkeypatch.setattr(
        "app.uplynk.sync.sync_account",
        lambda account: SyncResult(account_id=account.id, account_label=account.label),
    )
    _login(client)
    response = client.post(f"/admin/uplynk-accounts/{acct.id}/sync")
    assert response.headers["Location"].endswith("/admin/uplynk-accounts")


def test_sync_requires_an_admin(app, client):
    from app.auth.passwords import hash_password

    acct = _account()
    db.session.add(
        User(email="user@example.com", password_hash=hash_password("user-password"), is_admin=False)
    )
    db.session.commit()
    _login(client, "user@example.com", "user-password")
    assert client.post(f"/admin/uplynk-accounts/{acct.id}/sync").status_code == 403


def test_sync_all_syncs_each_account_with_a_key(app, client, monkeypatch):
    first, second = _account("alpha"), _account("beta")
    no_key = UplynkAccount(label="NoKey", workspace_id="n", legacy_api_key_encrypted=encrypt("k"))
    db.session.add(no_key)
    db.session.commit()
    pk = _slicer(first, "a")
    synced = []

    def fake_sync(account):
        synced.append(account.label)
        return SyncResult(account_id=account.id, account_label=account.label, created=1)

    monkeypatch.setattr("app.uplynk.sync.sync_account", fake_sync)
    _login(client)
    response = client.post("/admin/slicer-updates/sync-all", data={"slicer_ids": [pk]})
    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/admin/slicer-updates?slicer_ids={pk}")
    assert synced == ["alpha", "beta"]
    assert second.id


def test_sync_all_continues_past_a_failed_account(app, client, monkeypatch):
    _account("alpha")
    _account("beta")
    db.session.commit()

    def fake_sync(account):
        error = "boom" if account.label == "alpha" else None
        return SyncResult(account_id=account.id, account_label=account.label, error=error)

    monkeypatch.setattr("app.uplynk.sync.sync_account", fake_sync)
    _login(client)
    body = client.post("/admin/slicer-updates/sync-all", follow_redirects=True).get_data(
        as_text=True
    )
    assert "Sync failed for &#39;alpha&#39;: boom" in body
    assert "Synced &#39;beta&#39;" in body


def test_sync_all_names_accounts_it_ran_out_of_time_for(app, client, monkeypatch):
    _account("alpha")
    db.session.commit()
    synced = []
    monkeypatch.setattr("app.uplynk.sync.sync_account", lambda account: synced.append(account))
    monkeypatch.setattr("app.admin.routes.SYNC_ALL_BUDGET_SECONDS", -1)
    _login(client)
    body = client.post("/admin/slicer-updates/sync-all", follow_redirects=True).get_data(
        as_text=True
    )
    assert synced == []
    assert "Ran out of time before syncing: alpha." in body


def test_sync_all_requires_an_admin(app, client):
    from app.auth.passwords import hash_password

    db.session.add(
        User(email="user@example.com", password_hash=hash_password("user-password"), is_admin=False)
    )
    db.session.commit()
    _login(client, "user@example.com", "user-password")
    assert client.post("/admin/slicer-updates/sync-all").status_code == 403


def test_select_has_a_sync_all_button_outside_the_select_form(app, client):
    _slicer(_account(), "a")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert 'form="sync-all"' in body
    select_form_end = body.index("</form>", body.index('class="updates-select-form"'))
    assert body.index('<form id="sync-all"') > select_form_end


def test_sync_all_asks_for_confirmation_naming_the_account_count(app, client):
    _slicer(_account("alpha"), "a")
    _account("beta")
    no_key = UplynkAccount(label="NoKey", workspace_id="n", legacy_api_key_encrypted=encrypt("k"))
    db.session.add(no_key)
    db.session.commit()
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    assert "return confirm(&#39;Sync all 2 accounts?" not in body
    assert "return confirm('Sync all 2 accounts? Slicers removed from Uplynk" in body


# ---------------------------------------------------------------------------
# Sync progress
# ---------------------------------------------------------------------------

JSON_ACCEPT = {"Accept": "application/json"}


def test_sync_answers_json_for_the_progress_panel(app, client, monkeypatch):
    acct = _account()
    monkeypatch.setattr(
        "app.uplynk.sync.sync_account",
        lambda account: SyncResult(
            account_id=account.id, account_label=account.label, created=2, updated=6, deactivated=1
        ),
    )
    _login(client)
    response = client.post(f"/admin/uplynk-accounts/{acct.id}/sync", headers=JSON_ACCEPT)
    assert response.status_code == 200
    assert response.get_json() == {
        "label": "Acct",
        "created": 2,
        "updated": 6,
        "deactivated": 1,
        "error": None,
    }
    event = db.session.query(AdminEvent).filter_by(category="sync").one()
    assert event.action == "run"


def test_sync_json_reports_a_failure_and_audits_it(app, client, monkeypatch):
    acct = _account()
    monkeypatch.setattr(
        "app.uplynk.sync.sync_account",
        lambda account: SyncResult(
            account_id=account.id, account_label=account.label, error="boom"
        ),
    )
    _login(client)
    response = client.post(f"/admin/uplynk-accounts/{acct.id}/sync", headers=JSON_ACCEPT)
    assert response.get_json()["error"] == "boom"
    assert db.session.query(AdminEvent).filter_by(category="sync").one().action == "error"
    # No flash waits for the next page: the panel already showed the result.
    assert "boom" not in client.get("/admin/uplynk-accounts").get_data(as_text=True)


def test_sync_json_requires_an_admin(app, client):
    from app.auth.passwords import hash_password

    acct = _account()
    db.session.add(
        User(email="user@example.com", password_hash=hash_password("user-password"), is_admin=False)
    )
    db.session.commit()
    _login(client, "user@example.com", "user-password")
    response = client.post(f"/admin/uplynk-accounts/{acct.id}/sync", headers=JSON_ACCEPT)
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/admin/uplynk-accounts", "/admin/slicer-updates"])
def test_every_page_with_sync_has_the_progress_panel(app, client, path):
    acct = _account()
    _slicer(acct, "a")
    _login(client)
    body = client.get(path).get_data(as_text=True)
    assert 'id="sync-progress"' in body
    assert "js/sync_progress.js" in body
    assert "window.initSyncProgress(" in body
    assert (
        f'data-sync-accounts=\'[{{"label": "Acct", "url": "/admin/uplynk-accounts/{acct.id}/sync"}}]\''
        in body
    )


def test_sync_all_lists_only_accounts_with_a_key(app, client):
    _slicer(_account("alpha"), "a")
    no_key = UplynkAccount(label="NoKey", workspace_id="n", legacy_api_key_encrypted=encrypt("k"))
    db.session.add(no_key)
    db.session.commit()
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    all_form = body[body.index('<form id="sync-all"') :][:400]
    assert "alpha" in all_form
    assert "NoKey" not in all_form


def test_sync_all_button_disabled_with_no_keys(app, client):
    no_key = UplynkAccount(label="NoKey", workspace_id="n", legacy_api_key_encrypted=encrypt("k"))
    db.session.add(no_key)
    db.session.flush()
    _slicer(no_key, "a")
    _login(client)
    body = client.get("/admin/slicer-updates").get_data(as_text=True)
    button = body[body.index('form="sync-all"') :][:160]
    assert "disabled" in button


def test_sync_json_accepts_the_csrf_header_the_panel_sends(app, client, monkeypatch):
    acct = _account()
    _slicer(acct, "a")
    monkeypatch.setattr(
        "app.uplynk.sync.sync_account",
        lambda account: SyncResult(account_id=account.id, account_label=account.label),
    )
    _login(client)
    app.config["WTF_CSRF_ENABLED"] = True
    body = client.get("/admin/uplynk-accounts").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', body).group(1)
    url = f"/admin/uplynk-accounts/{acct.id}/sync"
    assert client.post(url, headers=JSON_ACCEPT).status_code == 400
    response = client.post(url, headers={**JSON_ACCEPT, "X-CSRFToken": token})
    assert response.status_code == 200
    assert response.get_json()["error"] is None


# ---------------------------------------------------------------------------
# Sync on the Dashboard
# ---------------------------------------------------------------------------


def test_dashboard_gives_admins_sync_buttons_and_the_panel(app, client):
    acct = _account()
    _slicer(acct, "a")
    _login(client)
    body = client.get("/").get_data(as_text=True)
    assert 'form="sync-all"' in body
    assert f'form="sync-{acct.id}"' in body
    assert f'<form id="sync-{acct.id}"' in body
    assert 'id="sync-progress"' in body
    assert "js/sync_progress.js" in body
    assert "window.initSyncProgress?.();" in body
    assert 'name="return_to" value="dashboard"' in body


def test_dashboard_hides_sync_from_regular_users(app, client):
    from app.auth.passwords import hash_password

    acct = _account()
    pk = _slicer(acct, "a")
    user = User(email="user@example.com", password_hash=hash_password("user-password"))
    user.slicers.append(db.session.get(Slicer, pk))
    db.session.add(user)
    db.session.commit()
    _login(client, "user@example.com", "user-password")
    body = client.get("/").get_data(as_text=True)
    assert "a" in body and 'class="slicer-card' in body
    assert 'form="sync-' not in body
    assert '<form id="sync-' not in body
    assert 'id="sync-progress"' not in body
    assert "data-sync-accounts" not in body
    assert "/sync" not in body


def test_dashboard_sync_button_disabled_without_a_key(app, client):
    acct = UplynkAccount(label="NoKey", workspace_id="ws", legacy_api_key_encrypted=encrypt("k"))
    db.session.add(acct)
    db.session.flush()
    _slicer(acct, "a")
    _login(client)
    body = client.get("/").get_data(as_text=True)
    assert "disabled" in body[body.index(f'form="sync-{acct.id}"') :][:220]


def test_sync_from_the_dashboard_returns_there_without_javascript(app, client, monkeypatch):
    acct = _account()
    monkeypatch.setattr(
        "app.uplynk.sync.sync_account",
        lambda account: SyncResult(account_id=account.id, account_label=account.label),
    )
    _login(client)
    response = client.post(
        f"/admin/uplynk-accounts/{acct.id}/sync", data={"return_to": "dashboard"}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_sync_ignores_an_unknown_return_to(app, client, monkeypatch):
    acct = _account()
    monkeypatch.setattr(
        "app.uplynk.sync.sync_account",
        lambda account: SyncResult(account_id=account.id, account_label=account.label),
    )
    _login(client)
    response = client.post(
        f"/admin/uplynk-accounts/{acct.id}/sync", data={"return_to": "https://evil.example"}
    )
    assert response.headers["Location"].endswith("/admin/uplynk-accounts")


def test_batch_edit_skips_a_slicer_without_that_line_so_it_does_not_restart():
    edited = "pts_offset_16:9\ngain:0.0"
    update = updates.UpdateRequest(
        configuration=edited, config_base="pts_offset_16:1\ngain:0.0", batch=True
    )
    plan = updates.build_plan(_target(), "Slicing", _config(shared="gain:0.0"), update)
    assert plan.skipped
    assert not plan.restarts


def test_batch_new_line_reaches_every_slicer():
    update = updates.UpdateRequest(
        configuration="gain:0.0\nnew:1", config_base="gain:0.0", batch=True
    )
    plan = updates.build_plan(_target(), "Stopped", _config("x", shared="mine:y"), update)
    assert plan.fields["configuration"] == "description:x\nmine:y\nnew:1\n"
