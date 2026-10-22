"""Starred slicers (per user, listed first) and the dashboard filter."""

from __future__ import annotations

from app.auth.passwords import hash_password
from app.crypto import encrypt
from app.models import Slicer, SlicerStar, UplynkAccount, User, db


def _login(client, email="test@example.com", password="test-password-123"):
    client.post(
        "/auth/login",
        data={"email": email, "password": password, "submit": "Sign in"},
        follow_redirects=True,
    )


def _make_slicers(names=("s1", "s2", "s3")):
    acct = UplynkAccount(
        label="Acct", workspace_id="ws", legacy_api_key_encrypted=encrypt("legacy-key")
    )
    db.session.add(acct)
    db.session.flush()
    slicers = [
        Slicer(
            uplynk_account_id=acct.id,
            slicer_id=name,
            slicer_api_url=f"https://ingest.example.com/{name}",
            region="us-west",
            protocol="SRT",
            description=f"Demo {name}",
            is_active=True,
        )
        for name in names
    ]
    db.session.add_all(slicers)
    db.session.commit()
    return [s.id for s in slicers]


def _order(page, ids):
    return sorted(ids, key=lambda i: page.index(f'id="slicer-{i}"'))


def test_star_lists_first_with_a_divider(app, client):
    _login(client)
    s1, s2, s3 = _make_slicers()

    page = client.get("/").get_data(as_text=True)
    assert _order(page, [s1, s2, s3]) == [s1, s2, s3]
    assert 'slicer-grid__divider"' not in page  # nothing starred

    r = client.post(f"/slicers/{s3}/star")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/#slicer-{s3}")
    page = client.get("/").get_data(as_text=True)
    assert _order(page, [s1, s2, s3]) == [s3, s1, s2]
    rule = page.index('slicer-grid__divider"')
    assert page.index(f'id="slicer-{s3}"') < rule < page.index(f'id="slicer-{s1}"')
    assert "slicer-card--starred" in page and "slicer-card__star--on" in page

    # Unstar toggles back.
    client.post(f"/slicers/{s3}/star")
    assert db.session.query(SlicerStar).count() == 0


def test_no_divider_when_every_slicer_is_starred(app, client):
    _login(client)
    (s1,) = _make_slicers(("s1",))
    client.post(f"/slicers/{s1}/star")
    page = client.get("/").get_data(as_text=True)
    assert "slicer-card--starred" in page and 'slicer-grid__divider"' not in page


def test_stars_are_per_user(app, client):
    s1, s2 = _make_slicers(("s1", "s2"))
    user = User(email="ru@example.com", password_hash=hash_password("password123"))
    user.slicers = [db.session.get(Slicer, s1), db.session.get(Slicer, s2)]
    db.session.add(user)
    db.session.commit()

    _login(client)
    client.post(f"/slicers/{s2}/star")
    client.post("/auth/logout")

    _login(client, "ru@example.com", "password123")
    page = client.get("/").get_data(as_text=True)
    assert _order(page, [s1, s2]) == [s1, s2]
    assert "slicer-card--starred" not in page


def test_star_needs_access(app, client):
    (s1,) = _make_slicers(("s1",))
    db.session.add(User(email="ru@example.com", password_hash=hash_password("password123")))
    db.session.commit()
    assert client.post(f"/slicers/{s1}/star").status_code == 302  # login first
    _login(client, "ru@example.com", "password123")
    assert client.post(f"/slicers/{s1}/star").status_code == 403
    assert client.post("/slicers/999/star").status_code == 404
    assert db.session.query(SlicerStar).count() == 0


def test_filter_box_and_search_text(app, client):
    _login(client)
    (s1,) = _make_slicers(("Slicer-A",))
    client.post(f"/slicers/{s1}/star")
    page = client.get("/?q=srt").get_data(as_text=True)
    assert 'id="slicer-filter"' in page and 'value="srt"' in page
    assert 'data-search="slicer-a demo slicer-a us-west srt starred"' in page
    assert "<span data-section-count>1</span>" in page
