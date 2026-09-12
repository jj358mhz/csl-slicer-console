# Changelog

All notable changes to **csl-slicer-console** are documented here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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