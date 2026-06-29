# Hestia Vigil

Use NASA FIRMS (Fire Information for Resource Management System) satellite data to pull active fire detections. A simple web dashboard fetches FIRMS CSV, plots detections on a map, and sends alerts when new hotspots appear in a watch zone.

## Secrets

Vigil keeps non-secret settings in `config.yaml` and private values in `.env`.

1. Copy `.env.example` to `.env`.
2. Fill in:
   - `NASA_FIRMS_MAP_KEY`
   - `DISCORD_WEBHOOK_URL` (optional)
   - `EMAIL_USER` (optional)
   - `EMAIL_APP_PASSWORD` (optional)
3. Do not commit `.env`.

The existing `.gitignore` already ignores `.env`.
