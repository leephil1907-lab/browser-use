# Browser Use Decision Workbench

This directory contains the production browser-agent control surface for the Browser Use repository.

## Principles

- No demo records.
- No seeded projects.
- No simulated browser execution.
- No client-side action allowlist or pre-approved action catalogue.
- No fabricated metrics, confidence values, URLs, task results or task history.
- Runtime state comes from the configured backend.
- Credentials remain server-side.

## Backend contract

The frontend expects:

- `GET /api/health`
- `POST /api/agent/run`
- `GET /api/tasks`
- `GET /api/decision/projects`
- `POST /api/decision/saved`

The exact response schema may be adapted by the backend. The UI intentionally renders only fields returned by the server.

## Awesome Jev integration

The Decision Radar is designed around the capabilities surfaced by the `awesome-jev-projects` repository: searchable discovery, category/tag filtering, source/evidence links, and inspectable decision points. The workbench does not copy or ship that repository's catalogue into the frontend. Configure a server-side `DECISION_RADAR_URL` adapter to expose those live records.

The upstream radar and agent-skill resources are documented at `https://logicrw.github.io/awesome-jev-projects/en/` and `https://logicrw.github.io/awesome-jev-projects/skill.md`.

## Decision Radar

The Decision Radar surface is designed to consume live data from the Awesome Jev Projects-derived source. It does not bundle a local catalogue. If the endpoint is unavailable or returns no records, the UI remains empty.

## Security

Do not put Browser Use, model-provider, or radar credentials in frontend JavaScript, localStorage, query parameters, or committed files. Configure them on the server.

## Serving

Serve `web/` from the same origin as the Browser Use backend where possible so relative API paths resolve naturally. For a separate origin, configure `window.__BROWSER_USE_API_BASE__` before `app.js` loads and configure the server's CORS policy accordingly.

## Production behavior

A disconnected backend produces a disconnected state. A failed request produces an error state. There is deliberately no local fallback that fabricates browser activity or data.