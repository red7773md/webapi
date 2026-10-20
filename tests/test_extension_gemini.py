"""The Gemini browser extension (extension/): background.js and content.js, checked offline.

background.js runs in Node with a fake chrome.* API (skipped when Node.js is not installed).
content.js runs in a real Chromium page with the extension API stubbed (skipped when Playwright or
a Chromium it can start is not installed). Nothing goes to the network.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(__file__)
EXT = os.path.join(HERE, "..", "extension")
NODE = shutil.which("node")
CHROMIUM = "/opt/pw-browsers/chromium"  # pre-installed in this sandbox; Playwright's own build otherwise

_DRIVER = r"""
const fs = require('fs'), vm = require('vm');
const BG = fs.readFileSync(process.argv[2], 'utf8');
const GEM = 'https://gemini.google.com/app/abc';
const OTHER = 'https://example.com/';
const MIN = 60 * 1000;
const flush = () => new Promise((r) => setTimeout(r, 30));

function world(o = {}) {
  const store = {};
  if (o.configured !== false) {
    store.config = {serverUrl: 'https://gem.example.test/', syncKey: 'GEMKEY', intervalMin: o.intervalMin || 60};
  }
  if (o.state) store.state = o.state;
  const jar = {'__Secure-1PSID': 'psid' in o ? o.psid : 'G-PSID', '__Secure-1PSIDTS': 'G-TS'};
  const w = {store, fetches: [], badges: [], tabListeners: [], messageListeners: [], installListeners: [], opened: 0};
  const chrome = {
    storage: {local: {
      async get(keys) {
        const out = {};
        for (const k of [].concat(keys)) if (k in store) out[k] = JSON.parse(JSON.stringify(store[k]));
        return out;
      },
      async set(obj) { for (const k of Object.keys(obj)) store[k] = JSON.parse(JSON.stringify(obj[k])); },
    }},
    cookies: {async get(d) {
      const v = jar[d.name];
      return v ? {value: v} : null;
    }},
    action: {setBadgeText(a) { w.badges.push(a.text); }},
    tabs: {onUpdated: {addListener(f) { w.tabListeners.push(f); }}},
    runtime: {
      onStartup: {addListener() {}},
      onInstalled: {addListener(f) { w.installListeners.push(f); }},
      onMessage: {addListener(f) { w.messageListeners.push(f); }},
      getURL(p) { return 'chrome-extension://id/' + p; },
      openOptionsPage() { w.opened += 1; },
    },
  };
  const status = o.serverStatus || 200;
  const body = 'serverBody' in o ? o.serverBody : {ok: true};
  const ctx = {
    chrome,
    fetch: async (url, opts) => {
      if (url.endsWith('defaults.json')) {
        if (!o.defaults) return {ok: false, status: 404, json: async () => ({})};
        return {ok: true, status: 200, json: async () => o.defaults};
      }
      w.fetches.push({url, opts});
      if (o.fetchThrows) throw new Error('offline');
      return {ok: status < 300, status, json: async () => body};
    },
    console, Date, JSON, setTimeout,
  };
  vm.createContext(ctx);
  vm.runInContext(BG, ctx);
  w.fire = async (url, status = 'complete') => {
    for (const f of w.tabListeners) f(1, {status}, {url});
    await flush();
  };
  w.send = (msg) => new Promise((resolve) => { w.messageListeners[0](msg, {}, resolve); });
  return w;
}

const out = {};
(async () => {
  let w = world(); await w.fire(OTHER);
  out.otherSite = w.fetches.length;

  w = world(); await w.fire(GEM, 'loading');
  out.loading = w.fetches.length;

  w = world(); await w.fire(GEM);
  out.firstLoad = w.fetches.length;
  out.url = w.fetches[0] && w.fetches[0].url;
  out.keyHeader = w.fetches[0] && w.fetches[0].opts.headers['X-API-Key'];
  const sent = w.fetches[0] && JSON.parse(JSON.parse(w.fetches[0].opts.body).cookies);
  out.names = sent && sent.map((c) => c.name);
  out.values = sent && sent.map((c) => c.value);
  out.firstOk = w.store.state.lastResult.ok;
  out.firstMessage = w.store.state.lastResult.message;
  out.firstBadge = w.badges[w.badges.length - 1];
  out.firstSyncSet = !!w.store.state.lastSyncAt;
  await w.fire(GEM);
  out.secondThrottled = w.fetches.length;

  w = world({state: {lastSyncAt: Date.now() - 61 * MIN}}); await w.fire(GEM);
  out.stale = w.fetches.length;
  w = world({state: {lastSyncAt: Date.now() - 5 * MIN}}); await w.fire(GEM);
  out.recent = w.fetches.length;
  w = world({intervalMin: 30, state: {lastSyncAt: Date.now() - 31 * MIN}}); await w.fire(GEM);
  out.halfHourStale = w.fetches.length;

  w = world({configured: false}); await w.fire(GEM);
  out.unconfigured = w.fetches.length;

  w = world({psid: null}); await w.fire(GEM);
  out.loggedOutFetches = w.fetches.length;
  out.loggedOutMessage = w.store.state.lastResult.message;
  out.loggedOutBadge = w.badges[w.badges.length - 1];
  const firstTry = w.store.state.lastTryAt;
  await w.fire(GEM);
  out.loggedOutRetryBlocked = w.store.state.lastTryAt === firstTry;
  w.store.state.lastTryAt = Date.now() - 11 * MIN;
  await w.fire(GEM);
  out.loggedOutRetryAfterGap = w.store.state.lastTryAt > Date.now() - 60 * 1000;

  w = world({serverStatus: 401, serverBody: {detail: 'nope'}}); await w.fire(GEM);
  out.rejected = w.store.state.lastResult.message;
  w = world({serverStatus: 503, serverBody: {detail: 'x'}}); await w.fire(GEM);
  out.noKeySet = w.store.state.lastResult.message;
  w = world({serverStatus: 422, serverBody: {detail: 'The cookie text has no __Secure-1PSID.'}}); await w.fire(GEM);
  out.badCookies = w.store.state.lastResult.message;
  w = world({serverStatus: 422, serverBody: {detail: [{msg: 'field required'}]}}); await w.fire(GEM);
  out.listDetail = w.store.state.lastResult.message;
  w = world({fetchThrows: true}); await w.fire(GEM);
  out.offline = w.store.state.lastResult.message;
  out.offlineBadge = w.badges[w.badges.length - 1];

  w = world({state: {lastSyncAt: Date.now()}});
  const reply = await w.send({type: 'sync'});
  out.manualOk = reply.ok;
  out.manualFetches = w.fetches.length;

  w = world({});
  const st = await w.send({type: 'status'});
  out.statusConfigured = st.configured;
  out.statusLoggedIn = st.loggedIn;
  w = world({psid: null});
  out.statusLoggedOut = (await w.send({type: 'status'})).loggedIn;
  w = world({configured: false});
  out.statusUnconfigured = (await w.send({type: 'status'})).configured;

  w = world({});
  const ping = await w.send({type: 'ping'});
  out.pingOk = ping.ok;
  out.pingUrl = w.fetches[0] && w.fetches[0].url;
  w = world({serverBody: {ok: false}});
  out.pingBad = (await w.send({type: 'ping'})).ok;
  w = world({configured: false});
  out.pingNoUrl = (await w.send({type: 'ping'})).message;

  w = world({configured: false, defaults: {serverUrl: 'https://d.example.test', syncKey: 'DKEY'}});
  await w.installListeners[0]({reason: 'install'});
  out.defaultsSyncKey = w.store.config && w.store.config.syncKey;
  out.defaultsOpened = w.opened;
  w = world({defaults: {serverUrl: 'https://d.example.test', syncKey: 'DKEY'}});
  await w.installListeners[0]({reason: 'update'});
  out.keepsExisting = w.store.config.syncKey;
  out.updateOpened = w.opened;

  console.log(JSON.stringify(out));
})().catch((e) => { console.error((e && e.stack) || e); process.exit(1); });
"""


@unittest.skipUnless(NODE, "node is not installed")
class GeminiBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        driver = os.path.join(cls._tmp.name, "driver.js")
        with open(driver, "w", encoding="utf-8") as fh:
            fh.write(_DRIVER)
        proc = subprocess.run(
            [NODE, driver, os.path.join(EXT, "background.js")],
            capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise AssertionError("driver failed: " + proc.stderr)
        cls.out = json.loads(proc.stdout)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_other_sites_do_not_sync(self):
        self.assertEqual(self.out["otherSite"], 0)

    def test_page_still_loading_does_not_sync(self):
        self.assertEqual(self.out["loading"], 0)

    def test_first_load_sends_both_cookies_with_the_sync_key(self):
        self.assertEqual(self.out["firstLoad"], 1)
        self.assertEqual(self.out["url"], "https://gem.example.test/api/session/sync")  # trailing slash removed
        self.assertEqual(self.out["keyHeader"], "GEMKEY")
        self.assertEqual(self.out["names"], ["__Secure-1PSID", "__Secure-1PSIDTS"])
        self.assertEqual(self.out["values"], ["G-PSID", "G-TS"])
        self.assertTrue(self.out["firstOk"])
        self.assertEqual(self.out["firstMessage"], "Gemini cookies saved.")
        self.assertEqual(self.out["firstBadge"], "")
        self.assertTrue(self.out["firstSyncSet"])

    def test_second_load_inside_the_interval_is_throttled(self):
        self.assertEqual(self.out["secondThrottled"], 1)

    def test_sync_runs_again_after_the_interval(self):
        self.assertEqual(self.out["stale"], 1)

    def test_recent_sync_is_not_repeated(self):
        self.assertEqual(self.out["recent"], 0)

    def test_the_chosen_interval_is_used(self):
        self.assertEqual(self.out["halfHourStale"], 1)

    def test_unconfigured_extension_does_nothing(self):
        self.assertEqual(self.out["unconfigured"], 0)

    def test_logged_out_failure_is_recorded_and_retry_is_delayed(self):
        self.assertEqual(self.out["loggedOutFetches"], 0)
        self.assertIn("gemini.google.com", self.out["loggedOutMessage"])
        self.assertEqual(self.out["loggedOutBadge"], "!")
        self.assertTrue(self.out["loggedOutRetryBlocked"])
        self.assertTrue(self.out["loggedOutRetryAfterGap"])

    def test_server_errors_are_explained(self):
        self.assertEqual(self.out["rejected"], "The server rejected the sync key.")
        self.assertEqual(self.out["noKeySet"], "The server has no GEMINI_SYNC_KEY set yet.")
        self.assertEqual(self.out["badCookies"], "The cookie text has no __Secure-1PSID.")
        # FastAPI validation errors are lists; they must not be shown as text
        self.assertEqual(self.out["listDetail"], "Server returned HTTP 422.")

    def test_unreachable_server_is_reported(self):
        self.assertIn("Could not reach", self.out["offline"])
        self.assertEqual(self.out["offlineBadge"], "!")

    def test_manual_sync_ignores_the_interval(self):
        self.assertTrue(self.out["manualOk"])
        self.assertEqual(self.out["manualFetches"], 1)

    def test_status_reports_setup_and_login(self):
        self.assertTrue(self.out["statusConfigured"])
        self.assertTrue(self.out["statusLoggedIn"])
        self.assertFalse(self.out["statusLoggedOut"])
        self.assertFalse(self.out["statusUnconfigured"])

    def test_ping_checks_the_health_endpoint(self):
        self.assertTrue(self.out["pingOk"])
        self.assertEqual(self.out["pingUrl"], "https://gem.example.test/healthz")
        self.assertFalse(self.out["pingBad"])
        self.assertEqual(self.out["pingNoUrl"], "Set the server URL first.")

    def test_defaults_file_is_applied_on_install_only_when_unset(self):
        self.assertEqual(self.out["defaultsSyncKey"], "DKEY")
        self.assertEqual(self.out["defaultsOpened"], 1)
        self.assertEqual(self.out["keepsExisting"], "GEMKEY")
        self.assertEqual(self.out["updateOpened"], 0)


PANEL_STUB = """
window.__sent = [];
window.__stub = {
  status: {configured: true, loggedIn: true, lastSyncAt: Date.now() - 5 * 60000, lastResult: {ok: true, message: 'Gemini cookies saved.'}},
  syncReply: {ok: true, message: 'Gemini cookies saved.'},
  throwNow: false,
};
window.chrome = {runtime: {
  sendMessage(msg) {
    if (window.__stub.throwNow) throw new Error('Extension context invalidated.');
    window.__sent.push(msg);
    if (msg.type === 'status') return Promise.resolve(window.__stub.status);
    if (msg.type === 'sync') return Promise.resolve(window.__stub.syncReply);
    return Promise.resolve({});
  },
}};
"""

CONTENT = os.path.join(EXT, "content.js")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None


def shadow(selector):
    return f"() => document.getElementById('gemini-sync-root').shadowRoot.querySelector({selector!r})"


@unittest.skipUnless(sync_playwright is not None, "playwright is not installed")
class FloatingPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM if os.path.exists(CHROMIUM) else None)
        except Exception as exc:  # no browser on this machine
            cls._pw.stop()
            raise unittest.SkipTest(f"no Chromium available: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def open_page(self, url="https://gemini.google.com/app/abc"):
        """A blank page that claims to be the Gemini site, with the panel script loaded once."""
        ctx = self.browser.new_context()
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.add_init_script(PANEL_STUB)
        page.route(url, lambda route: route.fulfill(body="<html><body><h1>page</h1></body></html>", content_type="text/html"))
        page.goto(url)
        page.add_script_tag(path=CONTENT)
        return page

    def open_panel(self, page):
        page.evaluate(shadow("[data-role=toggle]") + ".click()")
        page.wait_for_function(
            "() => document.getElementById('gemini-sync-root').shadowRoot.querySelector('[data-role=state]').textContent.length > 0"
        )

    def test_one_floating_button_is_added_and_it_opens_the_panel(self):
        page = self.open_page()
        self.assertEqual(page.evaluate("() => document.querySelectorAll('#gemini-sync-root').length"), 1)
        self.assertTrue(page.evaluate(shadow("[data-role=panel]") + ".hidden"))
        self.open_panel(page)
        self.assertFalse(page.evaluate(shadow("[data-role=panel]") + ".hidden"))
        self.assertIn({"type": "status"}, page.evaluate("() => window.__sent"))

    def test_the_panel_shows_whether_gemini_is_logged_in_and_the_last_sync(self):
        page = self.open_page()
        self.open_panel(page)
        text = page.evaluate(shadow("[data-role=state]") + ".textContent")
        self.assertEqual(text, "Logged in in this browser.")
        self.assertIn("Last sync: 5 min ago", page.evaluate(shadow("[data-role=last]") + ".textContent"))

    def test_logged_out_and_unset_states_are_explained(self):
        page = self.open_page()
        page.evaluate("() => { window.__stub.status.loggedIn = false; }")
        self.open_panel(page)
        self.assertEqual(page.evaluate(shadow("[data-role=state]") + ".textContent"), "Not logged in in this browser.")

        page = self.open_page()
        page.evaluate("() => { window.__stub.status = {configured: false, loggedIn: false, lastSyncAt: null, lastResult: null}; }")
        self.open_panel(page)
        self.assertEqual(page.evaluate(shadow("[data-role=state]") + ".textContent"), "Not set up yet. Open SETTINGS.")

    def test_the_sync_button_sends_a_sync_and_shows_the_result(self):
        page = self.open_page()
        self.open_panel(page)
        page.evaluate(shadow("[data-role=sync]") + ".click()")
        page.wait_for_function(
            "() => document.getElementById('gemini-sync-root').shadowRoot.querySelector('[data-role=msg]').textContent === 'Gemini cookies saved.'"
        )
        self.assertIn({"type": "sync"}, page.evaluate("() => window.__sent"))
        self.assertIn("ok", page.evaluate(shadow("[data-role=msg]") + ".className"))

    def test_loading_the_script_twice_still_gives_one_button(self):
        page = self.open_page()
        page.add_script_tag(path=CONTENT)
        self.assertEqual(page.evaluate("() => document.querySelectorAll('#gemini-sync-root').length"), 1)

    def test_a_stale_extension_asks_for_a_reload(self):
        page = self.open_page()
        page.evaluate("() => { window.__stub.throwNow = true; }")
        page.evaluate(shadow("[data-role=toggle]") + ".click()")
        page.wait_for_function(
            "() => document.getElementById('gemini-sync-root').shadowRoot.querySelector('[data-role=msg]').textContent.includes('Reload this page')"
        )
        self.assertIn("Reload this page", page.evaluate(shadow("[data-role=msg]") + ".textContent"))

    def test_server_text_is_never_read_as_html(self):
        page = self.open_page()
        page.evaluate("() => { window.__stub.status.lastResult = {ok: false, message: '<img src=x id=evil>'}; }")
        self.open_panel(page)
        self.assertEqual(page.evaluate("() => document.getElementById('gemini-sync-root').shadowRoot.querySelectorAll('img').length"), 0)
        self.assertIn("<img src=x id=evil>", page.evaluate(shadow("[data-role=last]") + ".textContent"))


if __name__ == "__main__":
    unittest.main()
