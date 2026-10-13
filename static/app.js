/* GEMINI // ARCHIVE TERMINAL — front end.
   No build step. All server-provided text is inserted with textContent,
   never innerHTML. */

(() => {
  "use strict";

  const $app = document.getElementById("app");
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const MODULES = [
    { key: "session", label: "SESSION", fkey: "F1", step: 1 },
    { key: "chat", label: "CHAT", fkey: "F2", step: 2 },
    { key: "image", label: "IMAGE", fkey: "F3", step: 3 },
    { key: "research", label: "RESEARCH", fkey: "F4", step: 4 },
  ];

  const BOOT_LINES = [
    "GEMINI//ARCHIVE TERMINAL  rev 0.1",
    "tape deck ............................ ok",
    "phosphor coating ..................... ok",
    `archive node ......................... ${location.host}`,
    "awaiting operator credentials_",
  ];

  let clockTimer = null;

  // ----- helpers -------------------------------------------------------------

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  async function api(path, options = {}) {
    const init = { credentials: "same-origin", method: options.method || "GET", headers: {} };
    if (options.body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.body);
    }
    const res = await fetch(path, init);
    let data = null;
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
    if (!res.ok) {
      const detail = data && (data.detail || data.message);
      throw new ApiError(res.status, typeof detail === "string" ? detail : `HTTP ${res.status}`);
    }
    return data;
  }

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2), value);
      } else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const pad = (n) => String(n).padStart(2, "0");
  const stamp = () => new Date().toTimeString().slice(0, 8);

  function startClock(node) {
    const tick = () => {
      const d = new Date();
      node.textContent =
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    };
    tick();
    return setInterval(tick, 1000);
  }

  function panelTitle(text) {
    return el("h2", { class: "panel-title", text });
  }

  function isAuthError(err) {
    return err instanceof ApiError && err.status === 401;
  }

  // ----- boot and login ------------------------------------------------------

  async function init() {
    const bootPre = el("pre", { class: "boot" });
    $app.replaceChildren(bootPre);

    for (const line of BOOT_LINES) {
      bootPre.append(line + "\n");
      if (!REDUCED_MOTION) await sleep(160);
    }

    let me = { authenticated: false };
    try {
      me = await api("/api/auth/me");
    } catch (_) {
      me = { authenticated: false };
    }

    if (me.authenticated) {
      renderShell(me.username);
    } else {
      renderLogin(bootPre);
    }
  }

  function renderLogin(bootPre, message) {
    const userInput = el("input", {
      class: "field",
      id: "login-user",
      name: "username",
      autocomplete: "username",
      maxlength: 200,
      required: true,
    });
    const passInput = el("input", {
      class: "field",
      id: "login-pass",
      name: "password",
      type: "password",
      autocomplete: "current-password",
      maxlength: 500,
      required: true,
    });
    const status = el("div", { class: "status", role: "status", "aria-live": "polite" });
    const submit = el("button", { class: "btn", type: "submit", text: "[ AUTHENTICATE ]" });

    const setStatus = (text, cls = "") => {
      status.className = `status ${cls}`.trim();
      status.textContent = text;
    };

    const onSubmit = async (event) => {
      event.preventDefault();
      submit.disabled = true;
      setStatus("authenticating...", "dim");
      try {
        const result = await api("/api/auth/login", {
          method: "POST",
          body: { username: userInput.value, password: passInput.value },
        });
        passInput.value = "";
        renderShell(result.username);
      } catch (err) {
        setStatus(err.message, "bad");
        passInput.value = "";
        passInput.focus();
        submit.disabled = false;
      }
    };

    const form = el(
      "form",
      { class: "login-form", onsubmit: onSubmit, autocomplete: "on" },
      el("div", { class: "row" }, el("label", { class: "dim", for: "login-user", text: "USER" }), userInput),
      el("div", { class: "row" }, el("label", { class: "dim", for: "login-pass", text: "PASS" }), passInput),
      submit,
      status,
    );

    const frame = el(
      "section",
      { class: "frame login" },
      panelTitle("archive access"),
      el("p", { class: "hint", text: "Operator login. Credentials are set on the server, not in this page." }),
      form,
    );

    if (message) setStatus(message, "warn");
    $app.replaceChildren(...(bootPre ? [bootPre] : []), frame);
    userInput.focus();
  }

  // ----- shell ---------------------------------------------------------------

  function renderShell(username) {
    clearInterval(clockTimer);

    const clock = el("span", { class: "clock" });
    clockTimer = startClock(clock);

    const nav = el(
      "nav",
      { class: "nav", "aria-label": "Modules" },
      MODULES.map((m) => el("a", { href: `#/${m.key}`, "data-key": m.key, title: m.fkey }, `[${m.fkey}] ${m.label}`)),
      el("button", { class: "btn danger", type: "button", onclick: logout, text: "[ LOGOUT ]" }),
    );

    const header = el(
      "header",
      { class: "frame head" },
      el(
        "div",
        { class: "head-row" },
        el("span", { class: "brand", text: "GEMINI//ARCHIVE" }),
        el("span", { class: "rec", text: "● REC" }),
        clock,
      ),
      el("div", { class: "head-row dim", text: `operator: ${username}  ·  node: ${location.host}` }),
      nav,
    );

    const view = el("main", { id: "view", class: "frame", tabindex: -1 });
    $app.replaceChildren(header, view);
    route();
  }

  function currentKey() {
    const key = location.hash.replace(/^#\/?/, "");
    return MODULES.some((m) => m.key === key) ? key : "session";
  }

  function route() {
    const view = document.getElementById("view");
    if (!view) return;
    const key = currentKey();

    document.querySelectorAll(".nav a").forEach((a) => {
      if (a.dataset.key === key) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });

    const mod = MODULES.find((m) => m.key === key);
    if (key === "session") sessionView(view);
    else if (key === "chat") chatView(view);
    else offlineView(view, mod);
  }

  function offlineView(view, mod) {
    view.replaceChildren(
      panelTitle(`${mod.label} // module offline`),
      el("p", { text: `This module is build step ${mod.step} and is not installed yet.` }),
      el("p", { class: "dim", text: "Verify the SESSION uplink first; this module will use it." }),
    );
  }

  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch (_) {
      /* ignore: we are leaving either way */
    }
    clearInterval(clockTimer);
    renderLogin(undefined, "session closed.");
  }

  // ----- SESSION module ------------------------------------------------------

  function sessionView(view) {
    const statusBox = el("div", { class: "kv", "aria-live": "polite" });
    const logBox = el("div", { class: "log", "aria-live": "polite" });
    const textarea = el("textarea", {
      class: "field",
      id: "cookie-input",
      rows: 6,
      spellcheck: false,
      autocomplete: "off",
      autocapitalize: "off",
      maxlength: 20000,
      placeholder: "__Secure-1PSID=...; __Secure-1PSIDTS=...   (or JSON, or just the PSID value)",
    });

    const buttons = [];
    const busy = (on) => buttons.forEach((b) => (b.disabled = on));

    const log = (text, cls = "dim") => {
      logBox.append(el("div", { class: cls, text: `${stamp()}  ${text}` }));
      logBox.scrollTop = logBox.scrollHeight;
    };

    const draw = (st) => {
      const rows = [
        ["STORAGE", st.storage || "—", ""],
        ["LINK", st.configured ? "cookies on file" : "no cookies", st.configured ? "ok" : "warn"],
        ["PSID", st.psid_hint || "—", ""],
        ["PSIDTS", st.has_psidts ? "present" : "absent", st.has_psidts ? "ok" : "warn"],
        ["SAVED", st.saved_at || "—", ""],
        ["UPLINK", st.verified ? `verified ${st.verified_at}` : "not verified", st.verified ? "ok" : "warn"],
        ["MODELS", st.models && st.models.length ? st.models.map((m) => m.display_name || m.name).join(", ") : "—", ""],
      ];
      if (st.last_error) rows.push(["ERROR", st.last_error, "bad"]);
      statusBox.replaceChildren(
        ...rows.flatMap(([k, v, cls]) => [el("div", { class: "k", text: k }), el("div", { class: `v ${cls}`, text: v })]),
      );
    };

    const failWith = (err) => {
      if (isAuthError(err)) {
        renderLogin(undefined, "session expired. authenticate again.");
        return;
      }
      log(err.message, "bad");
    };

    const verify = async () => {
      busy(true);
      log("verifying uplink (can take up to a minute)...");
      try {
        const st = await api("/api/session/verify", { method: "POST" });
        draw(st);
        if (st.verified) log(`uplink ok. ${st.models.length} model(s) visible.`, "ok");
        else log(st.last_error || "verification failed.", "bad");
      } catch (err) {
        failWith(err);
      } finally {
        busy(false);
      }
    };

    const save = async () => {
      if (!textarea.value.trim()) {
        log("nothing to save. paste the cookie values first.", "warn");
        return;
      }
      busy(true);
      try {
        const st = await api("/api/session/cookies", {
          method: "POST",
          body: { cookies: textarea.value },
        });
        textarea.value = "";
        draw(st);
        log("cookies stored. values are not shown again.", "ok");
        busy(false);
        await verify();
      } catch (err) {
        failWith(err);
      } finally {
        busy(false);
      }
    };

    const purge = async () => {
      if (!window.confirm("Purge the saved Gemini cookies?")) return;
      busy(true);
      try {
        const st = await api("/api/session/cookies", { method: "DELETE" });
        draw(st);
        log("cookies purged.", "warn");
      } catch (err) {
        failWith(err);
      } finally {
        busy(false);
      }
    };

    const verifyBtn = el("button", { class: "btn", type: "button", onclick: verify, text: "[ VERIFY UPLINK ]" });
    const saveBtn = el("button", { class: "btn", type: "button", onclick: save, text: "[ SAVE COOKIES ]" });
    const purgeBtn = el("button", { class: "btn danger", type: "button", onclick: purge, text: "[ PURGE COOKIES ]" });
    buttons.push(verifyBtn, saveBtn, purgeBtn);

    view.replaceChildren(
      panelTitle("session // gemini uplink"),
      statusBox,
      el("div", { class: "actions" }, verifyBtn, purgeBtn),
      el("hr", { class: "dim" }),
      panelTitle("import cookies from browser"),
      el(
        "p",
        { class: "hint" },
        "Sign in to gemini.google.com with the secondary account. Open DevTools (F12). ",
        "Chrome: Application > Cookies > https://gemini.google.com. ",
        "Firefox: Storage > Cookies. Copy __Secure-1PSID and __Secure-1PSIDTS, ",
        "or copy the ",
        el("code", { text: "cookie" }),
        " request header from any request in the Network tab.",
      ),
      el(
        "p",
        { class: "hint" },
        "Accepted: the cookie header, a JSON object, a cookie-extension JSON export, or the bare PSID value. ",
        "Chromium cookies can expire within hours; Firefox tends to last longer.",
      ),
      textarea,
      el("div", { class: "actions" }, saveBtn),
      logBox,
    );

    // Load current state.
    busy(true);
    api("/api/session")
      .then((st) => {
        draw(st);
        log("uplink panel ready.");
      })
      .catch(failWith)
      .finally(() => busy(false));
  }

  // ----- CHAT module ---------------------------------------------------------

  const CHAT_MAX_FILES = 4;
  const CHAT_MAX_FILE_BYTES = 3 * 1024 * 1024;

  function readFileBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const url = String(reader.result || "");
        resolve(url.slice(url.indexOf(",") + 1));
      };
      reader.onerror = () => reject(new Error(`could not read ${file.name}.`));
      reader.readAsDataURL(file);
    });
  }

  const shortTime = (iso) => (iso || "").slice(0, 16).replace("T", " ");

  function chatView(view) {
    let chatId = null;
    let chats = [];
    let queued = [];

    const listBox = el("ul", { class: "chat-list", "aria-label": "Saved chats" });
    const transcript = el("div", { class: "transcript", "aria-live": "polite" });
    const notice = el("div", { class: "status", role: "status", "aria-live": "polite" });
    const input = el("textarea", {
      class: "field composer",
      id: "chat-input",
      rows: 4,
      maxlength: 8000,
      placeholder: "Message Gemini.  Ctrl+Enter to send.",
    });
    const modelSelect = el(
      "select",
      { class: "field select", id: "chat-model", "aria-label": "Model" },
      el("option", { value: "", text: "default model" }),
    );
    const fileInput = el("input", {
      class: "file",
      id: "chat-files",
      type: "file",
      multiple: true,
      "aria-label": "Attach files",
    });
    const fileHint = el("div", { class: "hint" });
    const sendBtn = el("button", { class: "btn", type: "button", onclick: () => send(), text: "[ SEND ]" });
    const newBtn = el("button", { class: "btn", type: "button", onclick: () => newChat(), text: "[ NEW CHAT ]" });
    const deleteBtn = el("button", {
      class: "btn danger",
      type: "button",
      onclick: () => remove(),
      text: "[ DELETE CHAT ]",
    });
    const controls = [sendBtn, newBtn, input, modelSelect, fileInput];

    const setBusy = (on) => {
      controls.forEach((c) => (c.disabled = on));
      deleteBtn.disabled = on || chatId === null;
    };

    const setNotice = (text, cls = "") => {
      notice.className = `status ${cls}`.trim();
      notice.textContent = text;
    };

    const fail = (err) => {
      if (isAuthError(err)) {
        renderLogin(undefined, "session expired. authenticate again.");
        return;
      }
      setNotice(err.message, "bad");
    };

    const messageNode = (m) =>
      el(
        "div",
        { class: `msg ${m.role === "user" ? "user" : "model"}` },
        el("div", { class: "who", text: m.role === "user" ? "YOU" : "GEMINI" }),
        el("pre", { class: "body", text: m.text || "(no text in this reply)" }),
        m.files && m.files.length
          ? el("div", { class: "hint", text: `files: ${m.files.map((f) => f.name).join(", ")}` })
          : null,
      );

    const showTranscript = (messages) => {
      transcript.replaceChildren(...messages.map(messageNode));
      if (!messages.length) transcript.append(el("p", { class: "dim", text: "no messages yet. say something." }));
      transcript.scrollTop = transcript.scrollHeight;
    };

    const appendMessages = (messages) => {
      transcript.querySelector("p.dim")?.remove();
      for (const m of messages) transcript.append(messageNode(m));
      transcript.scrollTop = transcript.scrollHeight;
    };

    const drawList = () => {
      if (!chats.length) {
        listBox.replaceChildren(el("li", { class: "dim", text: "no saved chats yet" }));
        return;
      }
      listBox.replaceChildren(
        ...chats.map((c) =>
          el(
            "li",
            null,
            el("button", {
              class: `chat-item${c.id === chatId ? " current" : ""}`,
              type: "button",
              onclick: () => openChat(c.id),
              text: c.title || "untitled",
            }),
            el("div", { class: "dim", text: `${shortTime(c.updated_at)} · ${c.message_count} msgs` }),
          ),
        ),
      );
    };

    const renderQueued = () => {
      fileHint.textContent = queued.length
        ? `attached: ${queued.map((f) => f.name).join(", ")}`
        : `no files attached. up to ${CHAT_MAX_FILES}, 3 MB each.`;
    };

    const loadList = async () => {
      const res = await api("/api/chat");
      chats = res.chats || [];
      drawList();
    };

    const loadModels = async () => {
      const st = await api("/api/session");
      for (const m of st.models || []) {
        if (m.available === false) continue;
        modelSelect.append(el("option", { value: m.name, text: m.display_name || m.name }));
      }
    };

    const openChat = async (id) => {
      setBusy(true);
      try {
        const chat = await api(`/api/chat/${id}`);
        chatId = chat.id;
        queued = [];
        fileInput.value = "";
        renderQueued();
        showTranscript(chat.messages || []);
        setNotice(`opened "${chat.title || "untitled"}".`, "dim");
        drawList();
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    };

    function newChat() {
      chatId = null;
      queued = [];
      fileInput.value = "";
      renderQueued();
      showTranscript([]);
      drawList();
      setNotice("new conversation. the first message names it.", "dim");
      setBusy(false);
      input.focus();
    }

    const send = async () => {
      const text = input.value.trim();
      if (!text) {
        setNotice("type a message first.", "warn");
        return;
      }
      setBusy(true);
      setNotice("awaiting gemini... (can take up to a minute)", "dim");
      try {
        const files = [];
        for (const f of queued) files.push({ name: f.name, data: await readFileBase64(f) });
        const res = await api("/api/chat/send", {
          method: "POST",
          body: { chat_id: chatId, message: text, model: modelSelect.value || null, files },
        });
        chatId = res.chat.id;
        input.value = "";
        queued = [];
        fileInput.value = "";
        renderQueued();
        appendMessages(res.messages);
        await loadList();
        setNotice(`reply received. ${res.chat.message_count} messages in this chat.`, "ok");
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    };

    const remove = async () => {
      if (chatId === null) return;
      if (!window.confirm("Delete this chat from the archive? Gemini's own history is not touched.")) return;
      setBusy(true);
      try {
        await api(`/api/chat/${chatId}`, { method: "DELETE" });
        newChat();
        await loadList();
        setNotice("chat deleted from the archive.", "warn");
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    };

    fileInput.addEventListener("change", () => {
      const picked = Array.from(fileInput.files || []);
      const big = picked.filter((f) => f.size > CHAT_MAX_FILE_BYTES);
      queued = picked.slice(0, CHAT_MAX_FILES).filter((f) => f.size <= CHAT_MAX_FILE_BYTES);
      if (picked.length > CHAT_MAX_FILES) setNotice(`only the first ${CHAT_MAX_FILES} files are attached.`, "warn");
      if (big.length) setNotice(`over 3 MB, not attached: ${big.map((f) => f.name).join(", ")}`, "bad");
      renderQueued();
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        send();
      }
    });

    view.replaceChildren(
      panelTitle("chat // gemini"),
      el(
        "div",
        { class: "chat-layout" },
        el(
          "aside",
          { class: "chat-side" },
          el("div", { class: "actions" }, newBtn),
          el("div", { class: "hint", text: "saved chats" }),
          listBox,
        ),
        el(
          "section",
          { class: "chat-main" },
          transcript,
          notice,
          input,
          el("div", { class: "composer-row" }, modelSelect, deleteBtn, sendBtn),
          el("div", { class: "composer-row" }, fileInput),
          fileHint,
        ),
      ),
    );

    newChat();
    setBusy(true);
    Promise.all([loadList(), loadModels()])
      .catch(fail)
      .finally(() => setBusy(false));
  }

  // ----- keyboard and routing -----------------------------------------------

  document.addEventListener("keydown", (event) => {
    if (!/^F[1-4]$/.test(event.key) || !document.getElementById("view")) return;
    event.preventDefault();
    const mod = MODULES[Number(event.key.slice(1)) - 1];
    location.hash = `#/${mod.key}`;
  });

  window.addEventListener("hashchange", () => {
    if (document.getElementById("view")) route();
  });

  init();
})();
