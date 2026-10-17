# Changelog

All notable changes to **csl-slicer-console** are documented here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.12.0] - 2026-10-16

### ✨ Added
- **Slicer config panel.** A **Config** toggle on each slicer card shows or
  hides the slicer's configuration from the v4 retrieve endpoint: protocol
  (and RIST profile), region, slicer version, encoding profile, plugin
  (`<id> · v<version>`, or `none`), source IP, target state, description,
  created time, the streaming URL, slicer API URL and passphrase (each with
  a copy button), and the line-delimited `configuration` string as a
  `key: value` table. Open panels are remembered across reloads.
  Closes [#49](https://github.com/jj358mhz/csl-slicer-console/issues/49).
- The config is **captured once per run**: when a poll first sees the slicer
  running (including starts made outside the console), after a console
  Start, or the first time a running slicer has nothing stored. It's stored
  server-side (encrypted, since it carries the passphrase), shared by every
  user, and never re-queried while the slicer runs. Opening the panel reads
  only the database.
- After a stop the last config stays visible, marked **From last run**,
  until the next start replaces it. A failed capture is shown in the panel
  alongside the previous capture and retried on the next poll.

### ♻️ Changed
- New migration adds `config_encrypted`, `config_captured_at`,
  `config_capture_pending` and `config_error` to `slicers`.

---

## [1.11.0] - 2026-10-10

### ✨ Added
- **Blackout** and **Content Start** now get the same 1s follow-up as
  Start/Stop: after a successful console action, the slicer is polled every
  second until the badge reads Blackout / Slicing (capped at 30s), and the
  result panel shows the state timeline with dwell times. Previously the
  badge kept its old state until the next 30s poll.
  Closes [#47](https://github.com/jj358mhz/csl-slicer-console/issues/47).

### ♻️ Changed
- If a followed action never reaches its expected state before the cap,
  the timeline's live counter freezes instead of counting up forever.

---

## [1.10.0] - 2026-10-10

### ✨ Added
- **State timeline in the Start/Stop result panel.** After a successful
  Start or Stop, the result panel lists each state the slicer passes
  through, the local time it was first seen, and how long it stayed
  (e.g. `Slicing 2s → Stopping 4s → Stopped`). The current state's time
  counts up live. Built client-side from the 1s badge refresh, so accurate
  to about a second; nothing is stored.
  Closes [#45](https://github.com/jj358mhz/csl-slicer-console/issues/45).

### ♻️ Changed
- **Stopping** now gets the same 1s badge refresh as Initializing, so
  Stopping → Stopped shows promptly even when the stop came from outside the
  console. The Stop watch hands off to it, as Start does at Initializing,
  so a slicer never gets more than one request per second.

---

## [1.9.3] - 2026-10-10

### ♻️ Changed
- **Stopping** state badge now pulses, alongside Initializing, Slicing and
  Blackout. It keeps the same grey fill as Stopped; only a soft white glow
  is added. Closes [#43](https://github.com/jj358mhz/csl-slicer-console/issues/43).

---

## [1.9.2] - 2026-10-10

### ♻️ Changed
- **Ready** state badge is now solid green (`#58b263`) with dark text,
  instead of the plain grey fallback. It does not pulse.
  Closes [#41](https://github.com/jj358mhz/csl-slicer-console/issues/41).

---

## [1.9.1] - 2026-10-10

### 🐛 Fixed
- After **Start** (or **Stop**), the badge stayed on its old state until the
  next 30s poll, so a fast startup could skip past **Initializing** without
  it ever showing (e.g. a `start_blackout` slicer going Stopped →
  Initializing → Blackout inside one poll window). A successful, non-dry-run
  Start/Stop, from a tile or the batch bar, now polls that slicer every
  second until it leaves Stopped (start) or reaches Stopped (stop), for up
  to 2 minutes. Once it's Initializing, the badge's own 1s poll takes over.
  Closes [#39](https://github.com/jj358mhz/csl-slicer-console/issues/39).

### ♻️ Changed
- **Blackout** badges now pulse too (purple glow), alongside Initializing and
  Slicing.

---

## [1.9.0] - 2026-10-10

### ✨ Added
- **Initializing** state badge is now mustard (`#d4aa3c`) with dark text,
  instead of the plain grey fallback.
- While a slicer is **Initializing**, its tile refreshes every second (via
  the single-slicer state route) until it moves to Slicing, Ready,
  Blackout, or any other state, then drops back to the 30s workspace poll.
  Closes [#33](https://github.com/jj358mhz/csl-slicer-console/issues/33).
- **Initializing** and **Slicing** badges pulse with a soft glow (1.6s
  cycle) so live and starting-up slicers stand out. The pulse is pinned
  to the page clock, so poll swaps don't restart it, and it is turned off
  under `prefers-reduced-motion`.

---

## [1.8.0] - 2026-10-10

### ✨ Added
- Confirmation dialog before **Start**, **Stop**, **Content Start**, and
  **Blackout** on the individual slicer tile buttons, which previously fired
  immediately. The dialog names the slicer and explains what the action does.
  Status and State still fire without a prompt, and dry-run mode skips the
  dialog. Closes [#31](https://github.com/jj358mhz/csl-slicer-console/issues/31).

### ♻️ Changed
- Batch-bar confirmation now covers **Start** too, and applies when a single
  slicer is selected (previously it only prompted for 2+ slicers).
- The confirmation dialog is keyboard-accessible: focus starts on Cancel,
  Tab stays inside the dialog, Esc or a backdrop click cancels, and focus
  returns to the button that opened it. The Confirm button is tinted to
  match the action (purple Blackout, red Stop, green Start / Content Start).

---

## [1.7.1] - 2026-10-06

### ♻️ Changed
- Blackout action button is now purple, matching the Blackout state badge
  (`#9547F3`), instead of sharing Stop's red treatment. Applies to both the
  per-tile button and the batch toolbar button.
  ([#30](https://github.com/jj358mhz/csl-slicer-console/pull/30))

### 📚 Docs
- README dashboard highlight corrected from four to six action buttons per
  card (Start/Stop were added in 1.4.0) and notes the color coding.

---

## [1.7.0] - 2026-09-23

### ✨ Added
- Live thumbnail previews on slicer tiles — a small preview image next to
  the slicer name, sourced from Uplynk's `thumb_url`, so you can glance at
  a tile and confirm a live slicer is actually receiving video. Refreshes
  in place via the existing 30s HTMX poll, same as the state badge. Served
  through a backend proxy (Uplynk returns the URL over plain `http://`,
  which an HTTPS-served console can't embed directly) with a short
  in-process cache to collapse duplicate fetches from concurrent viewers.
  Closes [#28](https://github.com/jj358mhz/csl-slicer-console/issues/28).

---

## [1.6.0] - 2026-09-17

### ✨ Added
- `docker-compose.local.yml` overlay and a new README "Running your own
  instance" section for self-hosting outside the author's homelab — plain
  bridge network and a published host port instead of the `auth-net`
  external network the production compose file assumes. (#25)

---

## [1.5.0] - 2026-09-13

### ♻️ Changed
- Coalesced dashboard state polling — one `list_slicers()` call per account per 30-second interval, instead of one `retrieve_slicer()` call per slicer. For a 9-slicer account that's a 9× reduction in Uplynk API traffic. Per-slicer polling on `GET /slicers/<id>/state` stays available for on-demand refresh. (#16)
- Skipped no-op DB writes on state polls. When `last_state` and `connection_mode` both match what's already on the row, the poll commits nothing (including `last_seen_at`). For a steady-state fleet that's zero SQLite writes across a polling interval rather than one per slicer. (#15)

---

## [1.4.0] - 2026-09-13

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