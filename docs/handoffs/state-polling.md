# Handoff: State polling + SRT push/pull label (#2 + #3)

## Project context

**Repo:** `jj358mhz/csl-slicer-console`
**Live at:** `https://csl.telcomjj.com` (LAN-only, homelab Utility Pi)
**Current version:** v1.2.1 (or whatever's on main after the mobile UX PR merges)
**Stack:** Flask 3 + Jinja2 + HTMX + SQLAlchemy 2 + Alembic + SQLite, gunicorn, Python 3.12, uv, argon2, Fernet, PyJWT (ES256).
**Deployment:** SSH-deploy from GitHub Actions to the Utility Pi on push to main. Portainer polling disabled — SSH is sole deployer.
**Version badge:** Baked in at build time via `git describe --tags --always --dirty`; falls back to `dev` locally.

## What to build

Two issues, resolved together in one PR because they share the same Uplynk endpoint:

- **[#2 — Slicer state badges don't update; add auto-polling](https://github.com/jj358mhz/csl-slicer-console/issues/2)** — State badges reflect the state at page load and never refresh. For an operations console this is the single biggest gap in usefulness. Also account for all state values in the docs, not just the current four.
- **[#3 — Slicer tile label should distinguish SRT push vs pull](https://github.com/jj358mhz/csl-slicer-console/issues/3)** — Tile currently shows `us-west-2 · SRT`. Should show `us-west-2 · SRT push` or `us-west-2 · SRT pull`.

**Shared endpoint:** [`GET /api/v4/ingest/cloud-slicers/live/slicers/{slicer_id}`](https://docs.uplynk.com/reference/retrieve_live_cloud_slicer)

Auth is the v4 scoped-key JWT flow already implemented in `app/uplynk/discovery.py` (ES256-signed, `X-Auth-Uplynk-Jwt` header). Reuse it — don't reimplement.

## Design decisions (make these first)

Before writing code, resolve:

- **Poll interval — 30s default.** Fast enough to feel live, slow enough not to hammer Uplynk or the container. Global constant, not per-user config.
- **Pause when tab is backgrounded — yes.** `document.hidden` guard on the polling loop, resume on `visibilitychange`. Trivial and saves API calls.
- **Collapsed sections still poll — yes.** Cards stay in the DOM; state is still relevant when the user expands. Cheap enough.
- **Global on/off toggle in page header — no, not yet.** Ship polling on-by-default; add a toggle only if it becomes a nuisance. Keep the page header uncluttered.
- **Polling mechanism — HTMX `hx-trigger="every 30s"` on the badge element.** No new JS. Uses existing session auth. Response is a partial template that swaps the badge (and optionally the label) in place.
- **New backend route or reuse `/slicers/<id>/control`?** New route: `GET /slicers/<id>/state`. Different verb, different caller, different template. Don't overload the control endpoint.
- **All-states audit.** Pull the full state enum from the retrieve endpoint docs. Current tile CSS handles `slicing`, `adbreak`, `blackout`, `stopped`, and a fallback `unknown`. Add badge variants + CSS colors for any state we're currently rendering as `unknown`.
- **SRT push/pull — where does it live in the response?** Probably `srt_mode`, `srt_type`, or under a `transport`/`ingest_config` block. Inspect a real response first. Persist to the `slicers` table (Alembic migration) so it's captured at sync time too, not just on poll.

## Files to touch

**Backend:**
- `app/uplynk/discovery.py` (or new `app/uplynk/live.py`) — add a `retrieve_slicer(account, slicer_id)` function that hits the retrieve endpoint with the JWT and returns the parsed response.
- `app/slicers/routes.py` (or wherever the current control route lives) — new `GET /slicers/<id>/state` route that calls the retrieve function and renders a partial template.
- `app/models.py` — add `srt_mode` column (nullable string) to `Slicer`.
- `app/uplynk/sync.py` — capture `srt_mode` at sync time from the same endpoint (already called during discovery).
- `migrations/versions/*.py` — new Alembic migration for the `srt_mode` column.
- Optional: `app/slicers/service.py` — if state parsing gets non-trivial, extract to a service function.

**Frontend:**
- `app/templates/main/index.html` — add `hx-get="/slicers/{{ s.id }}/state" hx-trigger="every 30s" hx-swap="outerHTML"` on the badge element. Wrap badge + label in a container the partial can replace.
- `app/templates/slicers/_state.html` — new partial rendering the badge + tile label. Called by the polling response.
- `app/static/css/app.css` — new badge variants for any additional states (`starting`, `stopping`, `error`, whatever the docs list). Append to a new section 22, not into existing sections.

**Tests:**
- `tests/test_discovery.py` or new `tests/test_live.py` — cover the retrieve function (happy path, 401, 404, malformed response).
- `tests/test_slicer_routes.py` — cover the new state route (authorized, unauthorized, other user's slicer).
- `tests/test_sync.py` — cover `srt_mode` capture at sync time.

## What NOT to change

- Existing control endpoints (`status`, `state`, `content_start`, `blackout`) and their SHA1 auth flow. State polling is v4 API, orthogonal.
- Copy-to-clipboard button (v1.2.0).
- Version badge, deploy workflow, Dockerfile.
- Batch bar, confirmation modal, dry-run toggle.
- Collapsible sections logic (v1.1.0 + mobile fixes).
- CSS sections 19 (dashboard nav), 20 (version badge), 21 (copy button).

## Testing / verification

- Load dashboard, wait 30s, verify a badge you expect to change (a slicer you can toggle from another tab) updates in place.
- Background the tab for a minute, come back — verify polling resumes and doesn't fire 10x rapidly.
- Load with a slicer in every documented state (or mock the response) — verify each renders with an appropriate badge color, none fall through to `unknown`.
- Verify `srt_mode` displays correctly on SRT slicers, gracefully absent for non-SRT.
- `uv run pytest -v` — all tests plus new ones green.
- Watch Docker logs during a poll cycle — no error spam, no 429s from Uplynk.

## How to work on this

Standard flow — one chat, block-by-block, pauses for testing.

```bash
cd ~/github/csl-slicer-console
git checkout main
git pull
gh issue develop 2 --checkout --name state-polling
# link #3 to the same branch so both close on merge
gh issue develop 3 --name state-polling
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

## PR notes

Reference both issues in the PR body:

Closes #2
Closes #3

Bump: **v1.3.0** — new user-facing capability (live state), plus schema change. Feature bump.

## Nice-to-have follow-ups (backlog)

- Global polling on/off toggle if it becomes noisy or expensive.
- Configurable poll interval (per-user or per-workspace).
- Websocket / SSE push instead of poll — bigger change, only worth it if the fleet grows or Uplynk exposes a push channel.
- State history sparkline per card (show last N state transitions inline).