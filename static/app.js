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
        renderShell(result.username, true);
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

  function renderShell(username, powerOn = false) {
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
        clock,
      ),
      el("div", { class: "head-row dim", text: `operator: ${username}  ·  node: ${location.host}` }),
      nav,
    );

    const view = el("main", { id: "view", class: "frame", tabindex: -1 });
    $app.replaceChildren(header, view);
    if (powerOn) {
      // One power-on flicker after a successful login. The class is removed when it has played.
      $app.classList.add("power-on");
      setTimeout(() => $app.classList.remove("power-on"), 1200);
    }
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
    else if (key === "image") imageView(view);
    else if (key === "research") researchView(view);
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
      // The import box is for a first save, a replacement after a failed check, or a fresh start after purge.
      // A saved cookie that verified hides it, so the box never sits beside a working login.
      const working = Boolean(st.configured && st.verified);
      importArea.hidden = working;
      importTitle.textContent = st.configured ? "replace the saved cookies" : "import cookies from browser";
      purgeBtn.hidden = !st.configured;
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

    const importTitle = panelTitle("import cookies from browser");
    const importArea = el(
      "div",
      null,
      el("hr", { class: "dim" }),
      importTitle,
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
    );

    view.replaceChildren(
      panelTitle("session // gemini uplink"),
      statusBox,
      el("div", { class: "actions" }, verifyBtn, purgeBtn),
      importArea,
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
    const controls = [sendBtn, input, modelSelect, fileInput];
    // True while Gemini is still writing the reply to the chat on screen.
    let awaitingReply = false;
    let replyTimer = null;

    const setBusy = (on) => {
      controls.forEach((c) => (c.disabled = on || awaitingReply));
      newBtn.disabled = on;
      deleteBtn.disabled = on || awaitingReply || chatId === null;
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
            el("div", {
              class: "dim",
              text: `${shortTime(c.updated_at)} · ${c.message_count} msgs${
                c.status === "waiting" ? " · writing" : c.status === "failed" ? " · no reply" : ""
              }`,
            }),
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

    const loadModels = () => fillModelSelect(modelSelect);

    const openChat = async (id) => {
      stopWatching();
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
        if (chat.status === "waiting") watchReply(chat.id);
        else if (chat.status === "failed") setNotice(chat.pending?.error || "gemini did not reply.", "bad");
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    };

    function newChat() {
      stopWatching();
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

    const CHAT_POLL_MS = 20000;

    const stopWatching = () => {
      clearTimeout(replyTimer);
      replyTimer = null;
      awaitingReply = false;
    };

    // Waits for the reply to a chat id. Each check asks the server, which asks Gemini
    // no more often than its own gap allows.
    const watchReply = (id) => {
      clearTimeout(replyTimer);
      awaitingReply = true;
      setBusy(false);
      transcript.querySelector(".pending-line")?.remove();
      transcript.append(el("p", { class: "dim pending-line", text: "gemini is writing the reply..." }));
      transcript.scrollTop = transcript.scrollHeight;
      setNotice("gemini is still writing. this page checks every 20 seconds. you can leave and come back.", "dim");
      replyTimer = setTimeout(() => checkReply(id), CHAT_POLL_MS);
    };

    const checkReply = async (id) => {
      replyTimer = null;
      if (id !== chatId) return; // another chat is open now
      try {
        const chat = await api(`/api/chat/${id}`);
        if (id !== chatId) return;
        if (chat.status === "waiting") {
          watchReply(id);
          return;
        }
        stopWatching();
        showTranscript(chat.messages || []);
        if (chat.status === "failed") setNotice(chat.pending?.error || "gemini did not reply.", "bad");
        else setNotice("reply received.", "ok");
        setBusy(false);
        await loadList();
      } catch (err) {
        if (id !== chatId) return;
        stopWatching();
        setBusy(false);
        fail(err);
      }
    };

    const send = async () => {
      if (awaitingReply) {
        setNotice("gemini is still answering this chat. wait for the reply.", "warn");
        return;
      }
      const text = input.value.trim();
      if (!text) {
        setNotice("type a message first.", "warn");
        return;
      }
      setBusy(true);
      setNotice("sending to gemini...", "dim");
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
        if (res.status === "waiting") watchReply(res.chat.id);
        else if (res.status === "failed")
          setNotice("gemini did not confirm the message in time. the chat is saved; send the message again.", "bad");
        else setNotice(`reply received. ${res.chat.message_count} messages in this chat.`, "ok");
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

  // ----- IMAGE module --------------------------------------------------------

  async function fillModelSelect(select) {
    const st = await api("/api/session");
    for (const m of st.models || []) {
      if (m.available === false) continue;
      select.append(el("option", { value: m.name, text: m.display_name || m.name }));
    }
  }

  function imageView(view) {
    const cache = new Map(); // image id -> object URL, released when the screen is left
    const viewer = el("div", { class: "viewer" });
    const grid = el("div", { class: "gallery", "aria-live": "polite" });
    const notice = el("div", { class: "status", role: "status", "aria-live": "polite" });
    const prompt = el("textarea", {
      class: "field composer",
      id: "image-prompt",
      rows: 3,
      maxlength: 1000,
      placeholder: "Describe an image.  Ctrl+Enter to generate.",
    });
    const modelSelect = el(
      "select",
      { class: "field select", id: "image-model", "aria-label": "Model" },
      el("option", { value: "", text: "default model" }),
    );
    const generateBtn = el("button", { class: "btn", type: "button", onclick: () => generate(), text: "[ GENERATE ]" });
    const controls = [prompt, modelSelect, generateBtn];

    const setBusy = (on) => controls.forEach((c) => (c.disabled = on));
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

    const release = () => {
      for (const url of cache.values()) URL.revokeObjectURL(url);
      cache.clear();
    };
    window.addEventListener("hashchange", release, { once: true });

    // Images are private, so they are fetched with the session cookie and shown as blob: URLs.
    const urlFor = async (id) => {
      if (!cache.has(id)) {
        const res = await fetch(`/api/image/${id}`, { credentials: "same-origin" });
        if (res.status === 401) throw new ApiError(401, "Login required.");
        if (!res.ok) throw new ApiError(res.status, `could not load image (HTTP ${res.status}).`);
        cache.set(id, URL.createObjectURL(await res.blob()));
      }
      return cache.get(id);
    };

    const showFull = async (img) => {
      try {
        const url = await urlFor(img.id);
        viewer.replaceChildren(
          el("img", { src: url, alt: img.prompt }),
          el("p", { class: "hint", text: `prompt: ${img.prompt}` }),
          el("div", { class: "actions" },
            el("a", { class: "btn", href: url, download: `gemini-image-${img.id}.${img.mime.split("/")[1]}`, text: "[ DOWNLOAD ]" }),
            el("button", { class: "btn", type: "button", onclick: () => viewer.replaceChildren(), text: "[ CLOSE ]" }),
          ),
        );
        viewer.scrollIntoView({ block: "nearest" });
      } catch (err) {
        fail(err);
      }
    };

    const remove = async (img) => {
      if (!window.confirm("Delete this image from the archive?")) return;
      try {
        await api(`/api/image/${img.id}`, { method: "DELETE" });
        cache.delete(img.id);
        viewer.replaceChildren();
        await loadGallery();
        setNotice("image deleted.", "warn");
      } catch (err) {
        fail(err);
      }
    };

    const card = (img) => {
      const thumb = el("div", { class: "thumb dim", text: "loading..." });
      urlFor(img.id)
        .then((url) => thumb.replaceChildren(el("img", { src: url, alt: img.prompt, loading: "lazy" })))
        .catch((err) => {
          thumb.textContent = err.message;
          if (isAuthError(err)) fail(err);
        });
      return el(
        "figure",
        { class: "gallery-card" },
        thumb,
        el("figcaption", { class: "hint", text: img.prompt }),
        el(
          "div",
          { class: "actions" },
          el("button", { class: "btn", type: "button", onclick: () => showFull(img), text: "[ VIEW ]" }),
          el("button", { class: "btn danger", type: "button", onclick: () => remove(img), text: "[ DELETE ]" }),
        ),
      );
    };

    async function loadGallery() {
      const res = await api("/api/image");
      const images = res.images || [];
      grid.replaceChildren(
        ...(images.length ? images.map(card) : [el("p", { class: "dim", text: "no images yet." })]),
      );
    }

    async function generate() {
      const text = prompt.value.trim();
      if (!text) {
        setNotice("describe the image first.", "warn");
        return;
      }
      setBusy(true);
      setNotice("asking gemini for an image... (can take up to a minute)", "dim");
      try {
        const res = await api("/api/image/generate", {
          method: "POST",
          body: { prompt: text, model: modelSelect.value || null },
        });
        prompt.value = "";
        setNotice(`${res.images.length} image(s) saved to the archive.`, "ok");
        await loadGallery();
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    }

    prompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        generate();
      }
    });

    view.replaceChildren(
      panelTitle("image // gemini"),
      el(
        "p",
        { class: "hint" },
        'Gemini decides whether to make an image. Words like "draw" or "generate an image of" help. ',
        "Images are kept in the archive and are visible only to you.",
      ),
      prompt,
      el("div", { class: "composer-row" }, modelSelect, generateBtn),
      notice,
      viewer,
      el("hr", { class: "dim" }),
      panelTitle("archive"),
      grid,
    );

    setBusy(true);
    Promise.all([fillModelSelect(modelSelect), loadGallery()])
      .catch(fail)
      .finally(() => setBusy(false));
  }

  // ----- RESEARCH module -----------------------------------------------------

  // Each check can open a connection to Gemini, so the page checks slowly.
  const RESEARCH_PLAN_POLL_MS = 20000;
  const RESEARCH_POLL_MS = 60000;
  const RESEARCH_MAX_RETRIES = 5;
  const RESEARCH_STATUS = {
    planning: "WRITING THE PLAN on gemini",
    planned: "PLAN READY. not started",
    starting: "STARTING...",
    running: "RUNNING on gemini",
    done: "REPORT READY",
    failed: "FAILED",
  };

  const elapsed = (iso) => {
    const seconds = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
    return `${Math.floor(seconds / 60)}m ${pad(seconds % 60)}s`;
  };

  const isWebUrl = (value) => typeof value === "string" && /^https?:\/\//i.test(value);

  function researchView(view) {
    let currentId = null;
    let jobs = [];
    let pollTimer = null;
    let alive = true;
    let retries = 0;
    let planWatch = null; // id of a plan the page is waiting on, for the ready notice
    const urls = []; // object URLs for downloads, released when the screen is left

    window.addEventListener(
      "hashchange",
      () => {
        alive = false;
        clearTimeout(pollTimer);
        urls.forEach((u) => URL.revokeObjectURL(u));
      },
      { once: true },
    );

    const listBox = el("ul", { class: "chat-list", "aria-label": "Research jobs" });
    const topic = el("textarea", {
      class: "field composer",
      id: "research-topic",
      rows: 3,
      maxlength: 1000,
      placeholder: "Research question or topic.  Ctrl+Enter to make a plan.",
    });
    const modelSelect = el(
      "select",
      { class: "field select", id: "research-model", "aria-label": "Model" },
      el("option", { value: "", text: "default model" }),
    );
    const planBtn = el("button", { class: "btn", type: "button", onclick: () => makePlan(), text: "[ MAKE PLAN ]" });
    const newBtn = el("button", { class: "btn", type: "button", onclick: () => showNew(), text: "[ NEW ]" });
    const notice = el("div", { class: "status", role: "status", "aria-live": "polite" });
    const detail = el("div", { class: "research-detail", "aria-live": "polite" });
    const controls = [topic, modelSelect, planBtn];

    const setBusy = (on) => controls.forEach((c) => (c.disabled = on));
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

    // Shows in the tab title when the plan lands, so it is seen from another tab.
    // The title goes back to normal when the window is focused again.
    const flagPlanReady = () => {
      const base = document.title.replace(/^PLAN READY \/\/ /, "");
      document.title = `PLAN READY // ${base}`;
      const restore = () => {
        document.title = base;
        window.removeEventListener("focus", restore);
      };
      window.addEventListener("focus", restore);
    };

    const drawList = () => {
      if (!jobs.length) {
        listBox.replaceChildren(el("li", { class: "dim", text: "no research yet" }));
        return;
      }
      listBox.replaceChildren(
        ...jobs.map((j) =>
          el(
            "li",
            null,
            el("button", {
              class: `chat-item${j.id === currentId ? " current" : ""}`,
              type: "button",
              onclick: () => openJob(j.id),
              text: j.title || j.prompt,
            }),
            el("div", { class: "dim", text: `${RESEARCH_STATUS[j.status] || j.status}` }),
          ),
        ),
      );
    };

    const loadList = async () => {
      const res = await api("/api/research");
      jobs = res.jobs || [];
      drawList();
    };

    const planBlock = (plan) =>
      el(
        "div",
        { class: "plan" },
        el("div", { class: "dim", text: "PROPOSED PLAN" }),
        plan.eta_text ? el("p", { class: "hint", text: `estimated time: ${plan.eta_text}` }) : null,
        el("ol", { class: "plan-steps" }, (plan.steps || []).map((s) => el("li", { text: s }))),
      );

    const sourcesBlock = (sources) =>
      sources.length
        ? el(
            "ol",
            { class: "sources" },
            sources.map((s) =>
              el(
                "li",
                null,
                el("span", { text: s.title || "source" }),
                isWebUrl(s.url) ? " " : null,
                isWebUrl(s.url) ? el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.url }) : null,
              ),
            ),
          )
        : null;

    const reportBlock = (job) => {
      const url = URL.createObjectURL(new Blob([job.report || ""], { type: "text/markdown" }));
      urls.push(url);
      return el(
        "div",
        { class: "report" },
        el("div", { class: "dim", text: job.report_title ? `REPORT: ${job.report_title}` : "REPORT" }),
        el("pre", { class: "body", text: job.report || "" }),
        el("div", { class: "dim", text: "SOURCES" }),
        sourcesBlock(job.sources || []) || el("p", { class: "hint", text: "no web sources were cited." }),
        el("div", { class: "actions" },
          el("a", { class: "btn", href: url, download: `research-${job.id}.md`, text: "[ DOWNLOAD .MD ]" })),
      );
    };

    const drawJob = (job) => {
      const statusText =
        job.status === "running" && job.started_at
          ? `${RESEARCH_STATUS.running} for ${elapsed(job.started_at)}`
          : job.status === "planning"
            ? `${RESEARCH_STATUS.planning} for ${elapsed(job.created_at)}`
            : RESEARCH_STATUS[job.status] || job.status;
      const cls = job.status === "failed" ? "bad" : job.status === "done" ? "ok" : "warn";
      detail.replaceChildren(
        el("h3", { class: "sub", text: job.title || job.prompt }),
        el(
          "div",
          { class: "kv" },
          el("div", { class: "k", text: "STATUS" }),
          el("div", { class: `v ${cls}`, text: statusText }),
          el("div", { class: "k", text: "TOPIC" }),
          el("div", { class: "v", text: job.prompt }),
        ),
        job.status !== "done" && job.plan ? planBlock(job.plan) : null,
        job.status === "planning"
          ? el("p", { class: "hint", text: "Gemini is writing the plan. This page checks every few seconds. You can leave and come back." })
          : null,
        job.status === "running"
          ? el("p", { class: "hint", text: "Gemini is researching. This page checks every few seconds. You can leave and come back." })
          : null,
        job.status === "failed" ? el("p", { class: "bad", text: job.error || "research failed." }) : null,
        job.status !== "failed" && job.last_check_error
          ? el("p", { class: "hint", text: `last check did not reach gemini (retrying more slowly): ${job.last_check_error}` })
          : null,
        (job.status === "planned" || job.status === "failed") && job.plan
          ? el(
              "div",
              { class: "actions" },
              el("button", {
                class: "btn",
                type: "button",
                onclick: () => startJob(job.id),
                text: job.status === "failed" ? "[ RETRY START ]" : "[ START RESEARCH ]",
              }),
            )
          : null,
        job.status === "done" ? reportBlock(job) : null,
        el(
          "div",
          { class: "actions" },
          el("button", { class: "btn danger", type: "button", onclick: () => removeJob(job.id), text: "[ DELETE ]" }),
        ),
      );
    };

    const schedulePoll = (id, status = "running") => {
      clearTimeout(pollTimer);
      if (!alive) return;
      const delay = status === "planning" ? RESEARCH_PLAN_POLL_MS : RESEARCH_POLL_MS;
      pollTimer = setTimeout(() => pollJob(id), delay);
    };

    async function pollJob(id) {
      if (!alive || currentId !== id) return;
      try {
        const job = await api(`/api/research/${id}`);
        if (!alive || currentId !== id) return;
        retries = 0;
        if (job.status === "planning") planWatch = id;
        drawJob(job);
        if (job.status === "running" || job.status === "planning") schedulePoll(id, job.status);
        else await loadList();
        if (job.status !== "planning" && planWatch === id) {
          planWatch = null;
          if (job.status === "planned") {
            setNotice("plan ready. read it, then press START RESEARCH.", "ok");
            flagPlanReady();
          }
        }
      } catch (err) {
        if (isAuthError(err)) return fail(err);
        const transient = err.status >= 500;
        if (transient && retries < RESEARCH_MAX_RETRIES) {
          retries += 1;
          setNotice(`connection hiccup, retrying (${retries}/${RESEARCH_MAX_RETRIES})...`, "warn");
          schedulePoll(id);
          return;
        }
        fail(err);
      }
    }

    async function openJob(id) {
      clearTimeout(pollTimer);
      currentId = id;
      drawList();
      setBusy(true);
      try {
        const job = await api(`/api/research/${id}`);
        drawJob(job);
        setNotice("", "");
        if (job.status === "planning") planWatch = id;
        if (job.status === "running" || job.status === "planning") schedulePoll(id, job.status);
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    }

    function showNew() {
      clearTimeout(pollTimer);
      currentId = null;
      detail.replaceChildren(el("p", { class: "dim", text: "make a plan, read it, then start the research." }));
      drawList();
      setNotice("", "");
      topic.focus();
    }

    async function makePlan() {
      const text = topic.value.trim();
      if (!text) {
        setNotice("type a research topic first.", "warn");
        return;
      }
      setBusy(true);
      setNotice("making a research plan with gemini. this takes a minute or two.", "dim");
      try {
        const job = await api("/api/research", {
          method: "POST",
          body: { prompt: text, model: modelSelect.value || null },
        });
        topic.value = "";
        currentId = job.id;
        drawJob(job);
        await loadList();
        if (job.status === "planning") {
          planWatch = job.id;
          setNotice("gemini is still writing the plan. this page checks every few seconds.", "dim");
          schedulePoll(job.id, "planning");
        } else {
          setNotice("plan ready. start the research when you are happy with it.", "ok");
        }
      } catch (err) {
        fail(err);
      } finally {
        setBusy(false);
      }
    }

    async function startJob(id) {
      clearTimeout(pollTimer);
      setBusy(true);
      setNotice("asking gemini to start the research...", "dim");
      try {
        const job = await api(`/api/research/${id}/start`, { method: "POST" });
        drawJob(job);
        retries = 0;
        setNotice("research started. this can take 5 to 10 minutes.", "ok");
        schedulePoll(id);
        await loadList();
      } catch (err) {
        fail(err);
        if (currentId === id) await openJob(id).catch(() => {});
      } finally {
        setBusy(false);
      }
    }

    async function removeJob(id) {
      if (!window.confirm("Delete this research from the archive? The Gemini chat is not touched.")) return;
      try {
        await api(`/api/research/${id}`, { method: "DELETE" });
        if (currentId === id) showNew();
        await loadList();
        setNotice("research deleted from the archive.", "warn");
      } catch (err) {
        fail(err);
      }
    }

    topic.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        makePlan();
      }
    });

    view.replaceChildren(
      panelTitle("research // gemini"),
      el(
        "p",
        { class: "hint" },
        "Gemini drafts a plan first. You start it when the plan looks right. ",
        "Research takes several minutes and needs an account that has deep research.",
      ),
      el(
        "div",
        { class: "chat-layout" },
        el(
          "aside",
          { class: "chat-side" },
          el("div", { class: "actions" }, newBtn),
          el("div", { class: "hint", text: "saved research" }),
          listBox,
        ),
        el(
          "section",
          { class: "chat-main" },
          topic,
          el("div", { class: "composer-row" }, modelSelect, planBtn),
          notice,
          detail,
        ),
      ),
    );

    showNew();
    setBusy(true);
    Promise.all([loadList(), fillModelSelect(modelSelect)])
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
