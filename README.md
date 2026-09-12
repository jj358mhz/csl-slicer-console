# 📺 csl-slicer-console

[![Release](https://img.shields.io/github/v/release/jj358mhz/csl-slicer-console?label=release&color=ec1e79)](https://github.com/jj358mhz/csl-slicer-console/releases/latest)

Multi-user web console for controlling Uplynk CSL ingest slicers. Replaces
the single-user Python CLI with a browser-based, multi-tenant admin surface —
per-user slicer assignments, JWT-authenticated auto-discovery, HTMX-driven
control buttons, and full audit trails.

## 🌟 Highlights

- 🔐 **Auth & multi-tenancy** — email + argon2 passwords, Flask-Login
  sessions, CSRF protection, admin vs regular users. Admins see all active
  slicers; regular users see only the slicers assigned to them.
- 🏢 **Uplynk account management** — admins register workspaces with two
  credential types, stored **Fernet-encrypted** at rest:
    - **Legacy API key** — CSL slicer control (SHA1-signed requests)
    - **Scoped API Key** *(optional)* — upload the `.env` file from Uplynk's
      Scoped API Keys page. Used for v4 API discovery via ES256-signed JWTs
      sent in `X-Auth-Uplynk-Jwt`.
- 🔍 **Auto-discovery + fallback** — one-click Sync populates slicers from
  `/api/v4/ingest/cloud-slicers/live/slicers`. Manual slicer CRUD available
  for accounts without a scoped key.
- 🎛️ **HTMX dashboard** — slicers grouped by workspace with live state
  pills (Slicing / AdBreak / Blackout / Stopped). Four action buttons per
  card, inline JSON results, no page reload. **Collapsible workspace
  sections** with per-workspace persistence, sticky chip nav for jumping
  between workspaces, and slicer counts per section.
- ✅ **Batch operations** — checkbox per card, section select-all, floating
  action bar, parallel fire against selected slicers, confirmation modal
  for destructive multi-slicer actions.
- 🧪 **Dry-run mode** — dashboard toggle skips the real API call but writes
  an audit event so you can rehearse safely.
- 📜 **Two audit trails** —
    - *Slicer Control*: every `/blackout`, `/content_start`, `/state`,
      `/status` attempt with actor, status code, and response snippet.
    - *Admin Activity*: every user CRUD, account CRUD, slicer CRUD, and sync
      event.
- 🎨 **Uplynk-inspired UI** — dark near-black surfaces, magenta (`#ec1e79`)
  accent, semantic state colors. Design language shared with
  `scte-plugin-generator` for a unified Utility Pi look.

## 🚀 Live Deployment

Deployed on the homelab Utility Pi and fronted by Caddy at **[csl.telcomjj.com](https://csl.telcomjj.com)** *(LAN
only)*.

## 🏗️ Architecture

| Layer            | Tech                                                     |
|------------------|----------------------------------------------------------|
| Web framework    | Flask 3 + Jinja2                                         |
| ORM & migrations | SQLAlchemy 2 + Alembic                                   |
| Auth             | Flask-Login + Flask-WTF (CSRF) + argon2                  |
| Database         | SQLite (single-file, volume-mounted)                     |
| Frontend         | Server-rendered HTML + HTMX (no build step)              |
| Crypto           | `cryptography` (Fernet at rest, ES256 JWT for Uplynk v4) |
| WSGI server      | gunicorn                                                 |
| Runtime          | Python 3.12 in a slim Debian container                   |
| Package manager  | [uv](https://docs.astral.sh/uv/)                         |
| Reverse proxy    | Caddy (in the homelab-utility stack) with Cloudflare TLS |
| Orchestration    | Portainer-managed Docker Compose stack                   |

## 🗂️ Data model

| Table             | Purpose                                                   |
|-------------------|-----------------------------------------------------------|
| `users`           | Login accounts (admin flag, active flag)                  |
| `uplynk_accounts` | Workspaces + encrypted credentials                        |
| `slicers`         | Discovered or manually-added slicers                      |
| `user_slicers`    | Many-to-many: which users control which slicers           |
| `audit_events`    | Slicer control history (per-user history + admin filters) |
| `admin_events`    | User / account / slicer CRUD + sync events                |

## 🧑‍💻 Local development

Prerequisites: Docker Desktop, [uv](https://docs.astral.sh/uv/), Python 3.12.

```bash
git clone git@github.com:jj358mhz/csl-slicer-console.git
cd csl-slicer-console

# Install deps (creates .venv/, writes uv.lock)
uv sync

# Copy env template and fill in real secrets
cp .env.example .env
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"   # SECRET_KEY
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FERNET_KEY

# Start (dev overlay hot-reloads code + runs Flask in debug)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# Hit the app at http://localhost:5050
curl http://localhost:5050/health
```

Run tests on the host (dev deps aren't installed in the container):

```bash
uv run pytest -v
```

Lint & format:

```bash
uv run ruff check .
uv run ruff format .
```

## 🚢 Production deployment

Runs as a Portainer-managed stack pulling directly from this repo:

- **Stack name:** `csl-slicer-console`
- **Build method:** Repository (Portainer polls `main`)
- **Compose path:** `docker-compose.yml`
- **Compose working dir:** `/data/compose/<stack-id>` on the Pi
- **Network:** `auth-net` (shared with Caddy) — no host port publish

### Environment variables

Set via Portainer's **Advanced mode** env editor (Stack → Editor → Environment variables). Stored in Portainer, never
committed to Git.

| Variable                   | Purpose                                                    |
|----------------------------|------------------------------------------------------------|
| `SECRET_KEY`               | Signs Flask session cookies                                |
| `FERNET_KEY`               | Encrypts Uplynk API keys at rest                           |
| `BOOTSTRAP_ADMIN_EMAIL`    | First-run admin login                                      |
| `BOOTSTRAP_ADMIN_PASSWORD` | First-run admin password                                   |
| `LOG_LEVEL`                | `info` / `debug` / `warning`                               |
| `GUNICORN_WORKERS`         | Worker count (default 3)                                   |
| `UPLYNK_API_BASE`          | Uplynk v4 API base (default `https://services.uplynk.com`) |
| `DISCOVERY_SYNC_INTERVAL`  | Auto-sync interval in minutes (0 = disabled)               |

Bootstrap only runs when the `users` table is empty; subsequent env-var
changes to `BOOTSTRAP_ADMIN_*` are ignored. Reset an admin password
in-place with:

```bash
docker exec csl-slicer-console python -c "
from app import create_app; from app.auth.passwords import hash_password
from app.models import db, User
app = create_app()
with app.app_context():
    u = db.session.query(User).filter_by(email='YOUR_EMAIL').one()
    u.password_hash = hash_password('NEW_PASSWORD')
    db.session.commit()
"
```

### Caddy routing

In `homelab-utility/caddy/Caddyfile`:

```caddy
csl.telcomjj.com {
	import cf_tls
	reverse_proxy csl-slicer-console:5000
	import security_headers
	log { output file /data/logs/csl.log { roll_size 10mb roll_keep 5 } format json }
}
```

## ⚙️ CI/CD

GitHub Actions runs on every push:

- **`.github/workflows/ci.yml`** — `ruff check` + `ruff format --check` +
  `pytest` on every PR and non-main push.
- **`.github/workflows/deploy.yml`** — on push to `main`, SSHes into the
  Utility Pi, `git pull`, `docker compose up -d --build`, and prunes stale
  images. Portainer also polls the repo and keeps its managed copy in sync.
- **Version badge** — the deploy workflow injects `git describe --tags
  --always --dirty` as a Docker build arg (`APP_VERSION`), surfaced as a
  magenta pill in the top nav so you can see at a glance which release
  (or unreleased main commit) is running.

Secrets required for `deploy.yml`: `DEPLOY_HOST`, `DEPLOY_USER`,
`DEPLOY_SSH_KEY` (dedicated deploy keypair, `authorized_keys` entry on the Pi).

## 🧪 Test coverage (95 tests)

- **`test_crypto.py`** — Fernet round-trip, key masking, error paths
- **`test_bootstrap.py`** — admin creation, idempotence
- **`test_auth.py`** — login/logout, session redirects, invalid creds
- **`test_scoped_env.py`** — Uplynk `.env` file parser
- **`test_discovery.py`** — JWT signing (ES256), XAuth header, 401/403
  handling
- **`test_sync.py`** — slicer upsert, deactivation, manual-slicer guardrail
- **`test_admin_accounts.py`** — Uplynk account CRUD with file uploads
- **`test_admin_users.py`** — user CRUD, self-protection, slicer assignment
- **`test_admin_slicers.py`** — manual slicer entry fallback
- **`test_admin_events.py`** — admin activity logging
- **`test_csl.py`** — SHA1 signing, response parsing, summary formatting
- **`test_slicer_service.py`** — authorization, audit logging, dry-run
- **`test_slicer_routes.py`** — HTMX endpoint, per-user isolation
- **`test_audit_log.py`** — admin filters, per-user history, XSS-safe render

## 📁 Repo layout

```
csl-slicer-console/
├── app/
│   ├── __init__.py             # Flask app factory
│   ├── config.py               # Config (env vars, instance-based)
│   ├── models.py               # SQLAlchemy models
│   ├── bootstrap.py            # First-run admin creation
│   ├── crypto.py               # Fernet helpers
│   ├── admin/                  # Admin CRUD + slicer assignment + audit UI
│   ├── auth/                   # Login / logout / password hashing
│   ├── main/                   # Dashboard + per-user history
│   ├── slicers/                # HTMX control endpoints
│   ├── uplynk/
│   │   ├── csl.py              # CSL SHA1 slicer control client
│   │   ├── discovery.py        # v4 API JWT client
│   │   ├── scoped_env.py       # .env file parser
│   │   └── sync.py             # Discovery → DB upsert
│   ├── static/css/app.css      # Uplynk-inspired dark theme
│   └── templates/              # Jinja2 templates
├── migrations/                 # Alembic
├── tests/                      # Pytest suite
├── .github/workflows/          # CI + deploy
├── docker-compose.yml          # Production stack
├── docker-compose.dev.yml      # Local dev overlay
├── Dockerfile                  # Multi-stage build (uv → runtime)
├── entrypoint.sh               # Runs migrations then gunicorn
├── gunicorn.conf.py
└── pyproject.toml              # Deps + ruff config
```

## 📄 License

MIT