# Handoff: Dev docker-compose overlay merge fix (#8)

## Project context

**Repo:** `jj358mhz/csl-slicer-console`
**Current version:** v1.3.0 (just shipped)
**Branch state:** on `main`, clean, PR #17 just merged (ruff per-file ignore)
**Stack:** Flask 3, Docker Compose, uv, Python 3.12

## What to build

Read the full issue for symptoms and root cause:
https://github.com/jj358mhz/csl-slicer-console/issues/8

Summary: `docker-compose.dev.yml` names its service `web` but base names it `csl-slicer-console`. Compose merges by service name, so the overlay creates a disconnected service instead of merging. Fix is renaming the dev overlay's service to match base.

## Decision already made

Issue #8 lists two options for handling the `auth-net` external network (which only exists on the Pi, not on the mac). **Go with option 1**: override `networks` in the dev overlay properly — drop `auth-net`, use a plain bridge — in the same PR as the service rename. Do not go with option 2 (documenting `docker network create auth-net` as a prereq).

## Files to touch

- `docker-compose.dev.yml` — rename service `web` → `csl-slicer-console`, drop vestigial `!reset` directives, add proper `networks` override.

## Files to NOT touch

- `docker-compose.yml` (base) — the `auth-net` external reference is correct for prod on the Pi.

## Verification

Before AND after the edit:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml config
```

After the edit:

1. Config output should show a single merged `csl-slicer-console` service (not two separate services).
2. Bring up clean, without pre-creating `auth-net`:
```bash
   docker compose -f docker-compose.yml -f docker-compose.dev.yml down
   docker network rm auth-net 2>/dev/null || true
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```
3. Container name should be `csl-slicer-console` (not `csl-slicer-console-web`).
4. App should be reachable at `http://localhost:${WEB_PORT:-5050}`.

## How to work on this

Standard flow — `gh issue develop 8 --checkout`, edit, verify, PR, admin-merge (branch protection blocks direct and auto-merge; use `--admin` since this is a solo repo).

## PR notes

Bug fix, no version bump (dev-only tooling, no user-facing or Pi-deployed change). No CHANGELOG entry.
