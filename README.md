# Gemini Archive — web UI

A small single-admin web app that wraps the unofficial
[`gemini-webapi`](https://pypi.org/project/gemini-webapi) client, styled as an
old terminal archive. It uses your **own browser session cookies** for a
consumer Gemini account, not an API key.

> Read this first: this uses undocumented Google endpoints. It can break when
> Google changes its web app, and it can get the account it runs on rate-limited
> or locked. Use a secondary account.

## Status

- [x] Step 1: admin login, cookie import from browser, uplink verification, terminal UI
- [ ] Step 2: chat (multi-turn, file attachments, history)
- [ ] Step 3: image generation
- [ ] Step 4: deep research (background jobs, polling, report view)

## Setup

Requires Python 3.11 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then edit ADMIN_PASSWORD
set -a; . ./.env; set +a
.venv/bin/uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and log in with `ADMIN_USERNAME` and `ADMIN_PASSWORD`.

## Importing Gemini cookies

1. In a browser, sign in to gemini.google.com with the secondary account.
2. Open DevTools (F12).
   - Chrome: Application > Cookies > `https://gemini.google.com`
   - Firefox: Storage > Cookies > `https://gemini.google.com` (Firefox tends to last longer)
3. Copy `__Secure-1PSID` and `__Secure-1PSIDTS`. Or copy the `cookie` header from any
   request in the Network tab.
4. On the SESSION screen, paste it in and press SAVE COOKIES. The page then verifies it.

Accepted formats: a cookie header string, a JSON object, a cookie-extension JSON
export, or the bare PSID value (optionally followed by a space and the PSIDTS value).

## Security notes

- Cookies are stored in `data/gemini_cookies.json` with owner-only permissions
  (`0600`). They are plain text on disk. Treat the `data/` folder like a password file.
- The login throttle is in memory and resets when the server restarts. It locks an IP
  for 5 minutes after 5 failed logins.
- Behind a reverse proxy, set `COOKIE_SECURE=1` and serve only over HTTPS.
- The app serves no API docs, sets a strict Content-Security-Policy, and never
  echoes full cookie values back to the browser.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The tests use a fake Gemini client and make no network calls.
