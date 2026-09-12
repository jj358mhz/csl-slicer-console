# csl-slicer-console

Web console for controlling Uplynk CSL ingest slicers.

Multi-user Flask app with per-account API credentials, auto-discovery of
slicers via the Uplynk v4 API, and audit-logged control actions
(`/blackout`, `/content_start`, `/state`, `/status`).

**Status:** under construction. See CHANGELOG for progress.

## Local development

```bash
cp .env.example .env
# edit .env — generate SECRET_KEY and FERNET_KEY
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

## Production deployment

Deployed via Portainer as a Git-backed stack. Image built and pushed to
GHCR by GitHub Actions on push to `main`.

## License

MIT

Deployed at csl.telcomjj.com
