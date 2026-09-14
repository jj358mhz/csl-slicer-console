# Changelog

All notable changes to **csl-slicer-console** are documented here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### ✨ Added
- Admin scope viewer on the Uplynk account edit form — displays the scopes granted to each stored scoped API key as badges with hover tooltips. Read-only, admin-only. (#14)

### 🐛 Fixed
- Corrected scoped API key scope string in file-upload help text and test fixture (`video.services.ingest.slicer.cloudslicer.live:read` — was missing `.slicer`).

### ♻️ Changed
- Reconciled the two Uplynk slicer state vocabularies in a canonical `app/uplynk/states.py` module. Retrieve vocab (`Slicing`, `AdBreak`, `Blackout`, `Stopped`, etc — 8 values) renders on tile badges via `slicer.last_state`; SHA1 control vocab (`Capture`, `Ad`, `Replace`, `Blackout` — 4 values) lands in `AuditEvent.response_snippet` only. Only `Blackout` spells identically in both. (#11)
- Start/Stop actions for slicers on the dashboard and batch bar. Uses the v4 PATCH endpoint with `target_state: Ready|Stopped`, JWT auth. Requires the `:write` scope on the account's scoped key; without it, the call 403s and the audit log captures why. (#1)

---

## [1.3.0] — 2026-09-12

### Added

- 🔄 **Auto-polling state badges** — slicer state badges refresh every 30s via HTMX polling on `GET /slicers/<id>/state`. No page reload, no new JS. Falls through to the last-known state on transient Uplynk errors rather than 500ing. Closes [#2](https://github.com/jj358mhz/csl-slicer-console/issues/2).
- 🛰️ **SRT push/pull qualifier** — tile meta line now reads `us-east-1 · SRT pull` (or `push`) instead of just `SRT`. Populated at sync time from the v4 API's `connection_mode` field. Closes [#3](https://github.com/jj358mhz/csl-slicer-console/issues/3).
- 🔌 New `retrieve_slicer()` on the v4 discovery client and `poll_slicer_state()` service (access-checked, same policy as `control_slicer`).
- 🗄️ New `connection_mode` column on `slicers` (Alembic migration `5ca8377f1cb7`).

---

## [1.2.1] — 2026-09-12

### Fixed

- 📱 **Mobile section collapse** — tapping section headers on iOS/mobile now toggles collapse as it does on desktop. v1.1.0 disabled this under 640px on the assumption phones needed a simpler view; that assumption was wrong. Fixes [#4](https://github.com/jj358mhz/csl-slicer-console/issues/4).
- 🧭 **Mobile workspace chip nav** — chip nav now renders on narrow viewports with horizontal scroll, instead of being hidden. Jump-to-workspace is most useful on the smallest screens. Section headers also get a 44px minimum tap target (Apple HIG). Fixes [#5](https://github.com/jj358mhz/csl-slicer-console/issues/5).

---

## [1.2.0] — 2026-09-11

### Added

- 📋 **Copy JSON button** — hovering a slicer result block reveals a copy icon in the top-right corner. Click to copy the raw JSON body to the clipboard; a green "Copied" flash confirms. Only shows when the response is structured JSON (skipped for plain-text and errors).

---

## [1.1.1] — 2026-09-11

### Fixed

- 🧭 Workspace chip nav now updates the active chip immediately on click, so short/collapsed dashboards give visual feedback even when there's no scroll to perform.
- 📏 Sections scroll to just under the sticky chip nav (via `scroll-margin-top`) instead of hiding behind it.
- 🚢 Deploy workflow now pulls tags before running `git describe`, so the version badge reflects the tagged release instead of falling back to the short SHA.
- 🐳 Dockerfile `ARG APP_VERSION` moved to the end of the runtime stage so cache invalidates only on that final layer when the version changes, instead of silently reusing a cached `dev` value.

---

## [1.1.0] — 2026-09-11

### Added

**Dashboard navigation**
- 📁 **Collapsible workspace sections** — click a section header to collapse or expand its slicer grid. Chevron rotates, cards slide with a 200ms max-height transition. Collapsed state persists in `localStorage` per workspace username.
- 🔢 **Slicer counts** — each section header shows the number of slicers in that workspace, e.g. `EVERPASS (12)`.
- 🧭 **Workspace chip nav** — sticky chip row under the page header for jumping between workspaces. Click a chip to smooth-scroll to that section; the active chip highlights automatically as you scroll (IntersectionObserver-driven). Hides on narrow viewports.
- ⏬ **Collapse all / Expand all** — buttons in the page header alongside the Dry-run toggle. Bulk-toggle every section at once, state syncs to storage.
- ✅ **Batch-safe collapse** — batch bar count still reflects checked slicers inside collapsed sections. Section-level "Select all" works without expanding the section.
- 📱 **Narrow-viewport safety** — under 640px, chip nav hides and sections force open so the dashboard stays usable on mobile.

**Version transparency**
- 🏷️ **Live version badge** — magenta pill in the top nav shows the deployed version (from `git describe --tags --always --dirty`), links to the GitHub releases page. Reads from the `APP_VERSION` env var baked in at Docker build time by the deploy workflow; falls back to `dev` locally.

### Changed

- 🎨 CSS reorganized: new section `19. Dashboard nav & collapsible sections` and `20. Version badge` appended at the end of `app.css`.
- 🚢 Deploy workflow now injects the git-described version as a Docker build arg on every push.

---

## [1.0.1] — 2026-09-12

### Added

- 🎨 Favicon set (multi-size `.ico` + PNG + `apple-touch-icon` + web manifest) matching the magenta gradient brand mark.
- 📝 Every page title now reads `CSL Slicer Console — <page>` so the site name stays visible in the browser tab strip.

---

## [1.0.0] — 2026-09-11

Initial public release. Replaces the single-user Python CLI with a
multi-tenant Flask + HTMX web app.

### Added

**Auth & multi-tenancy**
- 🔐 Email + argon2 password auth with Flask-Login sessions and CSRF protection.
- 👥 Admin vs regular user roles. Admins see every active slicer; regular users see only their assigned slicers.
- 🧑 Full user CRUD with self-protection (can't demote or delete yourself).
- 🎯 Per-user slicer assignment via checkbox UI (grouped by Uplynk account).

**Uplynk integration**
- 🏢 Register multiple Uplynk workspaces with two credential types, stored **Fernet-encrypted** at rest:
  - **Legacy API key** — CSL slicer control (SHA1-signed body).
  - **Scoped API Key** *(optional)* — upload the `.env` file from Uplynk's Scoped API Keys page.
- 🔑 Scoped keys authenticate via ES256-signed JWTs sent in `X-Auth-Uplynk-Jwt`, matching Uplynk's XAuth spec.
- 🔍 One-click **Sync** populates slicers from `/api/v4/ingest/cloud-slicers/live/slicers` — no more hand-maintained URL lists.
- ✏️ Manual slicer entry as a fallback when a scoped key isn't available. Manual slicers survive sync cycles.

**Dashboard & control**
- 🎛️ HTMX dashboard grouped by workspace with live state pills (Slicing / AdBreak / Blackout / Stopped).
- 🕹️ Four action buttons per slicer — `status`, `state`, `content_start`, `blackout` — inline JSON results, no page reload.
- ✅ Batch operations: per-card checkboxes, section select-all, floating action bar, parallel fire, confirmation modal for destructive multi-slicer actions.
- 🧪 Dry-run toggle skips the real API call but still writes an audit event.

**Audit trails**
- 📜 **Slicer Control log** — every attempt with actor, HTTP status, response snippet, and dry-run flag. Per-user history view + admin filter by user / slicer / method.
- 🕵️ **Admin Activity log** — user CRUD, account CRUD, slicer CRUD, and sync events. Filter by actor and category.

**UI polish**
- 🎨 Uplynk-inspired dark theme (near-black surfaces, magenta `#ec1e79` accent, semantic state colors). Design language shared with `scte-plugin-generator`.
- 🏷️ Gradient "C" brand mark in the top nav.

**Ops & tooling**
- 🐳 Multi-stage Dockerfile using [uv](https://docs.astral.sh/uv/) for fast, reproducible builds; runs as non-root.
- 🧩 Portainer-managed Git-backed stack on `auth-net`, fronted by Caddy at `csl.telcomjj.com`.
- ⚙️ GitHub Actions: lint + tests on PRs; SSH deploy to the Utility Pi on push to `main`.
- 🧪 95 tests covering crypto, discovery client, sync service, CRUD flows, auth, HTMX routes, and audit views.
- 📚 README documents architecture, data model, dev workflow, and deployment.