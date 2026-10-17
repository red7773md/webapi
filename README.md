# Gemini Archive — web UI

A small single-admin web app, styled as an old terminal archive. It wraps the
unofficial [`gemini-webapi`](https://pypi.org/project/gemini-webapi) client and uses
your **own browser session cookies** for a consumer Gemini account. It does not use
an API key.

> This uses undocumented Google endpoints. It can break when Google changes its
> web app, and it can get the account it runs on rate-limited or locked. Use a
> secondary account.

See `context.md` for the full project context, decisions and next steps.

## Status

- [x] Step 1: admin login, cookie import, uplink verification, terminal UI, Vercel-ready storage
- [x] Step 2: chat (multi-turn, file attachments, history)
- [x] Step 3: image generation
- [x] Step 4: deep research (plan, confirm, background polling, report view)

## Run locally

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cp .env.example .env        # then set ADMIN_PASSWORD
set -a; . ./.env; set +a
.venv/bin/uvicorn index:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`.
Locally, cookies are kept in `data/` (owner-only). Set `BLOB_READ_WRITE_TOKEN` to use
a Vercel Blob store instead.

## Deploy on Vercel

1. Import the repo into Vercel. No build step is needed.
2. Create a **private** Blob store and connect it to the project. This adds
   `BLOB_READ_WRITE_TOKEN` automatically. A **public** store is refused: every cookie
   save fails with a message that asks for a private store.
3. Add these environment variables:
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD` (a long random string)
   - `APP_SECRET_KEY` (a long random string; it signs the login session)
4. Redeploy. `/healthz` should return `{"ok": true}`. If it returns 503, the response
   names the variables still missing.

## Importing Gemini cookies

1. In a browser, sign in to gemini.google.com with the secondary account.
2. Open DevTools (F12).
   - Chrome: Application > Cookies > `https://gemini.google.com`
   - Firefox: Storage > Cookies > `https://gemini.google.com` (Firefox tends to last longer)
3. Copy `__Secure-1PSID` and `__Secure-1PSIDTS`, or copy the `cookie` request header
   from any request in the Network tab.
4. On the SESSION screen, paste it and press SAVE COOKIES. The app then verifies it.

Accepted formats: a cookie header string, a JSON object, a cookie-extension JSON
export, or the bare PSID value (optionally followed by a space and the PSIDTS value).

## API for scripts

Set `APP_API_KEY` (at least 24 characters) in the environment, or in Vercel's project settings. This is this app's key, not a Gemini key. Send it on each call as `Authorization: Bearer <key>`, as an `X-API-Key` header, or as `?key=<key>` at the end of the URL. The chat, image and research routes accept it. The Gemini cookie and admin routes need the browser login.

```text
POST /api/chat/send          {"message": "..."}                  -> 200 answered, or 202 waiting with the chat id
POST /api/chat/send          {"chat_id": "<id>", "message": "..."} -> a follow-up in the same chat
GET  /api/chat/<id>          -> status answered | waiting | failed, and the messages
POST /api/research           {"prompt": "..."}                   -> job id, planning
GET  /api/research/<id>      -> status and the plan or report
POST /api/research/<id>/start
```

Gemini is connected through the browser (cookies on the Session screen). Calls fail with 409 "not connected" until that is done. Wrong keys are throttled like failed logins.

## Security notes

- Cookies and the verification result are stored together in one record: in Blob
  on Vercel, or in an owner-only file locally. Values are never sent back to the
  browser in full.
- The login throttle is in memory. It locks an IP for 5 minutes after 5 failed
  logins, and it is per instance on Vercel.
- The app sends a strict Content-Security-Policy and serves no API docs.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The tests use a fake Gemini client and an in-memory Blob API. They make no network calls.
