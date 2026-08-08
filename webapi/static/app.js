/* ============================================================
   AI COMMAND CENTER — Application
   Hash router, views, SSE chat, command palette, modals, theme
   ============================================================ */
(() => {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------------- Markdown (safe subset) ---------------- */
  function mdToHtml(md) {
    md = md || "";
    // escape raw text first (only HTML-special chars), tags built later stay intact
    md = esc(md);
    // stash fenced code blocks
    const codes = [];
    md = md.replace(/```(\w*)\n([\s\S]*?)```/g, (_, __, code) => {
      codes.push(code);
      return "\u0001" + (codes.length - 1) + "\u0001";
    });
    // inline formatting (bold / italic / inline code / links) before tags are built
    md = inline(md);
    // headings (anywhere on a line)
    md = md.replace(/^### (.*)$/gm, "<h3>$1</h3>")
           .replace(/^## (.*)$/gm, "<h2>$1</h2>")
           .replace(/^# (.*)$/gm, "<h1>$1</h1>");
    // hr
    md = md.replace(/^---+$/gm, "<hr>");
    // process blocks (blank-line separated)
    const blocks = md.split(/\n{2,}/);
    const out = blocks.map(block => {
      const lines = block.split("\n").map(l => l.trim()).filter(Boolean);
      if (!lines.length) return "";
      if (lines.every(l => l.startsWith("|") && l.endsWith("|"))) {
        const rows = lines.map(r => r.slice(1, -1).split("|").map(c => c.trim()));
        if (rows.length >= 2 && rows[1].every(c => /^:?-+:?$/.test(c))) {
          const head = rows[0], body = rows.slice(2);
          let h = "<table><thead><tr>" + head.map(c => "<th>" + c + "</th>").join("") + "</tr></thead><tbody>";
          for (const row of body) h += "<tr>" + row.map(c => "<td>" + c + "</td>").join("") + "</tr>";
          return h + "</tbody></table>";
        }
      }
      if (lines.every(l => /^[-*]\s/.test(l)))
        return "<ul>" + lines.map(l => "<li>" + l.replace(/^[-*]\s*/, "") + "</li>").join("") + "</ul>";
      if (lines.every(l => /^\d+\.\s/.test(l)))
        return "<ol>" + lines.map(l => "<li>" + l.replace(/^\d+\.\s*/, "") + "</li>").join("") + "</ol>";
      if (/^<(table|h[1-3]|pre|hr)/.test(block.trim())) return block.trim();
      return "<p>" + block.split("\n").map(l => l.trim()).join("<br>") + "</p>";
    }).join("");
    // restore code blocks
    return out.replace(/\u0001(\d+)\u0001/g, (_, i) => "<pre><code>" + codes[+i] + "</code></pre>");
  }
  function inline(s) {
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }

  /* ---------------- State ---------------- */
  const DB = window.AppData;
  const state = {
    view: "home",
    theme: localStorage.getItem("acc-theme") || "system",
    sidebarOpen: false,
    activeAgent: localStorage.getItem("acc-agent") || "commander",
    conversations: JSON.parse(localStorage.getItem("acc-convs") || "null") || DB.conversations,
    tasks: JSON.parse(localStorage.getItem("acc-tasks") || "null") || DB.tasks,
    automations: JSON.parse(localStorage.getItem("acc-autos") || "null") || DB.automations,
    files: JSON.parse(localStorage.getItem("acc-files") || "null") || DB.files,
    documents: JSON.parse(localStorage.getItem("acc-docs") || "null") || DB.documents,
    knowledge: DB.knowledge,
    notifications: JSON.parse(localStorage.getItem("acc-notifs") || "null") || DB.notifications,
    chat: [],          // active conversation messages
    chatStatus: "idle", // idle | running
    selectedAgent: null,
    runningConvId: null,
    filePreview: null,
    approvalRequest: null,
  };
  const save = (k, v) => localStorage.setItem(k, JSON.stringify(v));
  const persist = () => { save("acc-convs", state.conversations); save("acc-tasks", state.tasks); save("acc-autos", state.automations); save("acc-files", state.files); save("acc-docs", state.documents); save("acc-notifs", state.notifications); };

  const agentMeta = (key) => DB.agents.find(a => a.key === key) || { label: key, icon: "🤖", desc: "" };

  /* ---------------- Toast ---------------- */
  function toast(title, msg, kind = "") {
    const wrap = $("#toasts");
    const el = document.createElement("div");
    el.className = "toast " + kind;
    el.innerHTML = '<div><div class="t-title">' + esc(title) + '</div><div class="t-msg">' + esc(msg) + '</div></div>';
    wrap.appendChild(el);
    setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(() => el.remove(), 320); }, 3800);
  }

  /* ---------------- Router ---------------- */
  function navigate(view, param) {
    location.hash = "#/" + view + (param ? "/" + encodeURIComponent(param) : "");
  }
  function currentRoute() {
    const h = location.hash.replace(/^#\/?/, "");
    const [view, param] = h.split("/");
    return { view: view || "home", param: param ? decodeURIComponent(param) : null };
  }
  window.addEventListener("hashchange", render);

  /* ---------------- Shell ---------------- */
  function applyTheme() {
    const el = document.documentElement;
    if (state.theme === "system") {
      const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      el.dataset.theme = dark ? "dark" : "light";
    } else el.dataset.theme = state.theme;
    $("#themeBtn").textContent = el.dataset.theme === "dark" ? "🌙" : "☀️";
  }
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state.theme === "system") applyTheme(); });

  const NAV = [
    { view: "home", ic: "🏠", label: "Home" },
    { view: "chat", ic: "💬", label: "AI Chat" },
    { view: "agents", ic: "🤖", label: "Agents" },
    { view: "tasks", ic: "📋", label: "Tasks" },
    { view: "automations", ic: "⚡", label: "Automations" },
    { view: "files", ic: "📁", label: "Files" },
    { view: "analytics", ic: "📊", label: "Analytics" },
    { view: "documents", ic: "📄", label: "Documents" },
    { view: "knowledge", ic: "🧠", label: "Knowledge" },
    { view: "integrations", ic: "🔌", label: "Integrations" },
    { view: "settings", ic: "⚙️", label: "Settings" },
  ];
  const MOBILE_NAV = ["home", "chat", "tasks", "files", "more"];

  function renderShell() {
    const app = $("#app");
    app.innerHTML = `
      <div class="app">
        <div class="sidebar-backdrop hidden" id="sbBackdrop"></div>
        <aside class="sidebar" id="sidebar">
          <div class="brand">
            <div class="brand-logo">🤖</div>
            <div>
              <div class="brand-name">AI Command Center</div>
              <div class="brand-sub">Your Intelligent Work Assistant</div>
            </div>
          </div>
          <nav class="nav" id="mainNav" aria-label="Main navigation"></nav>
          <div class="sidebar-foot">
            <div class="security-note"><span>🔒</span><span>Your files are protected.<br>No data leaves your agents without your approval.</span></div>
          </div>
        </aside>
        <div class="main">
          <header class="header">
            <button class="icon-btn hamburger" id="hamburger" aria-label="Open menu">☰</button>
            <div class="header-title" id="headerTitle">Home</div>
            <div class="header-search" id="globalSearch" role="button" tabindex="0" aria-label="Search">
              <span>🔍</span><span>Search anything…</span><kbd>Ctrl K</kbd>
            </div>
            <div class="header-actions">
              <button class="icon-btn" id="themeBtn" aria-label="Toggle theme">☀️</button>
              <button class="icon-btn" id="notifBtn" aria-label="Notifications">🔔<span class="ping" id="notifPing" hidden></span></button>
              <button class="btn btn-soft btn-sm" id="newChatBtn" style="margin-left:4px">+ New Chat</button>
              <div class="avatar sm" style="margin-left:4px" title="Sandeep">SA</div>
            </div>
          </header>
          <div style="position:relative" id="headerPanelWrap">
            <div class="panel hidden" id="notifPanel"></div>
          </div>
          <main class="content" id="content"></main>
        </div>
        <nav class="bottom-nav" id="bottomNav" aria-label="Mobile navigation"></nav>
      </div>
      <div class="cmd hidden" id="cmdPalette"></div>
      <div class="overlay hidden" id="modalOverlay"></div>
      <div class="toasts" id="toasts"></div>`;

    renderNav();
    renderBottomNav();
    bindShell();
  }

  function renderNav() {
    const nav = $("#mainNav");
    nav.innerHTML = NAV.map((n, i) => {
      const count = navCount(n.view);
      return '<button class="nav-item' + (state.view === n.view ? " active" : "") + '" data-view="' + n.view + '">' +
        '<span class="ic">' + n.ic + '</span><span>' + n.label + '</span>' +
        (count ? '<span class="count">' + count + '</span>' : "") + '</button>';
    }).join("");
    $$("#mainNav .nav-item").forEach(b => b.addEventListener("click", () => { navigate(b.dataset.view); closeSidebar(); }));

    // recent conversations block
    const navEl = $("#mainNav");
    const recent = '<div class="nav-section">Recent</div>' + state.conversations.map(c =>
      '<button class="nav-item" data-openconv="' + c.id + '"><span class="ic">' + (c.pinned ? "📌" : "💬") + '</span><span>' + esc(c.title) + '</span></button>'
    ).join("");
    navEl.insertAdjacentHTML("beforeend", recent);
    $$("#mainNav [data-openconv]").forEach(b => b.addEventListener("click", () => { openConversation(b.dataset.openconv); }));
  }
  function navCount(view) {
    if (view === "tasks") return state.tasks.filter(t => t.status === "run" || t.status === "wait").length;
    if (view === "automations") return state.automations.length;
    return 0;
  }
  function renderBottomNav() {
    const nav = $("#bottomNav");
    nav.innerHTML = MOBILE_NAV.map(v => {
      const item = NAV.find(n => n.view === v) || { ic: "☰", label: "More" };
      return '<button data-view="' + v + '" class="' + (state.view === v ? "active" : "") + '"><span class="ic">' + item.ic + '</span>' + item.label + '</button>';
    }).join("");
    $$("#bottomNav button").forEach(b => b.addEventListener("click", () => navigate(b.dataset.view === "more" ? "settings" : b.dataset.view)));
  }
  function bindShell() {
    $("#hamburger").addEventListener("click", toggleSidebar);
    $("#sbBackdrop").addEventListener("click", closeSidebar);
    $("#themeBtn").addEventListener("click", () => { const order = ["light", "dark", "system"]; state.theme = order[(order.indexOf(state.theme) + 1) % 3]; localStorage.setItem("acc-theme", state.theme); applyTheme(); });
    $("#globalSearch").addEventListener("click", () => openCommand(true));
    $("#globalSearch").addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openCommand(true); } });
    $("#notifBtn").addEventListener("click", toggleNotifications);
    $("#newChatBtn").addEventListener("click", () => { state.chat = []; state.runningConvId = null; navigate("chat"); });
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openCommand(); }
      if (e.key === "Escape") { closeCommand(); closeNotifications(); closeSidebar(); closeModal(); }
    });
  }
  function toggleSidebar() { state.sidebarOpen = !state.sidebarOpen; $("#sidebar").classList.toggle("open", state.sidebarOpen); $("#sbBackdrop").classList.toggle("hidden", !state.sidebarOpen); }
  function closeSidebar() { state.sidebarOpen = false; $("#sidebar").classList.remove("open"); $("#sbBackdrop").classList.add("hidden"); }

  /* ---------------- Notifications ---------------- */
  function toggleNotifications() {
    const panel = $("#notifPanel");
    const isOpen = !panel.classList.contains("hidden");
    if (!isOpen) renderNotifications();
    panel.classList.toggle("hidden", isOpen);
    if (!isOpen) { state.notifications.forEach(n => n.read = true); persist(); $("#notifPing").hidden = true; }
  }
  function closeNotifications() { $("#notifPanel").classList.add("hidden"); }
  function renderNotifications() {
    const panel = $("#notifPanel");
    const imp = state.notifications.filter(n => n.kind === "important");
    const upd = state.notifications.filter(n => n.kind === "update");
    panel.innerHTML =
      '<div class="spread" style="padding:14px 16px;border-bottom:1px solid var(--border)"><strong style="font-size:14px">Notifications</strong>' +
      '<button class="btn btn-ghost btn-sm" id="clearNotifs">Clear all</button></div>' +
      '<div style="max-height:380px;overflow-y:auto">' +
      renderNotifGroup("Important", imp) + renderNotifGroup("Updates", upd) +
      '</div>';
    $("#clearNotifs").addEventListener("click", () => { state.notifications = []; persist(); renderNotifications(); $("#notifPing").hidden = true; });
    $$("#notifPanel .notif-item").forEach(el => el.addEventListener("click", () => {
      const kind = el.dataset.kind, title = el.dataset.title;
      if (kind === "approval") openApproval({ title: "Send the project report to the client", recipient: "client@example.com", attachment: "Monthly_Project_Report.pdf" });
      else toast(title, "Opened for you", "ok");
      closeNotifications();
    }));
  }
  function renderNotifGroup(label, items) {
    if (!items.length) return "";
    return '<div class="nav-section">' + label + '</div>' + items.map(n =>
      '<div class="notif-item' + (n.read ? "" : " unread") + '" data-kind="' + (n.desc && n.desc.includes("send") ? "approval" : "") + '" data-title="' + esc(n.title) + '">' +
      '<div>' + (n.kind === "important" ? "🔴" : "🟢") + '</div>' +
      '<div class="grow"><div class="ni-title">' + esc(n.title) + '</div><div class="ni-desc">' + esc(n.desc) + '</div><div class="ni-time">' + n.time + '</div></div></div>'
    ).join("");
  }

  /* ---------------- Command palette + search ---------------- */
  const COMMANDS = [
    { group: "Actions", items: [
      { label: "Start new chat", ic: "💬", kbd: "", run: () => { state.chat = []; navigate("chat"); } },
      { label: "Create task", ic: "📋", kbd: "", run: () => navigate("tasks") },
      { label: "Create automation", ic: "⚡", kbd: "", run: () => { openAutomationModal(); } },
      { label: "Upload file", ic: "📁", kbd: "", run: () => { navigate("files"); setTimeout(() => $("#fileInput") && $("#fileInput").click(), 150); } },
      { label: "Run agent", ic: "🤖", kbd: "", run: () => navigate("agents") },
      { label: "Open settings", ic: "⚙️", kbd: "", run: () => navigate("settings") },
      { label: "Toggle dark mode", ic: "🌙", kbd: "", run: () => { state.theme = state.theme === "dark" ? "light" : "dark"; localStorage.setItem("acc-theme", state.theme); applyTheme(); } },
    ]},
    { group: "Go to", items: NAV.map(n => ({ label: n.label, ic: n.ic, kbd: "", run: () => navigate(n.view) })) },
  ];
  function openCommand(searchOnly) {
    const palette = $("#cmdPalette");
    palette.classList.remove("hidden");
    palette.innerHTML =
      '<div class="cmd-box">' +
      '<div class="cmd-input-wrap"><span>🔍</span><input class="cmd-input" id="cmdInput" placeholder="' + (searchOnly ? "Search files, tasks, conversations…" : "Type a command or search…") + '" autocomplete="off"><span>Esc</span></div>' +
      '<div class="cmd-list" id="cmdList"></div></div>';
    renderCmd("");
    const input = $("#cmdInput");
    input.focus();
    input.addEventListener("input", () => renderCmd(input.value));
    input.addEventListener("keydown", e => {
      if (e.key === "Enter") { const sel = $(".cmd-item.sel"); if (sel) { palette.classList.add("hidden"); sel.dataset.run && runCommand(sel.dataset.run); } }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); moveCmdSel(e.key === "ArrowDown" ? 1 : -1); }
    });
  }
  let cmdRunFns = {};
  function runCommand(idx) { cmdRunFns[idx] && cmdRunFns[idx](); }
  function moveCmdSel(dir) {
    const items = $$(".cmd-item");
    let cur = items.findIndex(el => el.classList.contains("sel"));
    items.forEach(el => el.classList.remove("sel"));
    cur = Math.max(0, Math.min(items.length - 1, (cur < 0 ? 0 : cur) + dir));
    items[cur] && items[cur].classList.add("sel");
    items[cur] && items[cur].scrollIntoView({ block: "nearest" });
  }
  function renderCmd(q) {
    const list = $("#cmdList");
    cmdRunFns = {};
    const ql = (q || "").toLowerCase();
    let html = "", idx = 0;
    const searchResults = globalSearch(ql).slice(0, 5);
    if (searchResults.length && ql) {
      html += '<div class="cmd-sec">Search results</div>';
      searchResults.forEach(r => {
        html += '<div class="cmd-item" data-idx="' + idx + '"><span class="ci-ic">' + r.ic + '</span><div><b>' + esc(r.title) + '</b> <span class="muted">· ' + r.kind + '</span></div></div>';
        cmdRunFns[idx++] = r.run;
      });
    }
    COMMANDS.forEach(group => {
      const items = group.items.filter(i => !ql || i.label.toLowerCase().includes(ql));
      if (!items.length) return;
      html += '<div class="cmd-sec">' + group.group + '</div>';
      items.forEach(i => {
        html += '<div class="cmd-item' + (idx === 0 ? " sel" : "") + '" data-idx="' + idx + '"><span class="ci-ic">' + i.ic + '</span><div>' + esc(i.label) + '</div><span class="ci-kbd">' + i.kbd + '</span></div>';
        cmdRunFns[idx++] = i.run;
      });
    });
    if (!html) html = '<div class="empty"><div class="empty-ic">🔍</div><h3>No results</h3><p>Try a different search term.</p></div>';
    list.innerHTML = html;
    $$("#cmdList .cmd-item").forEach(el => el.addEventListener("click", () => { closeCommand(); runCommand(el.dataset.idx); }));
  }
  function closeCommand() { $("#cmdPalette").classList.add("hidden"); }

  /* ---------------- Global search ---------------- */
  function globalSearch(q) {
    if (!q) return [];
    const results = [];
    const push = (kind, ic, title, run) => { if (title.toLowerCase().includes(q)) results.push({ kind, ic, title, run }); };
    state.conversations.forEach(c => push("Conversation", "💬", c.title, () => openConversation(c.id)));
    state.files.forEach(f => push("File", DB.fileIcon(f.kind), f.name, () => openFilePreview(f)));
    state.tasks.forEach(t => push("Task", "📋", t.title, () => openTaskDetail(t)));
    state.automations.forEach(a => push("Automation", "⚡", a.name, () => openAutomationModal(a)));
    state.documents.forEach(d => push("Document", "📄", d.title, () => toast("Document", d.title + " opened", "ok")));
    return results;
  }

  /* ---------------- Modal helpers ---------------- */
  function openModal(html) {
    const ov = $("#modalOverlay");
    ov.classList.remove("hidden");
    ov.innerHTML = '<div class="modal" role="dialog" aria-modal="true" aria-label="Dialog">' + html + '</div>';
    ov.addEventListener("click", e => { if (e.target === ov) closeModal(); });
  }
  function closeModal() { $("#modalOverlay").classList.add("hidden"); $("#modalOverlay").innerHTML = ""; }

  /* ---------------- Main renderer ---------------- */
  function render() {
    const { view, param } = currentRoute();
    state.view = view;
    renderShell();
    applyTheme();
    updateNotifPing();
    const content = $("#content");
    const headerTitle = NAV.find(n => n.view === view)?.label || "Home";
    $("#headerTitle").textContent = headerTitle;

    const views = { home, chat, agents, tasks, automations, files, analytics, documents, knowledge, integrations, settings };
    (views[view] || home)(content, param);
    content.scrollTop = 0;
  }
  function updateNotifPing() { $("#notifPing").hidden = !state.notifications.some(n => !n.read); }

  /* ---------------- Home ---------------- */
  function home(content) {
    const hour = new Date().getHours();
    const greet = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
    const running = state.tasks.filter(t => t.status === "run").length;
    content.innerHTML = `
      <div class="page">
        <div class="hero">
          <h1>${greet}, Sandeep 👋</h1>
          <p>What would you like to accomplish today?</p>
          <div class="hero-composer">
            <span style="font-size:17px;color:var(--text-3)">✨</span>
            <textarea id="heroInput" rows="1" placeholder="Ask me anything, upload a file, or delegate a task…" aria-label="Ask anything"></textarea>
            <button class="btn btn-primary" id="heroSend">Send</button>
          </div>
          <div class="quick-actions">
            ${[["✍️","Write"],["📊","Analyze Data"],["📄","Create Document"],["📑","Create Presentation"],["🔍","Research"],["🤖","Run Agent"],["⚡","Automate"],["📁","Analyze File"]].map(([ic, l]) =>
              '<div class="qa-card" data-qa="' + l + '"><span class="qa-ic">' + ic + '</span><span class="qa-label">' + l + '</span></div>').join("")}
          </div>
        </div>
        <div class="dash-grid">
          ${kpi("Tasks active", running, "🟢 this week", "up")}
          ${kpi("Documents", state.documents.length, "▲ 2 this week", "up")}
          ${kpi("Automations", state.automations.filter(a=>a.status==="active").length, "all running", "")}
          ${kpi("Unread alerts", state.notifications.filter(n=>!n.read).length, "", "")}
        </div>
        <div class="section-title">Continue where you left off</div>
        <div class="grid-cards">
          ${state.conversations.slice(0, 4).map(c => `
            <div class="card hoverable" data-conv="${c.id}">
              <div class="spread"><span class="avatar sm ai">${agentMeta(c.agent).icon}</span><span class="badge sched">${esc(c.updated)}</span></div>
              <div class="card-title" style="margin-top:10px">${esc(c.title)}</div>
              <div class="card-sub">${esc(agentMeta(c.agent).label)} · ${c.items} messages</div>
            </div>`).join("")}
        </div>
        <div class="section-title">Frequently used</div>
        <div class="grid-3">
          ${[["📄","Project Report","Ask the docs agent to build a monthly report","Run →"],["💼","Resume Score","Match your resume against a job description","Run →"],["📊","Data Analysis","Analyze an Excel or CSV file with the finance agent","Run →"]].map(([ic,t,d,a]) =>
            `<div class="card hoverable" data-freq="${t}"><div class="row"><span class="avatar sm">${ic}</span><b>${t}</b></div><div class="card-sub" style="margin-top:8px">${d}</div><div style="margin-top:10px"><span class="badge">${a}</span></div></div>`).join("")}
        </div>
      </div>`;

    const input = $("#heroInput");
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; });
    input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendFromHome(); } });
    $("#heroSend").addEventListener("click", sendFromHome);
    $$(".qa-card").forEach(c => c.addEventListener("click", () => quickAction(c.dataset.qa)));
    $$("[data-conv]").forEach(c => c.addEventListener("click", () => openConversation(c.dataset.conv)));
    $$("[data-freq]").forEach(c => c.addEventListener("click", () => quickAction(c.dataset.freq)));
  }
  function kpi(label, value, delta, dir) {
    return '<div class="card kpi"><div class="kpi-label">' + label + '</div><div class="kpi-value">' + value + '</div>' +
      (delta ? '<div class="kpi-delta ' + (dir || "") + '">' + delta + '</div>' : "") + '</div>';
  }
  function sendFromHome() {
    const input = $("#heroInput");
    const text = input.value.trim();
    if (!text) return;
    state.chat = [];
    state.runningConvId = null;
    navigate("chat");
    setTimeout(() => { addUserMessage(text); runAgentTask(text); }, 60);
  }
  function quickAction(label) {
    const map = {
      "Write": "Draft a short professional update about my current work.",
      "Analyze Data": "Summarise the finance ledger for this month and flag cash-flow risks.",
      "Create Document": "Generate a weekly project status report with the docs agent.",
      "Create Presentation": "Outline a 10-slide pitch deck for the DPIIC project.",
      "Research": "Search the web for the latest PMP certification requirements.",
      "Run Agent": null,
      "Automate": null,
      "Analyze File": null,
    };
    if (map[label] === null) { navigate(label === "Run Agent" ? "agents" : label === "Automate" ? "automations" : "files"); return; }
    state.chat = [];
    navigate("chat");
    setTimeout(() => { addUserMessage(map[label]); runAgentTask(map[label]); }, 60);
  }

  /* ---------------- Chat ---------------- */
  function chat(content) {
    content.innerHTML = `
      <div class="chat-wrap">
        <div class="chat-scroll" id="chatScroll"></div>
        <div class="composer">
          <div class="composer-inner" style="position:relative">
            <textarea id="chatInput" rows="1" placeholder="Ask your AI agent…" aria-label="Message"></textarea>
            <div class="attach-list" id="attachList"></div>
            <div class="composer-tools">
              <button class="btn btn-icon" id="attachBtn" title="Attach file" aria-label="Attach file">📎</button>
              <button class="btn btn-icon" id="imgBtn" title="Upload image" aria-label="Upload image">🖼️</button>
              <button class="btn btn-icon" id="micBtn" title="Voice input (coming soon)" aria-label="Voice">🎤</button>
              <button class="agent-chip" id="agentPicker">🤖 <span id="agentPickerLabel"></span> ▾</button>
              <button class="btn btn-primary composer-send" id="chatSend">Send</button>
            </div>
            <div class="selector-pop hidden" id="agentSelector"></div>
          </div>
        </div>
      </div>`;

    $("#agentPickerLabel").textContent = agentMeta(state.activeAgent).label;
    renderAgentSelector();
    $("#agentPicker").addEventListener("click", e => { e.stopPropagation(); $("#agentSelector").classList.toggle("hidden"); });
    document.addEventListener("click", () => $("#agentSelector").classList.add("hidden"), { once: true });

    const input = $("#chatInput");
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 180) + "px"; });
    input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
    $("#chatSend").addEventListener("click", sendChat);
    $("#attachBtn").addEventListener("click", () => openFilePicker());
    $("#imgBtn").addEventListener("click", () => openFilePicker(true));
    $("#micBtn").addEventListener("click", () => toast("Voice input", "Voice recording is coming soon.", "warn"));

    // restore conversation
    if (state.chat.length) {
      state.chat.forEach(m => appendMessage(m, false));
    } else if (state.runningConvId) {
      openConversation(state.runningConvId, true);
    }
    // delegated timeline toggle (works after restore re-render too)
    $("#chatScroll").addEventListener("click", e => {
      const btn = e.target.closest(".tlToggle");
      if (!btn) return;
      const tl = btn.closest(".timeline");
      const d = tl && tl.querySelector(".tlDetail");
      if (!d) return;
      d.classList.toggle("hidden");
      btn.textContent = d.classList.contains("hidden") ? "Show details ▾" : "Hide details ▴";
    });
    scrollChat();
  }
  function renderAgentSelector() {
    const sel = $("#agentSelector");
    sel.innerHTML = DB.agents.map(a =>
      '<div class="selector-opt' + (a.key === state.activeAgent ? " sel" : '') + '" data-agent="' + a.key + '">' +
      '<span class="avatar sm">' + a.icon + '</span>' +
      '<div class="grow"><div class="so-name">' + esc(a.label) + '</div><div class="so-desc">' + esc(a.desc) + '</div></div>' +
      (a.key === state.activeAgent ? "✓" : "") + '</div>').join("");
    $$("#agentSelector .selector-opt").forEach(o => o.addEventListener("click", () => {
      state.activeAgent = o.dataset.agent;
      localStorage.setItem("acc-agent", state.activeAgent);
      $("#agentPickerLabel").textContent = agentMeta(state.activeAgent).label;
      renderAgentSelector();
      $("#agentSelector").classList.add("hidden");
    }));
  }
  function addUserMessage(text) {
    const m = { role: "user", text };
    state.chat.push(m);
    appendMessage(m, false);
  }
  function appendMessage(m, animate = true) {
    const scroll = $("#chatScroll");
    if (!scroll) return;
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (m.role === "user") {
      scroll.insertAdjacentHTML("beforeend",
        '<div class="msg user"><div class="avatar user">SA</div><div class="msg-body"><div class="msg-bubble">' + esc(m.text) + '</div><div class="msg-meta">' + time + '</div></div></div>');
    } else if (m.type === "timeline") {
      scroll.insertAdjacentHTML("beforeend", '<div class="timeline" data-tl="1">' + timelineHtml(m.tl) + '</div>');
    } else if (m.type === "result") {
      scroll.insertAdjacentHTML("beforeend",
        '<div class="msg"><div class="avatar ai">✦</div><div class="msg-body"><div class="msg-bubble"><div class="md">' + mdToHtml(m.text) + '</div></div>' +
        '<div class="resp-actions">' + responseActions(m) + '</div><div class="msg-meta"><span>' + agentMeta(m.agent).label + '</span><span>·</span><span>' + time + '</span></div></div></div>');
    } else if (m.type === "error") {
      scroll.insertAdjacentHTML("beforeend",
        '<div class="msg"><div class="avatar ai">✦</div><div class="msg-body"><div class="error-box"><div class="eb-title">⚠️ Something went wrong</div>' +
        '<div class="eb-desc">' + esc(m.text) + '</div><div class="row"><button class="btn btn-sm" data-retry="1">Retry</button><button class="btn btn-sm btn-ghost" data-method="1">Try another method</button></div></div></div></div>');
    }
    if (animate) scrollChat();
  }
  function scrollChat() { const s = $("#chatScroll"); if (s) s.scrollTop = s.scrollHeight; }

  function responseActions(m) {
    return ['<button class="chip" data-act="edit">✏️ Edit</button>',
            '<button class="chip" data-act="pdf">📄 Export PDF</button>',
            '<button class="chip" data-act="word">📝 Export Word</button>',
            '<button class="chip" data-act="ppt">📑 Presentation</button>',
            '<button class="chip" data-act="email">📧 Email</button>',
            '<button class="chip" data-act="save">🧠 Save to Knowledge</button>'].join("");
  }

  function sendChat() {
    if (state.chatStatus === "running") return;
    const input = $("#chatInput");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    input.style.height = "auto";
    addUserMessage(text);
    runAgentTask(text);
  }

  function runAgentTask(task) {
    state.chatStatus = "running";
    const agent = state.activeAgent;
    // timeline
    const steps = ["Understanding request", "Checking context", "Executing tools", "Preparing response", "Quality check"];
    showTimeline(steps, 0);
    setTimeout(() => setTimelineStep(1), 400);

    fetch("/api/v1/run/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent, task }),
    }).then(res => {
      if (!res.ok || !res.body) throw new Error("Request failed (" + res.status + ")");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      const toolNames = [];
      const read = () => reader.read().then(({ done, value }) => {
        if (done) return;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const chunk = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          if (!chunk.startsWith("data: ")) continue;
          let evt;
          try { evt = JSON.parse(chunk.slice(6)); } catch { continue; }
          if (evt.type === "meta") { /* label */ }
          else if (evt.type === "tool_call") {
            toolNames.push(evt.name);
            setTimelineStep(2, evt.name);
            appendToolEvent(evt);
            setTimelineStep(3);
          } else if (evt.type === "assistant") {
            setTimelineStep(3);
          } else if (evt.type === "result") {
            setTimelineStep(3);
            setTimelineStep(4);
            setTimelineDone();
            const m = { role: "assistant", type: "result", text: evt.text, agent };
            state.chat.push(m);
            appendMessage(m);
            state.chatStatus = "idle";
            bindResultActions();
            const conv = { id: "c" + Date.now(), title: task.slice(0, 34) + (task.length > 34 ? "…" : ""), agent, updated: "just now", pinned: false, items: state.chat.length };
            state.conversations.unshift(conv);
            persist();
            return;
          } else if (evt.type === "error") {
            setTimelineDone();
            const m = { role: "assistant", type: "error", text: evt.text, agent };
            state.chat.push(m);
            appendMessage(m);
            state.chatStatus = "idle";
            bindResultActions();
            return;
          }
        }
        return read();
      });
      return read();
    }).catch(err => {
      setTimelineDone();
      const m = { role: "assistant", type: "error", text: String(err), agent };
      state.chat.push(m);
      appendMessage(m);
      state.chatStatus = "idle";
      bindResultActions();
    });
    bindResultActions();
  }

  function timelineHtml(tl) {
    const steps = tl.steps.map((s, i) =>
      '<div class="tl-step ' + (i < tl.active ? "done" : i === tl.active ? "doing" : "pending") + '"><span class="tl-ic">' + (i < tl.active ? "✓" : i === tl.active ? "●" : "○") + '</span>' + s + '</div>').join("");
    const tools = tl.tools.map(t =>
      '<div class="tl-tool"><span class="tt-name">⚙ ' + esc(t.name) + '</span><span class="muted">' + esc(t.output || "completed") + '</span></div>').join("");
    return '<div class="timeline-title">' + (tl.done ? "✅ Completed" : "⚙️ Working on your request") + '</div>' +
      steps +
      '<div class="tl-detail"><button class="btn btn-ghost btn-sm tlToggle">Show details ▾</button><div class="hidden tlDetail" style="margin-top:6px">' + tools + '</div></div>';
  }

  function showTimeline(steps, active) {
    const scroll = $("#chatScroll");
    if (!scroll) return;
    const tlMsg = { role: "assistant", type: "timeline", tl: { steps, active, tools: [], done: false } };
    state.chat.push(tlMsg);
    scroll.insertAdjacentHTML("beforeend", '<div class="timeline" data-tl="1">' + timelineHtml(tlMsg.tl) + '</div>');
    scrollChat();
  }
  function currentTimeline() {
    const scroll = $("#chatScroll");
    if (!scroll) return null;
    const tls = $$('.timeline[data-tl="1"]', scroll);
    return tls.length ? tls[tls.length - 1] : null;
  }
  function currentTlMsg() {
    for (let i = state.chat.length - 1; i >= 0; i--) {
      if (state.chat[i] && state.chat[i].tl) return state.chat[i];
    }
    return null;
  }
  function refreshTimeline(tlMsg) {
    const tl = currentTimeline();
    if (!tl) return;
    const detail = tl.querySelector(".tlDetail");
    const wasOpen = detail && !detail.classList.contains("hidden");
    tl.innerHTML = timelineHtml(tlMsg.tl);
    if (wasOpen) {
      const nd = tl.querySelector(".tlDetail");
      const nb = tl.querySelector(".tlToggle");
      if (nd && nb) { nd.classList.remove("hidden"); nb.textContent = "Hide details ▴"; }
    }
  }
  function setTimelineStep(i, toolName) {
    const tlMsg = currentTlMsg();
    if (!tlMsg) return;
    tlMsg.tl.active = i;
    if (toolName && !tlMsg.tl.tools.some(t => t.name === toolName)) tlMsg.tl.tools.push({ name: toolName, output: "completed" });
    refreshTimeline(tlMsg);
  }
  function setTimelineDone() {
    const tlMsg = currentTlMsg();
    if (!tlMsg) return;
    tlMsg.tl.done = true;
    refreshTimeline(tlMsg);
  }
  function appendToolEvent(evt) {
    const tlMsg = currentTlMsg();
    if (!tlMsg) return;
    const out = String(evt.output || "").slice(0, 180);
    const existing = tlMsg.tl.tools.find(t => t.name === evt.name);
    if (existing) {
      if (existing.output === "completed") existing.output = out;
    } else {
      tlMsg.tl.tools.push({ name: evt.name, output: out });
    }
    refreshTimeline(tlMsg);
  }

  function bindResultActions() {
    $$(".resp-actions .chip").forEach(chip => chip.addEventListener("click", () => {
      const act = chip.dataset.act;
      const actions = {
        edit: () => toast("Edit", "Document opened in the editor (demo)", "ok"),
        pdf: () => toast("Export PDF", "Monthly_Project_Report.pdf generated", "ok"),
        word: () => toast("Export Word", "Monthly_Project_Report.docx generated", "ok"),
        ppt: () => toast("Presentation", "Pitch deck outline created", "ok"),
        email: () => openApproval({ title: "Send the generated report", recipient: "client@example.com", attachment: "Monthly_Project_Report.pdf" }),
        save: () => { toast("Saved to Knowledge", "Added to Templates in the Knowledge Center", "ok"); },
      };
      (actions[act] || (() => {}))();
    }));
  }

  /* ---------------- Approval modal ---------------- */
  function openApproval(req) {
    openModal(
      '<div class="modal-head"><span style="font-size:20px">⚠️</span><div class="modal-title">Approval Required</div></div>' +
      '<div class="modal-body">' +
      '<div class="approval-box">' +
      '<div class="approval-action">' + esc(req.title) + '</div>' +
      '<div class="approval-detail"><b>Recipient:</b> ' + esc(req.recipient || "—") + '<br><b>Attachment:</b> ' + esc(req.attachment || "—") + '</div>' +
      '<div class="muted small">Only approve actions you recognise. Sensitive actions are never sent without your confirmation.</div>' +
      '</div></div>' +
      '<div class="modal-foot"><button class="btn" id="apvCancel">Cancel</button><button class="btn btn-primary" id="apvOk">✅ Approve & Send</button></div>'
    );
    $("#apvCancel").addEventListener("click", () => { closeModal(); toast("Approval cancelled", "No email was sent.", "warn"); });
    $("#apvOk").addEventListener("click", () => {
      closeModal();
      toast("Email sent", "Report sent to " + esc(req.recipient), "ok");
      const n = { id: "n" + Date.now(), kind: "update", title: "Email sent", desc: "Project report delivered to " + req.recipient, time: "just now", read: false };
      state.notifications.unshift(n); persist(); updateNotifPing();
    });
  }

  /* ---------------- Agents view ---------------- */
  function agents(content) {
    content.innerHTML = `
      <div class="page">
        <div class="page-header"><div class="page-title">Agents</div><div class="page-desc">Delegate work to specialised AI agents. Auto mode picks the right one for you.</div></div>
        <div class="toolbar">
          <div class="search-input"><span>🔍</span><input id="agentSearch" placeholder="Filter agents…" aria-label="Filter agents"></div>
          <button class="btn" id="testAgentBtn">▶ Test selected</button>
        </div>
        <div class="grid-cards" id="agentCards">
          ${DB.agents.map(a => `
            <div class="card hoverable agent-card" data-agent="${a.key}">
              <div class="ac-head"><span class="avatar ai lg">${a.icon}</span>
                <div class="grow"><div class="ac-name">${esc(a.label)}</div><div class="ac-tag">${esc(a.tag)}</div></div>
                <span class="badge ok">● Online</span>
              </div>
              <div class="ac-desc">${esc(a.desc)}</div>
              <div class="ac-caps">${a.caps.map(c => '<span class="badge">' + c + '</span>').join("")}</div>
              <div class="row"><button class="btn btn-sm btn-soft" data-chat="${a.key}">💬 Chat</button><button class="btn btn-sm btn-ghost" data-test="${a.key}">Run test</button></div>
            </div>`).join("")}
        </div>
      </div>`;
    const search = $("#agentSearch");
    search.addEventListener("input", () => {
      const q = search.value.toLowerCase();
      $$("#agentCards .agent-card").forEach(c => {
        c.style.display = c.textContent.toLowerCase().includes(q) ? "" : "none";
      });
    });
    $$("[data-chat]").forEach(b => b.addEventListener("click", () => { state.activeAgent = b.dataset.chat; localStorage.setItem("acc-agent", state.activeAgent); state.chat = []; navigate("chat"); }));
    $$("[data-test]").forEach(b => b.addEventListener("click", () => testAgent(b.dataset.test)));
    $("#testAgentBtn").addEventListener("click", () => testAgent(state.activeAgent));
  }
  function testAgent(key) {
    state.activeAgent = key;
    state.chat = [];
    navigate("chat");
    setTimeout(() => {
      const task = { "finance": "Summarise this month's finance ledger and flag any cash-flow risks.", "jobsearch": "List the skill keywords detected in this text: 'Python, FastAPI, Docker, AWS, Jira'.", "exec": "List the 2 most recent job-related emails in my inbox. Do not send anything.", "commander": "What is today's date? Use the get_time tool." }[key] || "Introduce yourself and describe what you can do for me.";
      addUserMessage(task);
      runAgentTask(task);
    }, 60);
  }

  /* ---------------- Tasks view ---------------- */
  function tasks(content) {
    const counts = statusCounts();
    content.innerHTML = `
      <div class="page">
        <div class="page-header spread">
          <div><div class="page-title">Tasks</div><div class="page-desc">Monitor, pause, resume and retry delegated work.</div></div>
          <button class="btn btn-primary" id="newTaskBtn">+ New Task</button>
        </div>
        <div class="toolbar">
          <div class="seg" id="taskFilter">
            ${["All","Running","Waiting","Completed","Failed","Scheduled"].map(s => '<button data-f="' + s + '" class="' + (s==="All"?"sel":"") + '">' + s + '</button>').join("")}
          </div>
        </div>
        <div class="table-wrap">
          <table class="tbl">
            <thead><tr><th>Task</th><th>Agent</th><th>Status</th><th>Priority</th><th>Created</th><th style="text-align:right">Actions</th></tr></thead>
            <tbody id="taskRows"></tbody>
          </table>
        </div>
        <div class="empty hidden" id="taskEmpty"><div class="empty-ic">📋</div><h3>No tasks here</h3><p>Create a task and delegate it to an agent.</p><button class="btn btn-primary" id="taskEmptyBtn" style="margin-top:10px">+ New Task</button></div>
      </div>`;
    renderTaskRows("All");
    $$("#taskFilter button").forEach(b => b.addEventListener("click", () => { $$("#taskFilter button").forEach(x => x.classList.remove("sel")); b.classList.add("sel"); renderTaskRows(b.dataset.f); }));
    $("#newTaskBtn").addEventListener("click", openTaskModal);
    $("#taskEmptyBtn").addEventListener("click", openTaskModal);
    renderTasksState();
  }
  function statusCounts() {
    const c = {};
    state.tasks.forEach(t => c[t.status] = (c[t.status] || 0) + 1);
    return c;
  }
  let tasksTimer = null;
  function renderTasksState() {
    if (tasksTimer) return;
    tasksTimer = setInterval(() => {
      state.tasks.forEach(t => { if (t.status === "run" && t.progress < 96) t.progress = Math.min(96, t.progress + Math.floor(Math.random() * 6)); });
      const rows = $("#taskRows");
      if (rows && state.view === "tasks") renderTaskRows($(".seg button.sel")?.dataset.f || "All");
    }, 2500);
  }
  function taskBadge(status) {
    return { done: '<span class="badge ok">🟢 Completed</span>', run: '<span class="badge run">🔵 Running</span>', wait: '<span class="badge wait">🟡 Waiting for Approval</span>', fail: '<span class="badge err">🔴 Failed</span>', sched: '<span class="badge sched">⚪ Scheduled</span>' }[status] || status;
  }
  function renderTaskRows(filter) {
    const rows = $("#taskRows");
    if (!rows) return;
    const list = state.tasks.filter(t => filter === "All" || statusMatch(t.status, filter));
    if (!list.length) { $("#taskEmpty").classList.remove("hidden"); $("#taskEmpty").style.display = "flex"; }
    else { $("#taskEmpty").classList.add("hidden"); $("#taskEmpty").style.display = ""; }
    rows.innerHTML = list.map(t => {
      const pct = t.status === "done" ? 100 : t.status === "sched" ? 0 : t.progress;
      return '<tr data-task="' + t.id + '">' +
        '<td><b>' + esc(t.title) + '</b><br><div class="row" style="gap:5px;margin-top:5px"><span style="font-size:11px;color:var(--text-3)">' + pct + '%</span><div style="flex:1;max-width:140px;height:5px;background:var(--surface-3);border-radius:4px;overflow:hidden"><div style="height:100%;width:' + pct + '%;background:var(--accent)"></div></div></div></td>' +
        '<td class="muted">' + esc(t.agent) + '</td>' +
        '<td>' + taskBadge(t.status) + '</td>' +
        '<td><span class="badge">' + t.priority + '</span></td>' +
        '<td class="muted">' + t.created + '</td>' +
        '<td><div class="row" style="justify-content:flex-end">' +
        '<button class="btn btn-ghost btn-sm" data-act="view" title="View">👁</button>' +
        (t.status === "run" ? '<button class="btn btn-ghost btn-sm" data-act="pause" title="Pause">⏸</button>' : t.status === "sched" || t.status === "wait" || t.status === "done" ? '<button class="btn btn-ghost btn-sm" data-act="resume" title="Resume">▶</button>' : "") +
        '<button class="btn btn-ghost btn-sm" data-act="retry" title="Retry">↻</button>' +
        '<button class="btn btn-ghost btn-sm" data-act="cancel" title="Cancel">✕</button>' +
        '</div></td></tr>';
    }).join("");
    $$("#taskRows tr").forEach(tr => {
      const t = state.tasks.find(x => x.id === tr.dataset.task);
      $$("button", tr).forEach(b => b.addEventListener("click", () => taskAction(b.dataset.act, t)));
      tr.addEventListener("click", e => { if (e.target.closest("button")) return; openTaskDetail(t); });
    });
  }
  function statusMatch(st, filter) {
    const m = { "Running": "run", "Waiting": "wait", "Completed": "done", "Failed": "fail", "Scheduled": "sched" };
    return m[filter] === st;
  }
  function taskAction(act, t) {
    if (act === "view") openTaskDetail(t);
    else if (act === "pause") { t.status = "sched"; t.progress = t.progress; toast("Paused", "Task paused — will resume on schedule.", "warn"); }
    else if (act === "resume") { t.status = "run"; toast("Resumed", "Task is running again.", "ok"); }
    else if (act === "retry") { t.status = "run"; t.progress = 10; toast("Retrying", "Task restarted.", "ok"); }
    else if (act === "cancel") { t.status = "fail"; toast("Cancelled", "Task stopped.", "warn"); }
    persist();
    render();
  }
  function openTaskDetail(t) {
    openModal(
      '<div class="modal-head"><div class="modal-title">' + esc(t.title) + '</div></div>' +
      '<div class="modal-body">' +
      '<div class="row" style="gap:14px;flex-wrap:wrap">' +
      '<div class="field" style="flex:1;min-width:120px"><label>Agent</label><span>' + esc(t.agent) + '</span></div>' +
      '<div class="field" style="flex:1;min-width:120px"><label>Status</label><span>' + taskBadge(t.status) + '</span></div>' +
      '<div class="field" style="flex:1;min-width:120px"><label>Priority</label><span>' + t.priority + '</span></div>' +
      '<div class="field" style="flex:1;min-width:120px"><label>Created</label><span class="muted">' + t.created + '</span></div></div>' +
      '<div class="field"><label>Progress</label><div class="row"><span>' + (t.status === "done" ? 100 : t.progress) + '%</span><div style="flex:1;height:8px;background:var(--surface-3);border-radius:5px;overflow:hidden"><div style="height:100%;width:' + (t.status==="done"?100:t.progress) + '%;background:var(--accent);transition:width .4s"></div></div></div></div>' +
      '<div class="field"><label>Latest activity</label><div class="tl-detail" style="border:none;padding:0">' +
      '<div class="tl-tool"><span class="tt-name">⚙ agent</span><span class="muted">assigned</span></div>' +
      '<div class="tl-tool"><span class="tt-name">⚙ context</span><span class="muted">gathered from knowledge base</span></div>' +
      (t.status === "fail" ? '<div class="tl-tool"><span class="tt-name" style="color:var(--err)">✕ failed</span><span class="muted">connection to external service timed out</span></div>' : '<div class="tl-tool"><span class="tt-name">⚙ tool</span><span class="muted">executing…</span></div>') +
      '</div></div></div>' +
      '<div class="modal-foot"><button class="btn btn-ghost" data-dup="1">Duplicate</button>' +
      (t.status === "fail" ? '<button class="btn btn-primary" data-retry="1">↻ Retry</button>' : '<button class="btn btn-primary" data-close="1">Close</button>') +
      '</div>'
    );
    const foot = $(".modal-foot", $("#modalOverlay"));
    const okBtn = $("[data-retry]", foot);
    if (okBtn) okBtn.addEventListener("click", () => { t.status = "run"; t.progress = 10; persist(); closeModal(); render(); toast("Retrying", "Task restarted.", "ok"); });
    const dup = $("[data-dup]", foot);
    if (dup) dup.addEventListener("click", () => { state.tasks.push({ ...t, id: "t" + Date.now(), status: "sched", created: "just now", progress: 0 }); persist(); closeModal(); render(); toast("Duplicated", "A copy of the task was created.", "ok"); });
    const close = $("[data-close]", foot);
    if (close) close.addEventListener("click", closeModal);
  }
  function openTaskModal() {
    openModal(
      '<div class="modal-head"><div class="modal-title">Create Task</div></div>' +
      '<div class="modal-body">' +
      '<div class="field"><label for="ttTitle">Task</label><input id="ttTitle" placeholder="e.g. Generate weekly project status report"></div>' +
      '<div class="row" style="gap:14px"><div class="field grow"><label for="ttAgent">Agent</label><select id="ttAgent">' + DB.agents.map(a => '<option value="' + a.key + '">' + a.label + '</option>').join("") + '</select></div>' +
      '<div class="field grow"><label for="ttPri">Priority</label><select id="ttPri"><option>High</option><option>Med</option><option>Low</option></select></div></div>' +
      '<div class="field"><label>Schedule</label><div class="row"><label class="switch"><input type="checkbox" id="ttSched"><span class="sl"></span></label><span class="muted small">Run now (off) or schedule (on)</span></div></div>' +
      '</div>' +
      '<div class="modal-foot"><button class="btn" id="ttCancel">Cancel</button><button class="btn btn-primary" id="ttCreate">Create Task</button></div>'
    );
    $("#ttCancel").addEventListener("click", closeModal);
    $("#ttCreate").addEventListener("click", () => {
      const title = $("#ttTitle").value.trim();
      if (!title) { toast("Missing title", "Give the task a name first.", "err"); return; }
      const agent = agentMeta($("#ttAgent").value).label;
      const sched = $("#ttSched").checked;
      state.tasks.unshift({ id: "t" + Date.now(), title, agent, status: sched ? "sched" : "run", priority: $("#ttPri").value, created: "just now", progress: sched ? 0 : 8 });
      persist(); closeModal(); render();
      toast("Task created", title, "ok");
    });
  }

  /* ---------------- Automations ---------------- */
  function automations(content) {
    content.innerHTML = `
      <div class="page">
        <div class="page-header spread">
          <div><div class="page-title">Automations</div><div class="page-desc">Describe a routine and let the AI turn it into an automation.</div></div>
          <button class="btn btn-primary" id="newAutoBtn">+ New Automation</button>
        </div>
        <div class="card" style="margin-bottom:20px">
          <div class="field" style="margin:0"><label for="autoNL">Describe your automation in plain words</label>
          <div class="row"><input id="autoNL" placeholder='e.g. "Every Monday morning, prepare a project status summary and send it to me."' aria-label="Automation description">
          <button class="btn btn-primary" id="autoCreateBtn">Create with AI</button></div>
          <div class="hint">The AI will detect the trigger, actions and schedule automatically.</div></div>
        </div>
        <div class="grid-cards" id="autoCards"></div>
        <div class="empty hidden" id="autoEmpty"><div class="empty-ic">⚡</div><h3>No automations yet</h3><p>Automate repetitive work and let your AI handle it.</p></div>
      </div>`;
    renderAutoCards();
    $("#newAutoBtn").addEventListener("click", openAutomationModal);
    $("#autoCreateBtn").addEventListener("click", () => {
      const text = $("#autoNL").value.trim();
      if (!text) { toast("Describe it first", "Tell the AI what to automate.", "err"); return; }
      openAutomationModal(null, text);
    });
  }
  function renderAutoCards() {
    const wrap = $("#autoCards");
    if (!wrap) return;
    if (!state.automations.length) { $("#autoEmpty").classList.remove("hidden"); $("#autoEmpty").style.display = "flex"; }
    else { $("#autoEmpty").classList.add("hidden"); $("#autoEmpty").style.display = ""; }
    wrap.innerHTML = state.automations.map(a => `
      <div class="card">
        <div class="spread"><b style="font-size:14.5px">${esc(a.name)}</b><span class="badge ${a.status==="active"?"ok":a.status==="paused"?"wait":"sched"}">${a.status === "active" ? "🟢 Active" : a.status === "paused" ? "🟡 Paused" : "⚪ Disabled"}</span></div>
        <div class="auto-trigger" style="margin-top:11px"><span>${a.trigger.icon}</span><span class="muted small">Trigger</span><b class="grow">${esc(a.trigger.label)}</b></div>
        <div style="margin-top:10px">
          ${a.steps.map((s, i) => '<div class="auto-step"><span class="st-num">' + (i + 1) + '</span><span>' + esc(s) + '</span>' + (i < a.steps.length - 1 ? '<span class="st-line"></span>' : "") + '</div>').join("")}
        </div>
        <div class="spread muted small" style="margin-top:12px"><span>${a.runs} runs</span><span>Last: ${esc(a.last)}</span></div>
        <div class="row" style="margin-top:12px">
          <button class="btn btn-sm btn-soft" data-act="run">▶ Run now</button>
          <button class="btn btn-sm" data-act="toggle">${a.status === "active" ? "⏸ Pause" : "▶ Resume"}</button>
          <button class="btn btn-sm" data-act="edit">✏️</button>
          <button class="btn btn-sm btn-danger" data-act="del">🗑</button>
        </div>
      </div>`).join("");
    $$("#autoCards .card").forEach(card => {
      const id = state.automations.findIndex(a => card.textContent.includes(a.name));
      const a = state.automations[id];
      $$("button", card).forEach(b => b.addEventListener("click", () => {
        const act = b.dataset.act;
        if (act === "run") { toast("Automation triggered", a.name + " is running now.", "ok"); const n = { id: "n" + Date.now(), kind: "update", title: "Automation started", desc: a.name, time: "just now", read: false }; state.notifications.unshift(n); persist(); }
        else if (act === "toggle") { a.status = a.status === "active" ? "paused" : "active"; persist(); renderAutoCards(); }
        else if (act === "edit") openAutomationModal(a);
        else if (act === "del") { state.automations.splice(id, 1); persist(); renderAutoCards(); toast("Deleted", a.name + " removed.", "warn"); }
      }));
    });
  }
  function openAutomationModal(existing, nlText) {
    const a = existing || { name: "", trigger: { label: "", icon: "🗓️" }, steps: [], status: "paused" };
    openModal(
      '<div class="modal-head"><div class="modal-title">' + (existing ? "Edit Automation" : "Create Automation") + '</div></div>' +
      '<div class="modal-body">' +
      (nlText ? '<div class="insight" style="margin-bottom:14px"><span class="ic">🤖</span><span>I understood: <b>' + esc(nlText.slice(0, 70)) + '</b>…</span></div>' : "") +
      '<div class="field"><label>Name</label><input id="auName" value="' + esc(a.name) + '" placeholder="e.g. Weekly Project Report"></div>' +
      '<div class="field"><label>Trigger</label><input id="auTrigger" value="' + esc(a.trigger.label) + '" placeholder="Every Monday — 9:00 AM"></div>' +
      '<div class="field"><label>Actions</label><textarea id="auSteps" rows="4" placeholder="One action per line">' + esc(a.steps.join("\n")) + '</textarea></div>' +
      '<div class="hint">The AI converts natural language into a schedule and ordered steps. Edit anything before saving.</div>' +
      '</div>' +
      '<div class="modal-foot"><button class="btn" id="auCancel">Cancel</button><button class="btn btn-primary" id="auSave">' + (existing ? "Save Changes" : "Create Automation") + '</button></div>'
    );
    $("#auCancel").addEventListener("click", closeModal);
    $("#auSave").addEventListener("click", () => {
      const name = $("#auName").value.trim() || "New Automation";
      const trigger = $("#auTrigger").value.trim() || "Custom schedule";
      const steps = $("#auSteps").value.split("\n").map(s => s.trim()).filter(Boolean);
      const obj = { name, trigger: { label: trigger, icon: "🗓️" }, steps: steps.length ? steps : ["Run the task"], status: "active" };
      if (existing) Object.assign(existing, obj);
      else state.automations.unshift({ ...obj, id: "a" + Date.now(), runs: 0, last: "Never" });
      persist(); closeModal(); render();
      toast(existing ? "Automation updated" : "Automation created", name + (existing ? "" : " is now active."), "ok");
    });
  }

  /* ---------------- Files ---------------- */
  const FILE_TABS = ["All", "Documents", "PDFs", "Excel", "CSV", "Presentations", "Images", "Code", "Other"];
  function files(content) {
    content.innerHTML = `
      <div class="page">
        <div class="page-header spread">
          <div><div class="page-title">Files</div><div class="page-desc">Upload, preview and analyze files with AI.</div></div>
          <button class="btn btn-primary" id="fileUpBtn">📁 Upload File</button>
        </div>
        <div class="dropzone" id="dropzone"><div class="dz-ic">📂</div><div><b>Drag & drop files here</b> or click to browse</div><div class="muted small" style="margin-top:4px">PDF, DOCX, XLSX, CSV, PPTX, images, code…</div></div>
        <input type="file" id="fileInput" class="hidden" multiple>
        <div class="toolbar" style="margin-top:18px">
          <div class="seg" id="fileFilter">${FILE_TABS.map(t => '<button data-f="' + t + '" class="' + (t === "All" ? "sel" : "") + '">' + t + '</button>').join("")}</div>
        </div>
        <div class="grid-cards" id="fileCards"></div>
        <div class="empty hidden" id="fileEmpty"><div class="empty-ic">📁</div><h3>No files yet</h3><p>Upload documents, spreadsheets or PDFs and let AI work with them.</p><button class="btn btn-primary" id="fileEmptyBtn" style="margin-top:10px">Upload File</button></div>
      </div>`;
    renderFileCards("All");
    bindFileUpload();
    $("#fileUpBtn").addEventListener("click", () => $("#fileInput").click());
    $("#fileEmptyBtn").addEventListener("click", () => $("#fileInput").click());
    $$("#fileFilter button").forEach(b => b.addEventListener("click", () => { $$("#fileFilter button").forEach(x => x.classList.remove("sel")); b.classList.add("sel"); renderFileCards(b.dataset.f); }));
  }
  function bindFileUpload() {
    const dz = $("#dropzone");
    const input = $("#fileInput");
    dz.addEventListener("click", () => input.click());
    dz.addEventListener("dragover", e => { e.preventDefault(); dz.classList.add("drag"); });
    dz.addEventListener("dragleave", () => dz.classList.remove("drag"));
    dz.addEventListener("drop", e => { e.preventDefault(); dz.classList.remove("drag"); handleFiles(e.dataTransfer.files); });
    input.addEventListener("change", () => { handleFiles(input.files); input.value = ""; });
  }
  function handleFiles(files) {
    Array.from(files).forEach((f, i) => {
      const ext = (f.name.split(".").pop() || "txt").toLowerCase();
      const kind = { pdf: "pdf", docx: "doc", doc: "doc", xlsx: "xls", xls: "xls", csv: "csv", pptx: "ppt", ppt: "ppt", png: "img", jpg: "img", jpeg: "img", gif: "img", svg: "img", py: "code", js: "code", ts: "code", md: "txt", txt: "txt" }[ext] || "other";
      const size = f.size > 1048576 ? (f.size / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(f.size / 1024)) + " KB";
      state.files.unshift({ id: "f" + Date.now() + i, name: f.name, kind, size, updated: "just now", path: "uploads/" + f.name });
    });
    persist();
    if (state.view === "files") { renderFileCards("All"); }
    toast("Files uploaded", files.length + " file" + (files.length > 1 ? "s" : "") + " added.", "ok");
  }
  function renderFileCards(filter) {
    const wrap = $("#fileCards");
    if (!wrap) return;
    const list = state.files.filter(f => filter === "All" || f.kind === filter.toLowerCase());
    if (!list.length) { $("#fileEmpty").classList.remove("hidden"); $("#fileEmpty").style.display = "flex"; }
    else { $("#fileEmpty").classList.add("hidden"); $("#fileEmpty").style.display = ""; }
    wrap.innerHTML = list.map(f => `
      <div class="card hoverable file-card" data-file="${f.id}">
        <div class="file-ic ${DB.fileClass(f.kind)}">${DB.fileIcon(f.kind)}</div>
        <div class="grow"><div class="file-name">${esc(f.name)}</div><div class="file-meta">${f.size} · ${esc(f.updated)}</div>
        <div class="resp-actions" style="margin-top:8px">
          <button class="chip" data-act="preview">👁 Preview</button>
          <button class="chip" data-act="summarize">✨ Summarize</button>
          <button class="chip" data-act="analyze">📊 Analyze</button>
        </div></div>
      </div>`).join("");
    $$("#fileCards .file-card").forEach(card => {
      const f = state.files.find(x => x.id === card.dataset.file);
      $$("button", card).forEach(b => b.addEventListener("click", () => {
        const act = b.dataset.act;
        if (act === "preview") openFilePreview(f);
        else if (act === "summarize" || act === "analyze") analyzeFile(f, act === "analyze");
      }));
      card.addEventListener("click", e => { if (e.target.closest("button")) return; openFilePreview(f); });
    });
  }
  function openFilePreview(f) {
    openModal(
      '<div class="modal-head"><div class="modal-title">' + esc(f.name) + '</div></div>' +
      '<div class="modal-body"><div class="row" style="gap:14px"><div class="file-ic ' + DB.fileClass(f.kind) + '" style="width:52px;height:52px;font-size:24px">' + DB.fileIcon(f.kind) + '</div>' +
      '<div class="grow"><div class="muted small">Size</div><b>' + f.size + '</b><div class="muted small" style="margin-top:4px">Location</div><b>' + esc(f.path) + '</b></div></div>' +
      '<div class="tl-detail" style="border:none;padding:0;margin-top:12px">' +
      '<div class="tl-tool"><span class="tt-name">👁 Preview</span><span class="muted">' + DB.fileIcon(f.kind) + ' file preview is available</span></div></div>' +
      '<div class="resp-actions" style="margin-top:12px">' +
      '<button class="chip" data-act="sum">✨ Summarize</button><button class="chip" data-act="ext">📋 Extract info</button><button class="chip" data-act="ppt">📑 Make presentation</button><button class="chip" data-act="cmp">🔗 Compare</button></div>' +
      '</div>' +
      '<div class="modal-foot"><button class="btn" id="fpClose">Close</button><button class="btn btn-primary" id="fpDown">⬇ Download</button></div>'
    );
    $("#fpClose").addEventListener("click", closeModal);
    $("#fpDown").addEventListener("click", () => { closeModal(); toast("Downloading", f.name, "ok"); });
    $$(".resp-actions .chip").forEach(c => c.addEventListener("click", () => { const act = c.dataset.act; const map = { sum: ["Summarizing", f.name + " — summary ready"], ext: ["Extracted", "Key info from " + f.name], ppt: ["Presentation", "Slide deck from " + f.name + " created"], cmp: ["Compare", "Ready to compare with another file"] }; const [t, m] = map[act]; closeModal(); toast(t, m, "ok"); }));
  }
  function analyzeFile(f, deep) {
    const task = (deep ? "Analyze this file and give key insights, trends and recommended actions: " : "Summarize the key points of this file: ") + f.name;
    state.activeAgent = deep ? "finance" : "docs";
    localStorage.setItem("acc-agent", state.activeAgent);
    state.chat = [];
    navigate("chat");
    setTimeout(() => { addUserMessage(task); runAgentTask(task); }, 60);
  }

  /* ---------------- Analytics ---------------- */
  function analytics(content) {
    content.innerHTML = `
      <div class="page">
        <div class="page-header"><div class="page-title">Analytics</div><div class="page-desc">Project, finance and activity insights generated from your agents' work.</div></div>
        <div class="dash-grid">
          ${kpi("Revenue", "₹ 42,800", "▲ 18% vs last month", "up")}
          ${kpi("Projects", "6", "▲ 1 new", "up")}
          ${kpi("Completion", "78%", "▲ 5%", "up")}
          ${kpi("Open risks", "4", "▼ 2 resolved", "down")}
        </div>
        <div class="grid-2" style="margin-top:16px">
          <div class="card"><div class="card-title">Project completion trend</div><div class="chart-box" id="lineChart"></div></div>
          <div class="card"><div class="card-title">Budget by category</div><div class="chart-box" id="donutChart"></div></div>
        </div>
        <div class="section-title">Key insights</div>
        <div class="col">
          <div class="insight"><span class="ic">💡</span><span>Project completion increased by <b>18%</b> compared with last month — driven by DPIIC milestone delivery.</span></div>
          <div class="insight"><span class="ic">⚠️</span><span>Two high-priority tasks are waiting for approval. Resolve them to avoid a 3-day slip.</span></div>
        </div>
        <div class="section-title">Recommended actions</div>
        <div class="grid-3">
          ${[["1", "Review delayed activities", "Three sub-tasks on DPIIC are past due."], ["2", "Escalate high-risk items", "API integration risk needs owner attention."], ["3", "Reallocate resources", "Shift design hours to the HCLTech response."]].map(([n, t, d]) => '<div class="card"><div class="row"><span class="st-num">' + n + '</span><b>' + t + '</b></div><div class="card-sub" style="margin-top:6px">' + d + '</div></div>').join("")}
        </div>
      </div>`;
    drawLine($("#lineChart"));
    drawDonut($("#donutChart"));
  }
  function drawLine(el) {
    const w = 460, h = 190, p = { l: 34, r: 10, t: 12, b: 24 };
    const data = [42, 48, 45, 52, 58, 63, 61, 70, 78];
    const labels = ["Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"];
    const iw = w - p.l - p.r, ih = h - p.t - p.b;
    const min = 0, max = 100;
    const x = i => p.l + (i / (data.length - 1)) * iw;
    const y = v => p.t + ih - ((v - min) / (max - min)) * ih;
    let svg = '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="100%" preserveAspectRatio="none" role="img" aria-label="Project completion trend chart">';
    svg += '<defs><linearGradient id="lg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent)" stop-opacity=".25"/><stop offset="1" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>';
    for (let g = 0; g <= 100; g += 25) svg += '<line x1="' + p.l + '" y1="' + y(g) + '" x2="' + (w - p.r) + '" y2="' + y(g) + '" stroke="var(--border)" stroke-dasharray="3 3"/>';
    const pts = data.map((v, i) => x(i) + "," + y(v)).join(" ");
    svg += '<polygon points="' + pts + ' ' + x(data.length - 1) + ',' + (p.t + ih) + ' ' + x(0) + ',' + (p.t + ih) + '" fill="url(#lg)"/>';
    svg += '<polyline points="' + pts + '" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>';
    data.forEach((v, i) => { svg += '<circle cx="' + x(i) + '" cy="' + y(v) + '" r="3.5" fill="var(--surface)" stroke="var(--accent)" stroke-width="2"/>'; });
    svg += labels.map((l, i) => '<text x="' + x(i) + '" y="' + (h - 6) + '" text-anchor="middle" font-size="10" fill="var(--text-3)">' + l + '</text>').join("");
    svg += '</svg>';
    el.innerHTML = svg;
  }
  function drawDonut(el) {
    const data = [38, 26, 18, 18];
    const colors = ["var(--accent)", "#9333ea", "#0ea5e9", "#f0a645"];
    const labels = ["Development", "Infrastructure", "Marketing", "Other"];
    const r = 62, c = 110, R = 70;
    const cx = c, cy = c;
    let svg = '<svg viewBox="0 0 ' + (c * 2) + ' ' + (c * 2) + '" width="100%" height="100%" role="img" aria-label="Budget by category">';
    let a0 = -90;
    data.forEach((v, i) => {
      const a1 = a0 + (v / 100) * 360;
      const large = (a1 - a0) > 180 ? 1 : 0;
      const x0 = cx + r * Math.cos(a0 * Math.PI / 180), y0 = cy + r * Math.sin(a0 * Math.PI / 180);
      const x1 = cx + r * Math.cos(a1 * Math.PI / 180), y1 = cy + r * Math.sin(a1 * Math.PI / 180);
      svg += '<path d="M ' + cx + ' ' + cy + ' L ' + x0 + ' ' + y0 + ' A ' + r + ' ' + r + ' 0 ' + large + ' 1 ' + x1 + ' ' + y1 + ' Z" fill="' + colors[i] + '"/>';
      a0 = a1;
    });
    svg += '<circle cx="' + cx + '" cy="' + cy + '" r="42" fill="var(--surface)"/>';
    svg += '<text x="' + cx + '" y="' + (cy + 4) + '" text-anchor="middle" font-size="16" font-weight="800" fill="var(--text)">78%</text>';
    svg += '</svg>';
    el.innerHTML = svg;
    const legend = labels.map((l, i) => '<div class="row small" style="gap:6px"><span class="dot" style="background:' + colors[i] + '"></span>' + l + ' <b class="grow text-right">' + data[i] + '%</b></div>').join("");
    el.insertAdjacentHTML("beforeend", '<div style="margin-top:8px">' + legend + '</div>');
  }

  /* ---------------- Documents ---------------- */
  function documents(content) {
    content.innerHTML = `
      <div class="page">
        <div class="page-header spread">
          <div><div class="page-title">Documents</div><div class="page-desc">Generated reports, presentations and exports — ready to preview, share and export.</div></div>
          <button class="btn btn-primary" id="genDocBtn">+ Generate Document</button>
        </div>
        <div class="grid-cards" id="docCards"></div>
        <div class="empty hidden" id="docEmpty"><div class="empty-ic">📄</div><h3>No documents yet</h3><p>Ask the Documents agent to generate reports and presentations.</p><button class="btn btn-primary" id="docEmptyBtn" style="margin-top:10px">Generate</button></div>
      </div>`;
    renderDocCards();
    $("#genDocBtn").addEventListener("click", () => navigate("chat"));
    $("#docEmptyBtn").addEventListener("click", () => navigate("chat"));
  }
  function renderDocCards() {
    const wrap = $("#docCards");
    if (!wrap) return;
    if (!state.documents.length) { $("#docEmpty").classList.remove("hidden"); $("#docEmpty").style.display = "flex"; }
    else { $("#docEmpty").classList.add("hidden"); $("#docEmpty").style.display = ""; }
    wrap.innerHTML = state.documents.map(d => `
      <div class="card">
        <div class="row"><div class="file-ic ${DB.fileClass(d.kind)}">${DB.fileIcon(d.kind)}</div><div class="grow"><div class="file-name">${esc(d.title)}</div><div class="file-meta">${d.size} · updated ${esc(d.updated)}</div></div></div>
        <div class="resp-actions" style="margin-top:12px">
          <button class="chip" data-act="preview">👁 Preview</button>
          <button class="chip" data-act="pdf">📄 PDF</button>
          <button class="chip" data-act="word">📝 Word</button>
          <button class="chip" data-act="share">📤 Share</button>
          <button class="chip" data-act="edit">✏️ Edit</button>
        </div>
      </div>`).join("");
    $$("#docCards .card").forEach(card => {
      const d = state.documents.find(x => card.textContent.includes(x.title));
      $$("button", card).forEach(b => b.addEventListener("click", () => {
        const act = b.dataset.act;
        const map = { preview: ["Preview", d.title + " opened in viewer"], pdf: ["Export PDF", d.title + ".pdf generated"], word: ["Export Word", d.title + ".docx generated"], share: ["Share", "Share link copied"], edit: ["Edit", "Editor opened (demo)"] };
        const [t, m] = map[act];
        toast(t, m, "ok");
      }));
    });
  }

  /* ---------------- Knowledge ---------------- */
  function knowledge(content) {
    content.innerHTML = `
      <div class="page">
        <div class="page-header spread">
          <div><div class="page-title">Knowledge Center</div><div class="page-desc">Approved sources the AI can draw from when answering.</div></div>
          <div class="row"><button class="btn" id="addSrcBtn">＋ Add Source</button><button class="btn btn-soft" id="searchKnBtn">🔍 Search Knowledge</button></div>
        </div>
        <div class="card">
          <div class="spread" style="margin-bottom:8px"><b>Knowledge source status</b><div class="row small muted"><span class="badge ok">3 ready</span><span class="badge wait">1 updating</span><span class="badge err">1 error</span></div></div>
        </div>
        <div class="card" style="margin-top:14px;padding:0;overflow:hidden">
          ${state.knowledge.map(k => `
            <div class="kn-item">
              <div class="kn-ic">${k.icon}</div>
              <div class="grow"><b>${esc(k.name)}</b><div class="muted small">${esc(k.desc)} · ${k.count} items</div></div>
              <span class="badge ${k.status === "ready" ? "ok" : k.status === "updating" ? "run" : "err"}">${k.status === "ready" ? "🟢 Available" : k.status === "updating" ? "🟡 Updating" : "🔴 Error"}</span>
              <button class="btn btn-ghost btn-sm" data-act="view">👁</button>
            </div>`).join("")}
        </div>
        <div class="empty hidden" id="knEmpty"><div class="empty-ic">🧠</div><h3>No knowledge sources yet</h3><p>Add files or connect storage so your AI can answer from your own data.</p></div>
      </div>`;
    $("#addSrcBtn").addEventListener("click", () => { toast("Add Source", "Choose a file or connect a storage provider (demo).", "warn"); });
    $("#searchKnBtn").addEventListener("click", () => openCommand(true));
    $$("[data-act=view]").forEach(b => b.addEventListener("click", () => {
      const k = state.knowledge.find(x => b.closest(".kn-item").textContent.includes(x.name));
      toast("Knowledge source", k.name + (k.status === "error" ? " — not connected. Add a credential to sync." : " — " + k.count + " items available."), k.status === "error" ? "err" : "ok");
    }));
  }

  /* ---------------- Integrations ---------------- */
  function integrations(content) {
    const conn = DB.integrations.filter(i => i.status === "connected").length;
    content.innerHTML = `
      <div class="page">
        <div class="page-header"><div class="page-title">Integrations</div><div class="page-desc">Connect the services your agents can use. <b style="color:var(--ok)">${conn}/${DB.integrations.length} connected</b></div></div>
        <div class="card" style="padding:0;overflow:hidden">
          ${DB.integrations.map(i => `
            <div class="int-row">
              <div class="int-ic">${i.icon}</div>
              <div class="grow"><div class="int-name">${esc(i.name)}</div><div class="int-desc">${esc(i.desc)}</div><div class="small muted" style="margin-top:2px">${esc(i.detail)}</div></div>
              ${i.status === "connected"
                ? '<span class="badge ok">● Connected</span><button class="btn btn-ghost btn-sm" data-act="disconnect">Disconnect</button>'
                : '<button class="btn btn-sm btn-soft" data-act="connect">Connect</button>'}
            </div>`).join("")}
        </div>
      </div>`;
    $$("[data-act=connect]").forEach(b => b.addEventListener("click", () => {
      const name = b.closest(".int-row").querySelector(".int-name").textContent;
      openApproval({ title: "Connect " + name, recipient: name + " OAuth flow", attachment: "The app will open the provider's authorization screen." });
    }));
    $$("[data-act=disconnect]").forEach(b => b.addEventListener("click", () => {
      const name = b.closest(".int-row").querySelector(".int-name").textContent;
      toast("Disconnected", name + " removed. Reconnect any time.", "warn");
    }));
  }

  /* ---------------- Settings ---------------- */
  function settings(content) {
    const tabs = ["Account", "AI", "Appearance", "Notifications", "Privacy", "Integrations"];
    content.innerHTML = `
      <div class="page">
        <div class="page-header"><div class="page-title">Settings</div><div class="page-desc">Manage your account, AI preferences, privacy and connections.</div></div>
        <div class="row" style="margin-bottom:18px"><div class="seg" id="setTabs">${tabs.map((t, i) => '<button class="' + (i === 0 ? "sel" : "") + '" data-t="' + t + '">' + t + '</button>').join("")}</div></div>
        <div class="grid-2">
          <div class="card" id="setPanel"></div>
          <div class="col">
            <div class="card">
              <div class="card-title">Profile</div>
              <div class="row" style="margin-top:12px"><span class="avatar lg">SA</span>
                <div class="grow"><b>Sandeep</b><div class="muted small">dixitsandeep339@gmail.com</div></div>
                <button class="btn btn-sm">Edit</button></div>
            </div>
            <div class="card">
              <div class="card-title">Security</div>
              <div class="security-note" style="margin-top:10px"><span>🔒</span><span>Data is processed by your configured LLM provider and local tools. Sensitive actions (emails, external changes, payments) always require your approval.</span></div>
              <button class="btn btn-sm" style="margin-top:12px" data-open-sec="1">View details</button>
            </div>
          </div>
        </div>
      </div>`;
    renderSettingsTab("Account");
    $$("#setTabs button").forEach(b => b.addEventListener("click", () => { $$("#setTabs button").forEach(x => x.classList.remove("sel")); b.classList.add("sel"); renderSettingsTab(b.dataset.t); }));
    $$("[data-open-sec]").forEach(b => b.addEventListener("click", () => openSecurityModal()));
  }
  function renderSettingsTab(tab) {
    const panel = $("#setPanel");
    const p = { Account: () => `
      <div class="card-title">Account</div>
      <div class="field"><label>Name</label><input value="Sandeep"></div>
      <div class="field"><label>Email</label><input value="dixitsandeep339@gmail.com"></div>
      <div class="field"><label>Role</label><select><option>Project Manager</option><option>Analyst</option><option>Founder</option><option>Freelancer</option></select></div>
      <button class="btn btn-primary">Save changes</button>`,
      AI: () => `
      <div class="card-title">AI</div>
      <div class="field"><label>Default agent</label><select id="defAgent">${DB.agents.map(a => '<option value="' + a.key + '"' + (a.key === state.activeAgent ? " selected" : "") + '>' + a.label + '</option>').join("")}</select></div>
      <div class="field"><label>AI model</label><select><option>openai/gpt-oss-120b (fast)</option><option>llama-3.3-70b-versatile</option><option>llama-3.1-8b-instant</option></select><div class="hint">Provided by Groq — switch models from Agents/.env</div></div>
      <div class="field"><label>Response style</label><select><option>Concise</option><option>Balanced</option><option>Detailed</option></select></div>
      <div class="field"><label>Max tool steps</label><input type="number" value="8"></div>
      <button class="btn btn-primary" id="saveAI">Save preferences</button>`,
      Appearance: () => `
      <div class="card-title">Appearance</div>
      <div class="field"><label>Theme</label>
      <div class="seg" id="themeSeg">${["Light", "Dark", "System"].map(t => '<button data-t="' + t + '" class="' + (state.theme === t.toLowerCase() ? "sel" : "") + '">' + t + '</button>').join("")}</div></div>
      <div class="field"><label>Compact density</label><label class="switch"><input type="checkbox"><span class="sl"></span></label></div>`,
      Notifications: () => `
      <div class="card-title">Notifications</div>
      ${[["Email", true], ["Desktop", true], ["Task alerts", true], ["Automation summaries", false]].map(([n, on]) => `
        <div class="spread" style="padding:9px 0;border-bottom:1px solid var(--border)"><span>${n}</span><label class="switch"><input type="checkbox"${on ? " checked" : ""}><span class="sl"></span></label></div>`).join("")}`,
      Privacy: () => `
      <div class="card-title">Privacy</div>
      <div class="spread" style="padding:9px 0;border-bottom:1px solid var(--border)"><span>AI memory</span><label class="switch"><input type="checkbox" checked><span class="sl"></span></label></div>
      <div class="muted small" style="padding:6px 0 10px">Lets agents remember preferences and project context across conversations. You can view and delete saved items any time.</div>
      <div class="spread" style="padding:9px 0;border-bottom:1px solid var(--border)"><span>Allow web search</span><label class="switch"><input type="checkbox" checked><span class="sl"></span></label></div>
      <div class="spread" style="padding:9px 0"><span>Allow file access</span><label class="switch"><input type="checkbox" checked><span class="sl"></span></label></div>
      <button class="btn btn-danger btn-sm" style="margin-top:10px">Clear all memory</button>`,
      Integrations: () => `
      <div class="card-title">Integrations</div>
      <div class="muted small" style="padding-bottom:8px">Manage connections on the Integrations page.</div>
      <button class="btn btn-sm btn-soft" onclick="location.hash='#/integrations'">Open Integrations →</button>`,
    };
    panel.innerHTML = (p[tab] || p.Account)();
    if (tab === "Appearance") {
      $$("#themeSeg button").forEach(b => b.addEventListener("click", () => { state.theme = b.dataset.t.toLowerCase(); localStorage.setItem("acc-theme", state.theme); applyTheme(); $$("#themeSeg button").forEach(x => x.classList.remove("sel")); b.classList.add("sel"); toast("Theme", "Appearance set to " + b.dataset.t, "ok"); }));
    }
    if (tab === "AI") {
      $("#defAgent").addEventListener("change", e => { state.activeAgent = e.target.value; localStorage.setItem("acc-agent", state.activeAgent); });
      $("#saveAI").addEventListener("click", () => toast("Saved", "AI preferences updated.", "ok"));
    }
  }
  function openSecurityModal() {
    openModal(
      '<div class="modal-head"><div class="modal-title">🔒 Security details</div></div>' +
      '<div class="modal-body">' +
      '<div class="tl-detail" style="border:none;padding:0">' +
      '<div class="tl-tool"><span class="tt-name">🔐 Credentials</span><span class="muted">stored in .env, never in chat logs</span></div>' +
      '<div class="tl-tool"><span class="tt-name">📧 Email</span><span class="muted">reads inbox; sending requires explicit approval</span></div>' +
      '<div class="tl-tool"><span class="tt-name">💳 Payments</span><span class="muted">never initiated without approval</span></div>' +
      '<div class="tl-tool"><span class="tt-name">🧠 Memory</span><span class="muted">viewable and deletable in Privacy settings</span></div>' +
      '<div class="tl-tool"><span class="tt-name">🌐 Web access</span><span class="muted">only when a task needs search</span></div>' +
      '</div></div>' +
      '<div class="modal-foot"><button class="btn btn-primary" id="secClose">Got it</button></div>'
    );
    $("#secClose").addEventListener("click", closeModal);
  }

  /* ---------------- Conversation resume ---------------- */
  function openConversation(id, silent) {
    const conv = state.conversations.find(c => c.id === id);
    if (!conv) return;
    state.activeAgent = conv.agent;
    localStorage.setItem("acc-agent", state.activeAgent);
    state.chat = [];
    state.runningConvId = id;
    navigate("chat");
    if (silent) return;
    setTimeout(() => { addUserMessage("Continue the conversation about: " + conv.title); runAgentTask("Continue the conversation about: " + conv.title); }, 60);
  }

  /* ---------------- File picker in chat ---------------- */
  function openFilePicker(imageOnly) {
    const inp = document.createElement("input");
    inp.type = "file";
    inp.accept = imageOnly ? "image/*" : "*";
    inp.multiple = true;
    inp.onchange = () => {
      Array.from(inp.files).forEach(f => {
        const wrap = $("#attachList");
        const el = document.createElement("div");
        el.className = "attach-item";
        el.innerHTML = (imageOnly ? "🖼️ " : "📎 ") + esc(f.name) + ' <span class="x" data-rm="1">✕</span>';
        el.querySelector(".x").addEventListener("click", () => el.remove());
        wrap.appendChild(el);
      });
    };
    inp.click();
  }

  /* ---------------- Init ---------------- */
  document.addEventListener("DOMContentLoaded", render);
})();
