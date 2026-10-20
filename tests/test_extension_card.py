"""The admin page's extension card (static/app.js), run in Chromium against the real app on a local port.

Gemini is a fake client, and nothing leaves this machine. Skipped when Playwright or a Chromium it
can start is not installed.
"""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
import zipfile

from app.config import Settings
from app.main import create_app
from tests.test_chat import ADMIN_PASS, ADMIN_USER, Backend, FakeClient, MemoryStore

APP_KEY = "k3y-for-scripts-0123456789abcdef"
SYNC_KEY = "sync-key-for-the-extension-0123456789"
CHROMIUM = "/opt/pw-browsers/chromium"  # pre-installed in this sandbox; Playwright's own build otherwise

try:
    import uvicorn
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    uvicorn = None
    sync_playwright = None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """The real app on 127.0.0.1, in a thread, for the duration of a test class."""

    def __init__(self, sync_key: str | None, data_dir: str):
        settings = Settings(
            admin_username=ADMIN_USER,
            admin_password=ADMIN_PASS,
            secret_key="s" * 48,
            cookie_secure=False,
            data_dir=data_dir,
            api_key=APP_KEY,
            sync_key=sync_key,
        )
        backend = Backend()
        app = create_app(settings, gemini_factory=lambda psid, psidts: FakeClient(backend, psid, psidts), store=MemoryStore())
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.time() + 30
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("the test server did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@unittest.skipUnless(sync_playwright is not None, "playwright is not installed")
class ExtensionCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.with_key = LiveServer(SYNC_KEY, cls._tmp.name)
        cls.without_key = LiveServer(None, cls._tmp.name)
        cls.with_key.start()
        cls.without_key.start()
        cls._pw = sync_playwright().start()
        try:
            cls.browser = cls._pw.chromium.launch(executable_path=CHROMIUM)
        except Exception as exc:  # no browser on this machine
            cls.with_key.stop()
            cls.without_key.stop()
            cls._pw.stop()
            cls._tmp.cleanup()
            raise unittest.SkipTest(f"no Chromium available: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()
        cls.with_key.stop()
        cls.without_key.stop()
        cls._tmp.cleanup()

    def sign_in(self, server: LiveServer):
        ctx = self.browser.new_context(accept_downloads=True)
        self.addCleanup(ctx.close)
        page = ctx.new_page()
        page.goto(server.url + "/")
        page.fill("#login-user", ADMIN_USER)
        page.fill("#login-pass", ADMIN_PASS)
        page.locator("button[type=submit]").click()
        page.wait_for_selector("button:has-text('DOWNLOAD EXTENSION')")
        return page

    def test_the_card_offers_a_download_that_is_configured_with_this_server(self):
        page = self.sign_in(self.with_key)
        note = page.locator("p.hint", has_text="already holds this server's address")
        note.wait_for()
        button = page.locator("button", has_text="DOWNLOAD EXTENSION")
        self.assertTrue(button.is_enabled())

        with page.expect_download() as info:
            button.click()
        download = info.value
        self.assertEqual(download.suggested_filename, "gemini-session-sync.zip")
        with zipfile.ZipFile(download.path()) as z:
            config = json.loads(z.read("defaults.json"))
        self.assertEqual(config, {"serverUrl": self.with_key.url, "syncKey": SYNC_KEY})
        page.locator("div.log", has_text="extension downloaded").wait_for()

    def test_without_a_sync_key_the_card_says_what_to_set_and_stays_off(self):
        page = self.sign_in(self.without_key)
        page.locator("p.hint", has_text="Set GEMINI_SYNC_KEY").wait_for()
        self.assertTrue(page.locator("button", has_text="DOWNLOAD EXTENSION").is_disabled())


if __name__ == "__main__":
    unittest.main()
