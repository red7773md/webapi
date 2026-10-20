// Gemini Session Sync — background worker. Works in Chrome (service worker) and Firefox (background script).
// Copies the two Gemini cookies from this browser to your Gemini Archive server, with its sync key.
// Nothing else is read or sent.
const GEMINI_URL = "https://gemini.google.com/";
const GEMINI_PAGE = /^https:\/\/gemini\.google\.com\//;
const COOKIES = ["__Secure-1PSID", "__Secure-1PSIDTS"];
const DEFAULTS = { serverUrl: "", syncKey: "", intervalMin: 60 };
const RETRY_MIN = 10; // after a failed automatic attempt, wait this long before trying again

let inFlight = false; // one automatic sync at a time

async function getConfig() {
  const { config = {} } = await chrome.storage.local.get("config");
  return { ...DEFAULTS, ...config };
}
async function getState() {
  const { state = {} } = await chrome.storage.local.get("state");
  return state;
}
async function setState(patch) {
  const s = await getState();
  await chrome.storage.local.set({ state: { ...s, ...patch } });
}
function ready(c) {
  return !!(c.serverUrl && c.syncKey);
}
async function cookieValue(name) {
  try {
    const c = await chrome.cookies.get({ url: GEMINI_URL, name });
    return c && c.value ? c.value : null;
  } catch (e) {
    return null;
  }
}

// A pre-configured download ships with defaults.json (server address + sync key). Apply it once,
// so the extension works straight after install. The source copy has no defaults.json: setup is manual.
async function applyDefaults() {
  const { config } = await chrome.storage.local.get("config");
  if (config && config.syncKey) return;
  try {
    const r = await fetch(chrome.runtime.getURL("defaults.json"));
    if (!r.ok) return;
    const d = await r.json();
    if (d.serverUrl && d.syncKey) {
      await chrome.storage.local.set({ config: { ...DEFAULTS, ...d } });
    }
  } catch (e) {
    // no defaults file: the user fills in the options page
  }
}

// Automatic sync: when a gemini.google.com page finishes loading, sync the login in the background.
// At most one successful sync per interval; after a failure, retry no sooner than RETRY_MIN.
async function autoSyncIfDue() {
  if (inFlight) return;
  const cfg = await getConfig();
  if (!ready(cfg)) return;
  const st = await getState();
  const now = Date.now();
  if (st.lastSyncAt && now - st.lastSyncAt < cfg.intervalMin * 60 * 1000) return;
  if (st.lastTryAt && now - st.lastTryAt < RETRY_MIN * 60 * 1000) return;
  inFlight = true;
  try {
    await runSync();
  } finally {
    inFlight = false;
  }
}

chrome.tabs.onUpdated.addListener((_tabId, info, tab) => {
  if (info.status !== "complete" || !tab || !tab.url) return;
  if (GEMINI_PAGE.test(tab.url)) {
    autoSyncIfDue().catch((e) => console.warn("Gemini sync:", e));
  }
});

chrome.runtime.onStartup.addListener(() => { applyDefaults(); });
chrome.runtime.onInstalled.addListener(async (d) => {
  await applyDefaults();
  if (d.reason === "install") chrome.runtime.openOptionsPage();
});

// Shared by the button and the automatic sync: runs one sync and records the outcome.
async function runSync() {
  await setState({ lastTryAt: Date.now() });
  const r = await syncGemini();
  await setState({ lastResult: { at: Date.now(), ok: r.ok, message: r.message } });
  await updateBadge();
  return r;
}

// The toolbar badge shows "!" while the last attempt failed.
async function updateBadge() {
  const s = await getState();
  const failed = !!(s.lastResult && !s.lastResult.ok);
  chrome.action.setBadgeText({ text: failed ? "!" : "" });
}

// The server's error text, when it sent a plain string. FastAPI's validation errors are lists; skip those.
function detailText(data, status) {
  return typeof data.detail === "string" ? data.detail : "Server returned HTTP " + status + ".";
}

async function syncGemini() {
  const cfg = await getConfig();
  if (!ready(cfg)) {
    return { ok: false, message: "Fill in the server URL and sync key first." };
  }
  const cookies = [];
  for (const name of COOKIES) {
    const value = await cookieValue(name);
    if (value) cookies.push({ name, value });
  }
  if (!cookies.some((c) => c.name === "__Secure-1PSID")) {
    return { ok: false, message: "No Gemini login in this browser. Log in at gemini.google.com first." };
  }
  const base = cfg.serverUrl.replace(/\/+$/, "");
  let res;
  let data = {};
  try {
    res = await fetch(base + "/api/session/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": cfg.syncKey },
      body: JSON.stringify({ cookies: JSON.stringify(cookies) }),
    });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    return { ok: false, message: "Could not reach " + base + ". Check the server URL." };
  }
  if (res.ok) {
    await setState({ lastSyncAt: Date.now() });
    return { ok: true, message: "Gemini cookies saved." };
  }
  if (res.status === 401) return { ok: false, message: "The server rejected the sync key." };
  if (res.status === 503) return { ok: false, message: "The server has no GEMINI_SYNC_KEY set yet." };
  return { ok: false, message: detailText(data, res.status) };
}

async function pingServer() {
  const cfg = await getConfig();
  if (!cfg.serverUrl) return { ok: false, message: "Set the server URL first." };
  try {
    const r = await fetch(cfg.serverUrl.replace(/\/+$/, "") + "/healthz");
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok === true) return { ok: true, message: "Server reachable." };
    return { ok: false, message: "Server answered HTTP " + r.status + ". Check the URL." };
  } catch (e) {
    return { ok: false, message: "Could not reach the server URL." };
  }
}

// What the floating panel shows: whether the sync is set up, whether Gemini is logged in here, and the last result.
async function siteStatus() {
  const cfg = await getConfig();
  const st = await getState();
  return {
    configured: ready(cfg),
    loggedIn: !!(await cookieValue("__Secure-1PSID")),
    lastSyncAt: st.lastSyncAt || null,
    lastResult: st.lastResult || null,
  };
}

// Messages from the floating panel and the options page. Each one gets exactly one reply.
function answer(promise, reply) {
  promise.then(reply, (e) => reply({ ok: false, message: "Extension error: " + (e && e.message) }));
  return true; // keep the channel open for the async reply
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg) return false;
  if (msg.type === "sync") return answer(runSync(), reply);
  if (msg.type === "ping") return answer(pingServer(), reply);
  if (msg.type === "status") return answer(siteStatus(), reply);
  if (msg.type === "openOptions") {
    chrome.runtime.openOptionsPage();
    reply({ ok: true });
  }
  return false;
});
