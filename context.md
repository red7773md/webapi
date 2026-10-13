# Project context — Gemini Archive web UI

Read this first when starting a new chat. It says what exists, what was decided
and why, and what comes next. Keep it up to date after each step.

## What this is

A small single-admin web app ("terminal archive" theme) that wraps the unofficial
`gemini-webapi` client, so Emdad can use his **consumer Gemini account** (cookie
session, not an API key) from a browser. Purpose: test Gemini chat, image
generation and deep research endpoints from a UI, and later add an unofficial
ChatGPT client the same way.

Risk accepted by Emdad: unofficial endpoints can break, and the account used
(a secondary account) can be rate-limited or locked.

## Where things are

- Working copy: `/home/claude/gemini-webui` (git, branch `main`)
- GitHub: `red7773md/webapi` (this project; deployed to Vercel from here)
- Reference pattern: his Instagram MCP on Vercel, `red7773md/instagram-mcp-api`
  (Python, Upstash/Blob, entrypoint pattern). Not this project; only a model.
- Vercel: project `webapi` (id `prj_QgfFfjnb1360s1v3ibi7MCS8yegH`, team
  `emdadhussain950-gmailcoms-projects`), linked to `red7773md/webapi`, production
  branch `main`. Live URL: https://webapi-emdadhussain950-gmailcoms-projects.vercel.app
  (see "Deployment" below)
- The sandbox cannot reach `gemini.google.com` (egress policy 403). Live Gemini
  tests must be run from Emdad's machine or the deployed app.

## Status

| Step | What | State |
|---|---|---|
| 1 | Admin login, cookie import, verify uplink, terminal UI, Vercel-ready storage | code done (37 tests pass); deployed, builds READY on `48c6040`; waiting for Emdad to add env vars and connect Blob |
| 2 | Chat module (multi-turn, file attachments, history) | not started |
| 3 | Image generation module | not started |
| 4 | Deep research module (background jobs, polling, report view) | not started |
| — | ChatGPT client (unofficial, cookie-based) | not started |

Step 1 was committed and deployed in this session. Check `git log --oneline` and
the Vercel dashboard for the live state, because this table can fall behind.

## Architecture

```
index.py                  Vercel entrypoint: `app = build_entrypoint()`
pyproject.toml            [project] (needed by Vercel's `uv lock`) and [tool.vercel] entrypoint = "index:app"
vercel.json               maxDuration 60 for index.py
app/
  entrypoint.py           build_entrypoint(): real app, or a 503 app that names missing config
  main.py                 create_app(): routes, admin session, security headers, static UI
  config.py               load_settings(): env vars; ConfigError lists what is missing
  security.py             admin credential check (constant time) + login throttle
  storage.py              KeyValueStore: LocalFileStore (dev) and BlobStore (Vercel Blob)
  cookie_store.py         cookie parsing/masking; CookieRecord (cookies + verification state)
  gemini_service.py       GeminiService: verify, status, require_client (per event loop)
static/                   index.html, style.css, app.js (no build step, textContent only)
tests/test_step1.py       37 tests; fake Gemini client and in-memory Blob HTTP
```

Key decisions:
- **Storage on Vercel is Vercel Blob (private store), via its HTTP API.** Endpoints
  and headers come from the official `@vercel/blob` 2.8.1 source: PUT
  `https://blob.vercel-storage.com/?pathname=…` (x-api-version 12,
  x-vercel-blob-access private), GET from `https://<storeId>.private.blob.vercel-storage.com/<path>`
  with a bearer token, POST `https://blob.vercel-storage.com/delete` with `{"urls": [...]}`.
  The store ID is the 4th `_` segment of `BLOB_READ_WRITE_TOKEN`
  (`vercel_blob_rw_<storeId>_<secret>`).
- **One record holds cookies and verification state** (`gemini_cookies.json` in the
  store). Every serverless instance therefore shows the same status.
- **No long-lived state in memory is trusted.** The Gemini client is cached per
  event loop; `require_client()` rebuilds it when the loop changes.
- **Verification has a 45 s timeout** so it finishes inside the 60 s function limit.
- **Missing config returns 503 with the names of the missing variables**, not a crash.
- Admin login uses a signed session cookie (SameSite=strict, HTTPS-only on Vercel).
- No API docs endpoints (`/docs`, `/openapi.json`) are exposed.

## Environment variables

| Name | Where | Notes |
|---|---|---|
| `ADMIN_USERNAME` | Vercel + local | Required |
| `ADMIN_PASSWORD` | Vercel + local | Required. Long random string |
| `APP_SECRET_KEY` | Vercel (required there) | Signs the session cookie. Local: auto-generated into `data/.app_secret` |
| `BLOB_READ_WRITE_TOKEN` | Vercel (added by connecting Blob) | Required on Vercel. Local: optional, uses Blob if set |
| `COOKIE_SECURE` | optional | `1` forces HTTPS-only cookies (default `1` on Vercel) |
| `GEMINI_WEBUI_DATA` | optional, local | Folder for local cookies and secret (default `./data`) |
| `VERCEL` | set by Vercel | `1` enables Vercel mode |

## Deployment (Vercel)

Current state (2026-10-13):
- First build failed on `7e18c36`: Vercel runs `uv lock`, which needs a `[project]`
  table in `pyproject.toml`. Fixed in `48c6040`. That build is READY.
- `/healthz` returns 503 and names `ADMIN_USERNAME, ADMIN_PASSWORD, APP_SECRET_KEY,
  BLOB_READ_WRITE_TOKEN`. Nothing is configured yet, so this is expected.
- Waiting on Emdad: create a private Blob store and connect it, then add the three
  admin/secret variables, then redeploy. Do not print or request their values.
- Sandbox note: curl to the Vercel URL is refused by the egress proxy (403). Check the
  live site with the Vercel MCP `web_fetch_vercel_url` tool instead.

Steps:

1. Create a **private** Blob store and connect it to the project. It must be
   private, because the code writes with `x-vercel-blob-access: private`. A public
   store will reject or expose the cookie record.
2. Add `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `APP_SECRET_KEY` in Vercel.
3. Redeploy. `/healthz` should return `{"ok": true}`. If it returns 503, the
   message names what is still missing.
4. Open the site, log in, paste the Gemini cookies on the SESSION screen, and press
   VERIFY UPLINK.

Local run:
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env   # set ADMIN_PASSWORD
set -a; . ./.env; set +a
.venv/bin/uvicorn index:app --host 127.0.0.1 --port 8000
.venv/bin/python -m pytest -q
```

## Known limitations

- Cookies are stored in the Blob record as plain JSON. Blob access is only by the
  token. Encryption at rest is not implemented yet.
- The login throttle is in memory. On Vercel it is per instance, so it is weaker
  than a shared counter. Moving it into Blob or Redis is an option.
- `__Secure-1PSIDTS` rotates. The library refreshes it in memory, but the refreshed
  value is not written back to the record yet. If verification starts failing
  after a few days, that is the first thing to check.
- Chromium-based browsers issue device-bound cookies that can expire within hours.
  Firefox cookies last longer. This is in the README and the UI hint.
- Deep research runs for 5–10 minutes, which is longer than the 60 s function
  limit. Step 4 must start the job, store its ID, and poll with short requests.
  Do not wait inside one request.
- The sandbox cannot reach Google, so live Gemini calls are untested here.

## Next steps (in order)

1. Step 2, chat: `POST /api/chat/send` (uses `require_client()`, supports files
   via base64 or multipart), `GET /api/chat/history`, and a CHAT screen. Store chat
   metadata in Blob, not in memory. Verify with a live test from Emdad's machine.
2. Step 3, image generation: `POST /api/image/generate` returns the image(s),
   saved to Blob, with a URL in the UI.
3. Step 4, deep research: start job → store `{cid, research_id, status}` → poll
   endpoint → fetch report. Keep each request well under 60 s.
4. Then decide on the ChatGPT client (look at re-gpt first; check it is maintained).

## Working conventions

- Emdad prefers big builds in small steps: finish one step, commit, push, report,
  then continue.
- Commits end with the Co-Authored-By and Claude-Session trailers.
- Keep secrets out of the repo and out of chat output. Never print cookie values or
  env var values.
