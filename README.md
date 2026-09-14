# QCE ChatLab Pull Adapter

A small authenticated adapter that exposes QQ Chat Exporter (QCE) conversations
through the [ChatLab Pull protocol](https://docs.chatlab.fun/standard/chatlab-pull).
ChatLab discovers the QQ sessions and only synchronizes the friends and groups
selected in its data-source screen.

The adapter is deliberately stateless. It reads QCE through its authenticated
HTTP API, translates messages into ChatLab Format v0.0.2, and exposes QCE's
newest-first pages oldest-first through ChatLab's `nextSince` cursor. It never
mounts NapCat or ChatLab data.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `QCE_BASE_URL` | none | QCE origin, without the `/qce` browser prefix |
| `QCE_TOKEN_FILE` | `/run/secrets/qce/token` | File containing the QCE access token |
| `ADAPTER_TOKEN_FILE` | `/run/secrets/adapter/token` | File containing the Pull bearer token |
| `SESSION_ALLOWLIST_FILE` | unset | Optional JSON allowlist (see below) |
| `QCE_TIMEOUT_SECONDS` | `45` | QCE request timeout |
| `SESSION_CACHE_SECONDS` | `30` | Session discovery cache lifetime |

An optional allowlist is an additional server-side restriction:

```json
{"groups":["123456"],"friends":["u_example"]}
```

When it is absent, all QCE sessions are discoverable to an authenticated
ChatLab instance; ChatLab still pulls only the conversations selected there.

## Endpoints

- `GET /healthz`: process liveness
- `GET /readyz`: QCE dependency readiness
- `GET /sessions`: authenticated ChatLab session discovery
- `GET /sessions/{id}/messages?format=chatlab`: authenticated full/incremental pull

Use `http://qce-chatlab-pull.chatlab.svc.cluster.local:8080/api/v1` as the
ChatLab data-source URL. Send the adapter token as a bearer token. QCE
credentials stay inside the adapter container. Legacy unprefixed routes remain
available for direct health checks and older clients.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
```
