// Gemini Session Sync — floating panel on gemini.google.com (top page only).
// It asks the background worker for the status and starts a sync. It never reads cookies itself.
(function () {
  if (window.__geminiSyncLoaded) return; // one panel per page, even if the script is injected twice
  window.__geminiSyncLoaded = true;

  const host = document.createElement("div");
  host.id = "gemini-sync-root";
  const root = host.attachShadow({ mode: "open" }); // open, so the page's own styles cannot reach in
  root.innerHTML = `
<style>
  .fab {
    position: fixed; right: 16px; bottom: 16px; width: 44px; height: 44px; border-radius: 50%;
    border: 1px solid #5ef0ff; background: #021014; color: #5ef0ff; cursor: pointer;
    font: 700 16px/1 ui-monospace, Menlo, Consolas, monospace; box-shadow: 0 4px 16px rgba(0,0,0,.35);
    z-index: 2147483647; padding: 0;
  }
  .fab:focus-visible, button:focus-visible { outline: 2px solid #b8faff; outline-offset: 2px; }
  .panel {
    position: fixed; right: 16px; bottom: 70px; width: 280px; max-width: calc(100vw - 32px);
    box-sizing: border-box; padding: 12px; border-radius: 10px; border: 1px solid #5ef0ff;
    background: #021014; color: #d8fbff; z-index: 2147483647; box-shadow: 0 8px 28px rgba(0,0,0,.45);
    font: 13px/1.4 ui-monospace, Menlo, Consolas, monospace;
  }
  .panel[hidden] { display: none; }
  header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
  h2 { margin: 0; font-size: 12px; letter-spacing: .08em; color: #5ef0ff; }
  .x { background: none; border: 0; color: #b8faff; font-size: 18px; cursor: pointer; padding: 0 4px; }
  .state { border-top: 1px solid #1b4850; padding-top: 8px; }
  .last { font-size: 11px; opacity: .7; margin-top: 2px; }
  .sync {
    margin-top: 8px; background: #06262c; color: #5ef0ff; border: 1px solid #5ef0ff;
    border-radius: 6px; padding: 5px 10px; cursor: pointer; font: inherit; font-size: 12px;
  }
  .msg { min-height: 1.4em; margin: 6px 0 2px; font-size: 12px; }
  .msg.ok { color: #9dffc4; }
  .msg.bad { color: #ff8a8a; }
  footer { display: flex; gap: 12px; border-top: 1px solid #1b4850; padding-top: 8px; }
  .link { background: none; border: 0; color: #b8faff; cursor: pointer; font: inherit; font-size: 12px; padding: 0; text-decoration: underline; }
</style>
<button class="fab" data-role="toggle" aria-label="Gemini Session Sync" aria-expanded="false" title="Gemini Session Sync">G</button>
<section class="panel" data-role="panel" hidden aria-label="Gemini Session Sync">
  <header><h2>GEMINI // SYNC</h2><button class="x" data-role="close" aria-label="Close">×</button></header>
  <div class="state" data-role="state"></div>
  <div class="last" data-role="last"></div>
  <button class="sync" data-role="sync">SYNC GEMINI</button>
  <p class="msg" data-role="msg" aria-live="polite"></p>
  <footer>
    <button class="link" data-role="refresh">REFRESH</button>
    <button class="link" data-role="options">SETTINGS</button>
  </footer>
</section>`;
  document.documentElement.appendChild(host);

  const $ = (role) => root.querySelector(`[data-role="${role}"]`);
  const send = (msg) => chrome.runtime.sendMessage(msg);

  function setMsg(text, ok) {
    const m = $("msg");
    m.textContent = text;
    m.className = "msg " + (ok ? "ok" : "bad");
  }

  function reloadHint() {
    return "The extension was updated or reloaded. Reload this page.";
  }

  function ago(at) {
    if (!at) return "never";
    const min = Math.round((Date.now() - at) / 60000);
    if (min < 1) return "just now";
    if (min < 60) return min + " min ago";
    return Math.round(min / 60) + " h ago";
  }

  // Server and extension text is set with textContent only, never as HTML.
  function render(s) {
    s = s || {};
    $("state").textContent = !s.configured
      ? "Not set up yet. Open SETTINGS."
      : s.loggedIn
        ? "Logged in in this browser."
        : "Not logged in in this browser.";
    const r = s.lastResult;
    $("last").textContent = "Last sync: " + ago(s.lastSyncAt) + (r && !r.ok ? " · failed: " + r.message : "");
  }

  async function refresh() {
    try {
      render(await send({ type: "status" }));
    } catch (e) {
      setMsg(reloadHint(), false);
    }
  }

  async function syncNow() {
    setMsg("Syncing Gemini…", true);
    try {
      const r = await send({ type: "sync" });
      setMsg(r.message, r.ok);
    } catch (e) {
      setMsg(reloadHint(), false);
      return;
    }
    refresh();
  }

  async function openSettings() {
    try {
      await send({ type: "openOptions" });
    } catch (e) {
      setMsg(reloadHint(), false);
    }
  }

  function setOpen(open) {
    $("panel").hidden = !open;
    $("toggle").setAttribute("aria-expanded", String(open));
    if (open) refresh();
  }

  $("toggle").addEventListener("click", () => setOpen($("panel").hidden));
  $("close").addEventListener("click", () => setOpen(false));
  $("refresh").addEventListener("click", refresh);
  $("sync").addEventListener("click", syncNow);
  $("options").addEventListener("click", openSettings);
})();
