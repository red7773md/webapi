# Gemini Session Sync — browser extension

This extension copies your **Gemini login** from your own browser to your own **Gemini Archive**
server, so the server stays logged in when Google rotates the cookies. It sends only the two Gemini
cookies (`__Secure-1PSID` and `__Secure-1PSIDTS`), and only to the server address you give it. The
request is made over HTTPS and needs the server's sync key (`GEMINI_SYNC_KEY`).

It works in Chrome and Firefox (desktop). Whenever you open **gemini.google.com** in this browser,
it syncs your login in the background, at most once every 30 / 60 / 120 minutes (you choose). If you
synced recently, it does nothing.

## What you need

| Value | Where it comes from |
| --- | --- |
| Gemini Archive server URL | Your deployment address, for example `https://webapi-your-project.vercel.app` (no trailing slash needed) |
| Sync key | `GEMINI_SYNC_KEY`. Set it in that project's Vercel settings (at least 24 characters, different from `APP_API_KEY`), then redeploy. |

## Step 1 — Get the extension

Two ways:

- **Pre-configured copy (easiest):** sign in to your Gemini Archive page as the admin and use the
  extension download there. It already contains your server address and sync key, so you can skip
  Step 3. Keep that file private.
- **Source copy:** the `extension/` folder of the Gemini Archive repository. It has no keys, so you
  fill them in on the options page.

Unzip the file (or keep the folder) somewhere you will keep. Don't delete it afterwards, because the
browser loads the extension from it.

## Step 2 — Install it

### Chrome, Edge or Brave

1. Open `chrome://extensions` (or `edge://extensions`, `brave://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and select the folder.
4. Pin the extension from the puzzle icon if you want it visible.

### Firefox

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…**.
3. Select **`manifest-firefox.json`** inside the folder.

Firefox removes temporary add-ons when it restarts, so you'll repeat this step each time you start
Firefox. To make it permanent, Mozilla has to sign the extension, which this project doesn't do yet.

## Step 3 — Set it up

1. The options page opens after installation. If it doesn't, open the extension's **Options**
   (right-click its icon → Options, or `chrome://extensions` → Details → Extension options).
2. Fill in the **Gemini Archive server URL** and the **Sync key** (skip this if you used the
   pre-configured copy; the values are already there).
3. Choose how often an automatic sync may run (30 minutes, 1 hour or 2 hours).
4. Press **SAVE**, then **TEST SERVER**. You should see "Server reachable."
5. Log in to **gemini.google.com** in this same browser.
6. Press **SYNC NOW**. You should see "Gemini cookies saved."

## Day to day

- On gemini.google.com a round **G** button sits at the bottom right. Click it to see whether Gemini
  is logged in here, when it last synced, and any failure. **SYNC GEMINI** runs a sync now.
  **SETTINGS** opens the options page. The button is added by the extension itself; the Gemini page
  itself is not changed.
- Open gemini.google.com (or reload it). If the last successful sync is older than your interval, the
  extension sends your login to the server. You don't need to click anything.
- The badge shows `!` while the last attempt failed. After a failure the extension waits 10 minutes
  before trying again automatically. Press the sync button to try straight away.
- If you're logged out of Gemini in this browser, the attempt fails and says so. Log in there, and the
  next visit syncs.

## Security notes

- **The sync key is stored in plain text in this browser profile.** Anyone who can use your browser
  profile can read it. Don't use this on a shared computer.
- The extension asks for access to every https site because it has to reach your server address.
  You can narrow `host_permissions` in `manifest.json` to your server URL if you're comfortable
  editing it.
- The server accepts a sync only when the sync key matches. That key opens nothing else: the app's
  API key and a login do not work on that route. The server never sends the cookies back.
- Keep the key private. If it leaks, pick a new long random value for `GEMINI_SYNC_KEY`, redeploy,
  and update the extension.

## Troubleshooting

| Message | What to do |
| --- | --- |
| No Gemini login in this browser | Log in at gemini.google.com in this browser, then press SYNC GEMINI |
| The server rejected the sync key | Check the sync key matches `GEMINI_SYNC_KEY` on the Gemini Archive project |
| The server has no GEMINI_SYNC_KEY set yet | Add `GEMINI_SYNC_KEY` in Vercel and redeploy that project |
| Could not reach the server | Check the server URL and that the deployment is live |
| The extension was updated or reloaded | Reload the gemini.google.com tab |
