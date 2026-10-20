const $ = (id) => document.getElementById(id);

function show(message, ok) {
  $("out").textContent = message;
  $("out").className = ok ? "ok" : "bad";
}

function lastText(at, result) {
  return (at ? new Date(at).toLocaleString() : "never") +
    (result && !result.ok ? "  (last attempt failed: " + result.message + ")" : "");
}

async function load() {
  let { config = {}, state = {} } = await chrome.storage.local.get(["config", "state"]);
  if (!config.syncKey) {
    // pre-configured download: show the values it ships with (the background applies them too)
    try {
      const r = await fetch(chrome.runtime.getURL("defaults.json"));
      if (r.ok) config = { ...(await r.json()), ...config };
    } catch (e) { /* none */ }
  }
  $("serverUrl").value = config.serverUrl || "";
  $("syncKey").value = config.syncKey || "";
  $("intervalMin").value = String(config.intervalMin || 60);
  $("last").textContent = lastText(state.lastSyncAt, state.lastResult);
}

$("save").addEventListener("click", async () => {
  const config = {
    serverUrl: $("serverUrl").value.trim(),
    syncKey: $("syncKey").value.trim(),
    intervalMin: Number($("intervalMin").value) || 60,
  };
  const httpsOrLocal = (u) => !u || /^https:\/\//.test(u) || /^http:\/\/localhost/.test(u);
  if (!httpsOrLocal(config.serverUrl)) {
    show("The server URL must start with https://", false);
    return;
  }
  await chrome.storage.local.set({ config });
  show("Saved.", true);
});

$("test").addEventListener("click", async () => {
  const r = await chrome.runtime.sendMessage({ type: "ping" });
  show(r.message, r.ok);
});

$("sync").addEventListener("click", async () => {
  show("Syncing Gemini…", true);
  const r = await chrome.runtime.sendMessage({ type: "sync" });
  show(r.message, r.ok);
  load();
});

load();
