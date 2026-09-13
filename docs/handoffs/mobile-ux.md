# Handoff: Mobile UX pass (#4 + #5)

## Project context

**Repo:** `jj358mhz/csl-slicer-console`
**Live at:** `https://csl.telcomjj.com` (LAN-only, homelab Utility Pi)
**Current version:** v1.2.0
**Stack:** Flask 3 + Jinja2 + HTMX + SQLAlchemy 2 + Alembic + SQLite, gunicorn, Python 3.12, uv, argon2, Fernet, PyJWT (ES256).
**Deployment:** SSH-deploy from GitHub Actions to the Utility Pi on push to main; Portainer polling now disabled so SSH is sole deployer.
**Version badge:** Baked in at build time via `git describe --tags --always --dirty`; falls back to `dev` locally.

## What to build

Two related issues, resolved together in one PR:

- **[#4 — Section chevrons don't toggle on iOS / mobile](https://github.com/jj358mhz/csl-slicer-console/issues/4)** — v1.1.0 explicitly disabled collapse on viewports under 640px. The bypass needs to go so tapping a section header works on phones.
- **[#5 — Workspace chip nav is missing on mobile](https://github.com/jj358mhz/csl-slicer-console/issues/5)** — same v1.1.0 CSS hides the sticky chip row under 640px. Should render on mobile too, since jump-to-workspace is *most* valuable on the smallest screens.

Both stem from a single design assumption in v1.1.0 that turned out to be wrong. Fix them in one commit so the mobile dashboard feels intentional, not patched.

## Design decisions (make these first)

Before writing code, resolve:

- **Collapse on mobile — yes.** Drop the `NARROW_VIEWPORT.matches` early-return in `toggleSection()`, `applyCollapsed()`, and `collapseAllBtn` handler. Let localStorage persistence work on all viewports.
- **Chip nav on mobile — yes, with horizontal scroll.** Add `overflow-x: auto; -webkit-overflow-scrolling: touch` to `.workspace-nav`, drop `display: none` from the `@media (max-width: 640px)` block. Chips wrap to nowrap so they can scroll cleanly.
- **Tap targets — audit.** Section headers should be at least 44px tall on mobile (Apple HIG). Check current padding.
- **Default state on mobile.** Sections stay expanded by default (existing behavior). Users who want to collapse can; users who don't do nothing.

## Files to touch

- `app/static/css/app.css` — section 19 (`Dashboard nav & collapsible sections`). The `@media (max-width: 640px)` block at the end of the section is the culprit; may also need touch-target padding tweaks and horizontal-scroll styling for `.workspace-nav`.
- `app/templates/main/index.html` — the JS in `{% block extra_head %}`. Remove the `NARROW_VIEWPORT.matches` guards in the three places noted above. The `matchMedia` variable and the `change` listener can probably be deleted entirely.

## What NOT to change

- Copy-to-clipboard button and its handler (v1.2.0).
- Version badge and its context processor.
- Batch bar, confirmation modal, dry-run toggle.
- Deploy workflow, Dockerfile version-arg pattern.
- CSS section 20 (version badge), 21 (copy button).

## Testing / verification

- Chrome devtools mobile emulation (iPhone 12/13/14 profile is fine) — tap section headers, tap chips, verify scrolling.
- Real iOS device if available — Chrome's emulator misses some Safari quirks (touch delay, `overflow-x` momentum scroll).
- Desktop regression — after fix, resize a desktop browser to 500px wide and back to 1400px. Behavior should be continuous, not toggle at 640px.
- `uv run pytest -v` — all tests should still pass (pure frontend changes).

## How to work on this

Standard flow from tonight — one chat, block-by-block CSS then JS, pauses between blocks to test.

```bash
cd ~/github/csl-slicer-console
git checkout main
git pull
gh issue develop 4 --checkout   # creates branch linked to #4
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
# hit http://localhost:5050
```

## PR notes

Reference both issues in the PR body so both close on merge:

Closes #4
Closes #5

Bump: **v1.2.1** (bug fix, patch bump) or **v1.3.0** (arguably a UX feature). Lean patch — this is fixing broken behavior, not adding a capability.

## Nice-to-have follow-ups (backlog)

- Audit other desktop-only affordances on mobile: batch bar positioning, dry-run toggle placement, page-header controls wrapping.
- Consider a "condensed" card layout for phones (fewer visible fields per slicer, expand on tap for full detail).
