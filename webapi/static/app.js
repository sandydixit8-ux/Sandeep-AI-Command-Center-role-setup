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
    const parts = h.split("/").map(p => decodeURIComponent(p));
    return { view: parts[0] || "home", param: parts[1] ?? null, rest: parts.slice(1) };
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
    { view: "market", ic: "📈", label: "Market AI" },
    { view: "ideas", ic: "💡", label: "Idea Indicator" },
    { view: "today", ic: "📅", label: "Today Performance" },
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

    const views = { home, chat, agents, tasks, automations, files, analytics, documents, knowledge, integrations, settings, market, ideaIndicator, todayPerf };
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
          <form class="composer-inner" id="chatForm" style="position:relative" aria-label="Send a message to your AI agent">
            <textarea id="chatInput" rows="1" placeholder="Ask your AI agent…" aria-label="Message to your AI agent"></textarea>
            <div class="attach-list" id="attachList"></div>
            <div class="composer-tools">
              <button type="button" class="btn btn-icon" id="attachBtn" title="Attach file" aria-label="Attach file">📎</button>
              <button type="button" class="btn btn-icon" id="imgBtn" title="Upload image" aria-label="Upload image">🖼️</button>
              <button type="button" class="btn btn-icon" id="micBtn" title="Voice input (coming soon)" aria-label="Voice">🎤</button>
              <button type="button" class="agent-chip" id="agentPicker">🤖 <span id="agentPickerLabel"></span> ▾</button>
              <button type="submit" class="btn btn-primary composer-send" id="chatSend">Send</button>
            </div>
            <div class="selector-pop hidden" id="agentSelector"></div>
          </form>
        </div>
      </div>`;

    $("#agentPickerLabel").textContent = agentMeta(state.activeAgent).label;
    renderAgentSelector();
    $("#agentPicker").addEventListener("click", e => { e.stopPropagation(); $("#agentSelector").classList.toggle("hidden"); });
    document.addEventListener("click", () => $("#agentSelector").classList.add("hidden"), { once: true });

    const input = $("#chatInput");
    input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 180) + "px"; });
    input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat(); } });
    $("#chatForm").addEventListener("submit", e => { e.preventDefault(); sendChat(); });
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

    const apiHeaders = { "Content-Type": "application/json" };
    const tok = window.API_TOKEN || (localStorage && localStorage.getItem("api_token")) || "";
    if (tok) apiHeaders["X-API-Key"] = tok;
    fetch("/api/v1/run/stream", {
      method: "POST",
      headers: apiHeaders,
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

  /* ---------------- Idea Indicator ---------------- */
  // Plain-language layer over the signal endpoints. Every raw signal label,
  // factor and score is translated into a sentence a non-trader can read.
  function ideaVerdict(sig) {
    const s = (sig.signal || "").toUpperCase();
    if (s.includes("BUY")) return { cls: "ok", icon: "🟢", label: "Strong buy case", plain: "The factors together favour buying — this is one of the stronger setups on the board, but still size it sensibly and respect your stop loss." };
    if (s.includes("SELL")) return { cls: "err", icon: "🔴", label: "Reduce / be cautious", plain: "Most factors are against holding right now. Consider trimming exposure and waiting for a cleaner setup before acting." };
    if (s.includes("HOLD") || s.includes("WATCH")) return { cls: "wait", icon: "🟡", label: "Wait and watch", plain: "The factors are mixed — there is no clear edge today. Patience often beats forcing a trade here." };
    return { cls: "sched", icon: "⚪", label: "No clear edge", plain: "No decisive signal right now. Keep it on the watchlist and revisit when data firms up." };
  }
  function ideaConfidence(c) {
    const v = c || 0;
    if (v >= 75) return { word: "High conviction", cls: "ok" };
    if (v >= 50) return { word: "Moderate conviction", cls: "wait" };
    return { word: "Low conviction", cls: "sched" };
  }
  const IDEA_FACTOR_TEXT = [
    { re: /trend/i, buy: "price is above its moving averages — the trend is with you", sell: "price is trading below its moving averages — the trend is against you" },
    { re: /momentum|rsi/i, buy: "RSI reads healthy, not overheated — momentum favours strength", sell: "RSI is weak / losing momentum — it favours weakness" },
    { re: /volume/i, buy: "volume is expanding with the move — the rally has participation", sell: "volume is shrinking / not confirming — the move lacks fuel" },
    { re: /fundament/i, buy: "valuation and profitability look attractive", sell: "valuation looks stretched or profitability is soft" },
    { re: /sentiment/i, buy: "news and sentiment tone is positive", sell: "news and sentiment tone is cautious" },
  ];
  function ideaFactor(factor, support) {
    const f = IDEA_FACTOR_TEXT.find(x => x.re.test(factor || "")) || { buy: "this factor leans supportive", sell: "this factor leans negative" };
    const isBuy = (support || "").toUpperCase().includes("BUY");
    return { cls: isBuy ? "ok" : "err", text: (isBuy ? f.buy : f.sell), label: factor };
  }
  function ideaRegimePlain(r) {
    if (!r) return "";
    const reg = (r.regime || "").toLowerCase();
    if (reg.includes("bull")) return "The market is in an uptrend with broad participation — conditions generally favour the long side, though dips can still hurt.";
    if (reg.includes("bear")) return "The market is in a downtrend. Defensive positioning and tighter risk are wise until breadth improves.";
    return (r.tone || "").toLowerCase().includes("risk") ? "The market is range-bound. Directional bets have little edge here — patience and selectivity win." : "The market is range-bound. Directional bets have little edge here.";
  }
  function ideaOptLabel(label) {
    const l = String(label || "").toLowerCase();
    if (l.includes("strong") && l.includes("bull")) return { cls: "ok", text: "Option writers are strongly tilted bullish — key strikes see heavy call buying / put unwinding." };
    if (l.includes("bull")) return { cls: "ok", text: "Option writers lean mildly bullish — a modest upward tilt, treat as a tailwind not a verdict." };
    if (l.includes("strong") && l.includes("bear")) return { cls: "err", text: "Option writers are strongly tilted bearish — key strikes show put building / call unwinding." };
    if (l.includes("bear")) return { cls: "err", text: "Option writers lean mildly bearish — a modest downward tilt, worth respecting." };
    return { cls: "wait", text: "Option writers are balanced — no meaningful directional edge from the chain today." };
  }

  function ideaIndicator(content, param) {
    const page = '<div class="page" id="ideaPage">' +
      '<div class="page-header"><div class="page-title">💡 Idea Indicator</div>' +
      '<div class="page-desc">Every signal in the platform, explained in plain language — what it means, why, and how to act.</div></div>' +
      '<div id="ideaBody"><div class="empty" style="display:flex"><div class="empty-ic">🧭</div><h3>Scanning signals…</h3><p>Reading the market context and every stock signal.</p></div></div>' +
      '</div>';
    content.innerHTML = page;
    const body = $("#ideaBody");

    const scan = async () => {
      body.innerHTML = '<div class="empty" style="display:flex"><div class="empty-ic">🧭</div><h3>Scanning signals…</h3><p>Reading the market context and every stock signal.</p></div>';
      try {
        const [briefR, stocksR] = await Promise.all([
          window.MarketClient.brief().then(b => ({ ok: true, v: b })).catch(e => ({ ok: false, e })),
          window.MarketClient.stocks().then(s => ({ ok: true, v: s })).catch(e => ({ ok: false, e })),
        ]);
        const brief = briefR.ok ? briefR.v : { regime: { regime: "Sideways", tone: "Mixed", breadth: 0.5 }, summary: "", data_status: "offline", ai_interpretation: "" };
        const list = stocksR.ok ? stocksR.v : [];
        const sigs = await Promise.allSettled(list.map(s => window.MarketClient.signal(s.symbol).catch(() => ({ symbol: s.symbol, name: s.name, sector: s.sector, signal: "ERROR", confidence: 0, checks: [], evidence_strength: "Low", supporting: 0, total_factors: 5 }))));
        const scores = await Promise.allSettled(list.map(s => window.MarketClient.score(s.symbol).catch(() => null)));
        const opt = await Promise.allSettled(["NIFTY", "BANKNIFTY"].map(u => window.OptionsClient.signal(u).catch(() => null)));

        const board = list.map((s, i) => {
          const sig = sigs[i] && sigs[i].status === "fulfilled" ? sigs[i].value : null;
          const sc = scores[i] && scores[i].status === "fulfilled" ? scores[i].value : null;
          const v = ideaVerdict(sig || { signal: "NO SIGNAL" });
          const conf = ideaConfidence(sig && sig.confidence);
          const strength = (sig && sig.evidence_strength) || "Low";
          const fam = (v.cls === "ok" ? (v.label === "Strong buy case" ? 1 : 0.6) : v.cls === "err" ? -1 : 0);
          return { symbol: s.symbol, name: s.name, sector: s.sector, sig, sc, v, conf, strength, fam, price: s.price };
        }).sort((a, b) => b.fam - a.fam || (b.sig ? b.sig.confidence : 0) - (a.sig ? a.sig.confidence : 0));

        const buyCount = board.filter(x => x.v.cls === "ok").length;
        const sellCount = board.filter(x => x.v.cls === "err").length;
        const waitCount = board.length - buyCount - sellCount;

        const optR = opt.map((o, i) => ({ u: ["NIFTY", "BANKNIFTY"][i], sig: o.status === "fulfilled" ? o.value : null }));

        const factorChips = (sig) => (sig && sig.checks || []).map(c => {
          const f = ideaFactor(c.factor, c.support);
          return '<div class="row" style="margin:5px 0"><span class="badge ' + f.cls + '" style="font-size:11px;min-width:118px">' + (f.cls === "ok" ? "🟢 SUPPORT" : "🔴 AGAINST") + '</span><span class="small grow">' + esc(f.label) + ' — ' + esc(f.text) + '</span></div>';
        }).join("") || '<div class="muted small">No factor detail returned.</div>';

        const strengthBadge = (st) => '<span class="badge ' + (st === "High" ? "ok" : st === "Moderate" ? "wait" : "sched") + '">Evidence: ' + esc(st) + '</span>';

        const ideaCard = (x, featured) =>
          '<div class="card ' + (featured ? "hoverable" : "") + '" style="' + (featured ? "border-color:var(--accent)" : "") + '">' +
            '<div class="spread"><div class="row"><span class="avatar sm">' + x.v.icon + '</span><b>' + esc(x.symbol) + '</b><span class="muted small">' + esc(x.name) + '</span></div>' +
            '<span class="badge ' + x.v.cls + '" style="font-size:12px;font-weight:700">' + esc(x.v.label) + '</span></div>' +
            '<div class="card-sub" style="margin:8px 0 4px">' + esc(x.sector || "—") + (x.price ? ' · ₹' + fmtNum(x.price) : "") + '</div>' +
            '<div class="small" style="line-height:1.5">' + esc(x.v.plain) + '</div>' +
            '<div class="row" style="margin:8px 0"><span class="muted small">Confidence ' + (x.sig ? x.sig.confidence : 0) + '%</span><span class="badge ' + x.conf.cls + '" style="font-size:11px">' + x.conf.word + '</span>' + strengthBadge(x.strength) + '</div>' +
            '<div style="height:7px;background:var(--surface-3);border-radius:5px;overflow:hidden"><div style="height:100%;width:' + (x.sig ? x.sig.confidence : 0) + '%;background:' + (x.v.cls === "ok" ? "var(--ok)" : x.v.cls === "err" ? "var(--err)" : "var(--warn)") + ';transition:width .4s"></div></div>' +
            '<div style="margin-top:10px">' + factorChips(x.sig) + '</div>' +
            '<div class="row" style="margin-top:10px"><div class="grow"></div><button class="btn btn-sm btn-soft" data-open="' + esc(x.symbol) + '">Full report →</button></div>' +
          '</div>';

        const optCards = optR.map(o => {
          const l = ideaOptLabel(o.sig && o.sig.label);
          const comps = o.sig && o.sig.components ? Object.keys(o.sig.components).map(k =>
            '<div class="row" style="margin:4px 0"><span class="muted small grow">' + esc(k) + '</span><div style="width:110px;height:6px;background:var(--surface-3);border-radius:4px;overflow:hidden"><div style="height:100%;width:' + Math.round((o.sig.components[k] || 0) * 100) + '%;background:' + (o.sig.components[k] >= 0.6 ? "var(--ok)" : o.sig.components[k] >= 0.4 ? "var(--warn)" : "var(--err)") + '"></div></div></div>'
          ).join("") : "";
          return '<div class="card"><div class="spread"><b>' + esc(o.u) + ' options</b><span class="badge ' + l.cls + '">' + esc(o.sig ? o.sig.label : "—") + '</span></div>' +
            '<div class="small" style="margin:6px 0;line-height:1.5">' + esc(l.text) + '</div>' +
            (o.sig ? '<div class="row" style="margin-bottom:6px"><span class="muted small">Chain score ' + (o.sig.score || 0) + '/100</span><span class="badge ' + (o.sig.confidence === "MODERATE" ? "wait" : "sched") + '" style="font-size:11px">data: ' + esc(o.sig.data_quality || "—") + '</span></div>' : "") +
            comps + '</div>';
        }).join("");

        body.innerHTML =
          '<div class="mkt-banner">' +
            '<div class="row" style="flex-wrap:wrap;gap:12px">' +
              '<span class="badge ' + (String(brief.regime.regime).toLowerCase().includes("bear") ? "err" : String(brief.regime.regime).toLowerCase().includes("bull") ? "ok" : "wait") + '">' + esc(brief.regime.regime) + '</span>' +
              '<span class="badge">' + esc(brief.regime.tone) + '</span>' +
              '<span class="badge">Breadth ' + Math.round((brief.regime.breadth || 0) * 100) + '%</span>' +
              '<span class="muted small grow text-right">' + esc(brief.data_status) + '</span>' +
            '</div>' +
            '<div class="mkt-summary" style="font-size:13px;font-weight:500">' + esc(brief.summary || "") + '</div>' +
            '<div class="mkt-interp" style="font-size:13px">' + esc(ideaRegimePlain(brief.regime)) + '</div>' +
          '</div>' +
          '<div class="dash-grid" style="margin-top:14px">' +
            mktStat("🟢 Strong setups", buyCount, "of " + board.length + " stocks") +
            mktStat("🟡 Wait & watch", waitCount, "no clear edge") +
            mktStat("🔴 Cautious", sellCount, "factors against") +
            mktStat("Top pick", board.length && board[0].v.cls === "ok" ? board[0].symbol : "—", board.length && board[0].v.cls === "ok" ? "strongest setup" : "none strong") +
          '</div>' +
          '<div class="card" style="margin-top:16px"><div class="card-title">How to read this page</div>' +
            '<div class="small" style="line-height:1.6">' +
            '<b>🟢 Strong buy case</b> — most factors agree with buying; conviction shows how strongly they agree (not a guarantee).<br>' +
            '<b>🟡 Wait & watch</b> — factors are split; no edge yet, patience beats forcing a trade.<br>' +
            '<b>🔴 Cautious</b> — most factors are against holding; consider trimming.<br>' +
            '<b>Evidence</b> — how much data sits behind the reading (High / Moderate / Low). Every signal is a research aid, not investment advice.</div></div>' +
          '<div class="grid-cards" style="margin-top:16px">' + board.map((x, i) => ideaCard(x, i === 0)).join("") + '</div>' +
          '<div class="card" style="margin-top:16px"><div class="card-title">📉 Options mood (derived from the option chain)</div>' +
            '<div class="grid-2" style="margin-top:10px">' + optCards + '</div></div>';

        $$("#ideaBody [data-open]").forEach(b => b.addEventListener("click", () => navigate("market/stock/" + encodeURIComponent(b.dataset.open))));
      } catch (e) {
        body.innerHTML = '<div class="empty" style="display:flex"><div class="empty-ic">⚠️</div><h3>Could not load signals</h3><p>' + esc(e && e.message || String(e)) + '</p></div>';
      }
    };
    scan();
    if (window.__ideaRefresh) clearInterval(window.__ideaRefresh);
    window.__ideaRefresh = setInterval(() => {
      if (document.body.contains(content)) scan();
      else clearInterval(window.__ideaRefresh);
    }, 60000);
  }

  /* ---------------- Today Performance ---------------- */
  function todayPerf(content) {
    const page = '<div class="page">' +
      '<div class="page-header"><div><div class="page-title">📅 Today Performance</div>' +
      '<div class="page-desc">How the AI agent traded today — equity, P&L, cycles, risk guard and the memory engine. Auto-refreshes every 30s.</div></div>' +
      '<button class="btn btn-soft" id="tpRefresh">↻ Refresh</button></div>' +
      '<div id="tpBody"><div class="empty" style="display:flex"><div class="empty-ic">📅</div><h3>Loading today…</h3><p>Collecting trades, cycles, risk and memory.</p></div></div>' +
      '</div>';
    content.innerHTML = page;
    const body = $("#tpBody");
    $("#tpRefresh").addEventListener("click", load);

    function load() {
      body.innerHTML = '<div class="empty" style="display:flex"><div class="empty-ic">📅</div><h3>Loading today…</h3><p>Collecting trades, cycles, risk and memory.</p></div>';
      window.MarketClient.tradingPerformanceToday()
        .then(j => paint((j && j.performance) || null))
        .catch(e => { body.innerHTML = mktError(e); });
    }

    function paint(p) {
      if (!p) { body.innerHTML = mktError(new Error("empty performance response")); return; }
      const tr = p.trading || {}, pf = p.portfolio || {}, dy = p.day || {}, dl = p.daily_loss || {}, mem = p.memory || {};
      const run = tr.running === true;
      const pnl = (dy.day_pnl || 0) >= 0;
      const memCounts = (mem.stats && mem.stats.counts) || {};
      const memOut = (mem.stats && mem.stats.outcomes) || {};

      body.innerHTML = `
        <div class="dash-grid">
          ${mktStat("Day P&L", (pnl ? "+" : "") + fmtInr(dy.day_pnl), fmtPct(dy.day_return_pct))}
          ${mktStat("Realized today", fmtInr(dy.realized_today), (dy.sells_today || 0) + " sells · " + (dy.buys_today || 0) + " buys")}
          ${mktStat("Unrealized", fmtInr(dy.unrealized_today), (pf.open_count || 0) + " open positions")}
          ${mktStat("Cycles today", tr.cycles_today || 0, "auto-trading")}
        </div>

        <div class="card" style="margin-top:16px">
          <div class="spread"><div class="card-title">🤖 Auto-trading engine</div>
            <span class="badge ${run ? "ok" : "wait"}">${run ? "🟢 RUNNING" : "⏸ STOPPED"}</span></div>
          <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:8px">
            ${mktStat("Cycles today", tr.cycles_today || 0, (tr.cycles_ok_today || 0) + " ok · " + (tr.cycles_error_today || 0) + " err")}
            ${mktStat("Interval", tr.interval_sec ? tr.interval_sec + "s" : "—", "agent " + (tr.agent || "—"))}
            ${mktStat("Mode", tr.mode || "—", tr.live_trading_enabled ? "⚠ live money" : "paper / safe")}
            ${mktStat("Last cycle", tr.last_cycle_at || "—", "started " + (tr.started_at || "—"))}
          </div>
          ${tr.last_error ? '<div class="insight" style="margin-top:10px"><span class="ic">⚠️</span><span>Last error: ' + esc(String(tr.last_error).slice(0, 200)) + '</span></div>' : ""}
          ${tr.last_result ? '<div class="small muted" style="margin-top:8px;white-space:pre-wrap">Last result: ' + esc(String(tr.last_result).slice(0, 240)) + '</div>' : ""}
          <div class="row" style="margin-top:12px"><div class="grow"></div>
            <button class="btn btn-sm btn-soft" id="tpStart" ${run ? "disabled" : ""}>▶ Start</button>
            <button class="btn btn-sm" id="tpStop" ${run ? "" : "disabled"}>⏹ Stop</button></div>
        </div>

        <div class="card" style="margin-top:16px">
          <div class="spread"><div class="card-title">🛡️ Daily loss guard</div>
            <span class="badge ${dl.tripped ? "err" : "ok"}">${dl.tripped ? "🚨 TRIPPED" : "✅ HEALTHY"}</span></div>
          <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:8px">
            ${mktStat("Day loss", fmtInr(dl.daily_loss), "limit " + fmtInr(dl.limit_inr))}
            ${mktStat("Start equity", fmtInr(dy.start_equity), "now " + fmtInr(dy.equity_now))}
            ${mktStat("Exposure", (pf.exposure_pct || 0) + "%", "of portfolio")}
            ${mktStat("Win rate", (pf.win_rate_pct_all || 0) + "%", "all-time paper")}
          </div>
        </div>

        <div class="card" style="margin-top:16px">
          <div class="card-title">💼 Open positions</div>
          <table class="tbl"><thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Price</th><th>Value</th><th>Unrealized</th><th>Stop</th></tr></thead><tbody>
          ${(pf.positions || []).map(pos => '<tr><td><b>' + esc(pos.symbol) + '</b></td><td>' + pos.quantity + '</td><td>' + fmtNum(pos.entry, 2) + '</td><td>' + fmtNum(pos.price, 2) + '</td><td>' + fmtInr(pos.value) + '</td><td class="' + (pos.unrealized_pnl >= 0 ? "up" : "down") + '">' + (pos.unrealized_pnl >= 0 ? "+" : "") + fmtInr(pos.unrealized_pnl) + '</td><td>' + fmtNum(pos.stop_loss, 2) + '</td></tr>').join("") || '<tr><td colspan="7" class="muted">No open positions.</td></tr>'}
          </tbody></table>
        </div>

        <div class="grid-2" style="margin-top:16px">
          <div class="card"><div class="card-title">🧾 Trade executions today</div>
            <table class="tbl"><thead><tr><th>Time</th><th>Action</th><th>Detail</th><th>OK</th></tr></thead><tbody>
            ${(p.activity || []).map(a => '<tr><td class="muted small">' + esc(a.ts) + '</td><td><span class="badge ' + (a.allowed === false ? "err" : "ok") + '">' + esc(a.action) + '</span></td><td class="small">' + esc(a.detail) + '</td><td>' + (a.allowed === false ? "✗" : "✓") + '</td></tr>').join("") || '<tr><td colspan="4" class="muted">No executions recorded today.</td></tr>'}
            </tbody></table>
          </div>
          <div class="card"><div class="card-title">⏱ Auto-trading events today</div>
            <table class="tbl"><thead><tr><th>Time</th><th>Event</th><th>Detail</th></tr></thead><tbody>
            ${(p.cycles || []).slice(0, 12).map(c => '<tr><td class="muted small">' + esc(c.ts) + '</td><td><span class="badge ' + (c.event === "cycle_error" ? "err" : c.event === "cycle" ? "ok" : "wait") + '">' + esc(c.event) + '</span></td><td class="small">' + esc(c.agent || c.reason || c.error || "") + '</td></tr>').join("") || '<tr><td colspan="3" class="muted">No auto-trading events today.</td></tr>'}
            </tbody></table>
          </div>
        </div>

        <div class="card" style="margin-top:16px">
          <div class="spread"><div class="card-title">🧠 Memory engine</div>
            <span class="badge sched">dir: ${esc(mem.stats ? mem.stats.dir || "" : "")}</span></div>
          <div class="row" style="flex-wrap:wrap;gap:10px;margin-top:8px">
            ${mktStat("Decisions today", mem.today.decisions, (memCounts.decisions || 0) + " stored")}
            ${mktStat("Outcomes today", mem.today.outcomes, (memCounts.outcomes || 0) + " closed trades")}
            ${mktStat("Lessons today", mem.today.lessons, (memCounts.lessons || 0) + " stored")}
            ${mktStat("Win rate", Math.round((memOut.win_rate || 0) * 100) + "%", "expectancy " + fmtInr(memOut.expectancy))}
          </div>
          ${(mem.stats && mem.stats.recent_lessons && mem.stats.recent_lessons.length) ? '<div class="small" style="margin-top:10px;line-height:1.6">' +
            mem.stats.recent_lessons.slice(0, 4).map(l => '<div class="insight" style="margin-top:4px"><span class="ic">📓</span><span><b>' + esc(l.symbol) + '</b> — ' + esc(l.lesson) + '</span></div>').join("") + '</div>' : ""}
        </div>`;

      $("#tpStart").addEventListener("click", () => {
        window.MarketClient.tradingStart().then(() => { toast("Auto trading", "Started — each cycle runs through the safety gates.", "ok"); load(); }).catch(e => toast("Start failed", e.message, "err"));
      });
      $("#tpStop").addEventListener("click", () => {
        window.MarketClient.tradingStop().then(() => { toast("Auto trading", "Stopped — no further cycles.", "warn"); load(); }).catch(e => toast("Stop failed", e.message, "err"));
      });
    }

    load();
    if (window.__tpRefresh) clearInterval(window.__tpRefresh);
    window.__tpRefresh = setInterval(() => {
      if (document.body.contains(content)) load();
      else clearInterval(window.__tpRefresh);
    }, 30000);
  }

  /* ---------------- Market AI ---------------- */
  const MKT_TABS = [
    ["overview", "📊", "Overview"],
    ["brief", "🗞️", "Brief"],
    ["stocks", "🔍", "Stocks"],
    ["screener", "🎛️", "Screener"],
    ["watchlist", "⭐", "Watchlist"],
    ["signals", "🔔", "Signals"],
    ["options", "🧾", "Option Chain"],
    ["portfolio", "💼", "Portfolio"],
    ["paper", "🧪", "Paper Trading"],
    ["backtest", "📈", "Backtest"],
    ["risk", "🛡️", "Risk Center"],
    ["alerts", "🚨", "Alerts"],
    ["journal", "📓", "Journal"],
    ["monitoring", "🧠", "Monitoring"],
    ["audit", "🔏", "Audit"],
  ];
  const MKT_HINTS = {
    overview: "Regime, breadth and the day's moves in one screen.",
    brief: "One-line AI interpretation of the market state.",
    stocks: "Browse the tracked universe and open a stock's full intelligence report.",
    screener: "Filter the universe by score, sector, valuation and momentum.",
    watchlist: "Your saved symbols, refreshed with live Moneycontrol quotes (demo fallback offline).",
    signals: "Composite signals across the whole universe, never a single indicator.",
    options: "NSE India option chains: OI, PCR, IV, max pain, expected move, unusual activity, scenarios, strategies, paper trading and the Option Intel decision card (futures, IV rank, no-trade gate).",
    portfolio: "Simulated paper portfolio value, exposure and P&L.",
    paper: "Execute simulated buys and sells — no real money, ever.",
    backtest: "EMA+RSI strategy backtest on real historical OHLC with anti-overfit grading.",
    risk: "Position sizing from risk per trade and portfolio concentration checks.",
    alerts: "Rule-based alerts on signals, price moves and risk flags.",
    journal: "Every paper trade logged with entry, exit and P&L.",
    monitoring: "Data source status, quality and model versions.",
    audit: "A readable log of every market action taken in this workspace.",
  };

  function market(content) {
    const route = currentRoute();
    const sub = (route.rest && route.rest[0]) || "overview";
    const tab = MKT_TABS.find(t => t[0] === sub) || MKT_TABS[0];
    content.innerHTML = `
      <div class="page">
        <div class="page-header">
          <div><div class="page-title">Market AI</div>
          <div class="page-desc" id="mktHint">${MKT_HINTS[tab[0]]}</div></div>
          <button class="btn btn-soft" id="mktChat">💬 Ask Market agent</button>
        </div>
        <div class="seg mkt-tabs" id="mktTabs">
          ${MKT_TABS.map(t => '<button data-t="' + t[0] + '" class="' + (t[0] === sub ? "sel" : "") + '"><span>' + t[1] + '</span>' + t[2] + '</button>').join("")}
        </div>
        <div id="mktBody" style="margin-top:16px"></div>
      </div>`;
    $$("#mktTabs button").forEach(b => b.addEventListener("click", () => {
      const t = b.dataset.t;
      navigate("market", t === "overview" ? "" : t);
    }));
    $("#mktChat").addEventListener("click", () => {
      state.activeAgent = "market";
      localStorage.setItem("acc-agent", "market");
      state.chat = [];
      navigate("chat");
      setTimeout(() => { addUserMessage("Give me today's market brief, the current regime and a composite signal for TCS."); runAgentTask("Give me today's market brief, the current regime and a composite signal for TCS."); }, 60);
    });
    const body = $("#mktBody");
    const subs = { overview: mOverview, brief: mBrief, stocks: mStocks, stock: mStock, screener: mScreener, watchlist: mWatchlist, signals: mSignals, options: mOptions, portfolio: mPortfolio, paper: mPaper, backtest: mBacktest, risk: mRisk, alerts: mAlerts, journal: mJournal, monitoring: mMonitoring, audit: mAudit };
    (subs[sub] || mOverview)(body, route);

    // Auto-refresh market data every 30s while this view is open (re-renders
    // the current sub-view; cleared on navigation). Idempotent: each sub-view
    // re-render replaces #mktBody content and rebinds its own polling.
    if (window.__mktRefresh) clearInterval(window.__mktRefresh);
    window.__mktRefresh = setInterval(() => {
      if (!document.body.contains(body)) return;
      try { ss(body); } catch (e) { /* keep last render on error */ }
    }, 30000);
    function ss(bodyEl) {
      const f = subs[sub] || mOverview;
      f(bodyEl, route);
    }
  }

  function mktLoading() { return '<div class="empty" style="display:flex"><div class="empty-ic">📈</div><h3>Loading market data…</h3><p>Fetching live data from the Moneycontrol feed.</p></div>'; }
  function mktError(e) { return '<div class="empty" style="display:flex"><div class="empty-ic">⚠️</div><h3>Could not load market data</h3><p>' + esc(e && e.message || String(e)) + '</p></div>'; }
  function mktStat(label, value, sub) { return '<div class="card kpi"><div class="kpi-label">' + label + '</div><div class="kpi-value">' + value + '</div>' + (sub ? '<div class="kpi-delta">' + sub + '</div>' : "") + '</div>'; }
  function fmtInr(v) {
    if (v === undefined || v === null || isNaN(v)) return "—";
    return "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  }
  function fmtNum(v, d) { return (v === undefined || v === null) ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d || 0 }); }
  function fmtPct(v, d) { return (v === undefined || v === null) ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d === undefined ? 2 : d) + "%"; }
  function sigClass(sig) {
    if (!sig) return "badge";
    if (sig.includes("BUY")) return "badge ok";
    if (sig.includes("SELL")) return "badge err";
    return "badge wait";
  }
  function sparkSvg(closes, w, h) {
    if (!closes || closes.length < 2) return '<div class="muted small">No series</div>';
    w = w || 340; h = h || 84;
    const min = Math.min(...closes), max = Math.max(...closes);
    const span = (max - min) || 1;
    const x = i => 4 + (i / (closes.length - 1)) * (w - 8);
    const y = v => 6 + (h - 12) - ((v - min) / span) * (h - 12);
    const pts = closes.map((v, i) => x(i).toFixed(1) + "," + y(v).toFixed(1)).join(" ");
    const up = closes[closes.length - 1] >= closes[0];
    const col = up ? "var(--ok)" : "var(--err)";
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" width="100%" height="100%" preserveAspectRatio="none" role="img" aria-label="Price chart">' +
      '<polyline points="' + pts + '" fill="none" stroke="' + col + '" stroke-width="2" stroke-linejoin="round"/>' +
      '</svg>';
  }

  function mOverview(body) {
    body.innerHTML = mktLoading();
    Promise.allSettled([window.MarketClient.brief(), window.MarketClient.regime(), window.MarketClient.indices(), window.MarketClient.news(), window.MarketClient.signal("RELIANCE")]).then(([brief, reg, idx, news, sig]) => {
      if (brief.status !== "fulfilled") { body.innerHTML = mktError(brief.reason); return; }
      const b = brief.value, r = reg.status === "fulfilled" ? reg.value : null, ind = idx.status === "fulfilled" ? idx.value : [], nw = news.status === "fulfilled" ? news.value : [];
      const s = sig.status === "fulfilled" ? sig.value : null;
      body.innerHTML = `
        <div class="mkt-banner">
          <div class="row" style="flex-wrap:wrap;gap:12px">
            <span class="badge ${b.regime.regime.toLowerCase().includes("bear") ? "err" : b.regime.regime.toLowerCase().includes("bull") ? "ok" : "wait"}">${esc(b.regime.regime)}</span>
            <span class="badge">${esc(b.regime.tone)}</span>
            <span class="badge">Breadth ${Math.round(b.regime.breadth * 100)}%</span>
            <span class="muted small grow text-right">${esc(b.data_status)}</span>
          </div>
          <div class="mkt-summary">${esc(b.summary)}</div>
          <div class="mkt-interp">🤖 ${esc(b.ai_interpretation)}</div>
        </div>
        <div class="card" style="margin-top:14px" id="autoTradeCard">
          <div class="row" style="flex-wrap:wrap;gap:12px;align-items:center">
            <div style="display:flex;align-items:center;gap:10px">
              <span class="avatar sm" id="atIcon">🤖</span>
              <div><b>Automatic Trading</b><div class="muted small" id="atSub">Loading…</div></div>
            </div>
            <div class="grow"></div>
            <span class="badge" id="atBadge" style="font-weight:600">—</span>
            <button class="btn btn-primary" id="atStart">▶ Start</button>
            <button class="btn" id="atStop">⏹ Stop</button>
          </div>
          <div class="muted small" id="atDetail" style="margin-top:8px"></div>
        </div>
        <div class="dash-grid" style="margin-top:14px">
          ${r ? mktStat("Regime", r.regime.split(" ")[0], r.tone) : ""}
          ${mktStat("NIFTY 50", fmtNum(b.indices[0] && b.indices[0].value), fmtPct(b.indices[0] && b.indices[0].change_pct))}
          ${mktStat("Indices up", (b.regime.breadth * 6).toFixed(0) + " / 6", "breadth")}
          ${s ? mktStat("RELIANCE signal", s.signal, "conf " + s.confidence + "%") : ""}
        </div>
        <div class="grid-2" style="margin-top:16px">
          <div class="card"><div class="card-title">Indices</div>
            <table class="tbl"><thead><tr><th>Index</th><th>Value</th><th>Change</th></tr></thead><tbody>
            ${ind.map(i => '<tr><td><b>' + esc(i.symbol) + '</b><div class="muted small">' + esc(i.name) + '</div></td><td>' + fmtNum(i.value, 2) + '</td><td class="' + (i.change_pct >= 0 ? "up" : "down") + '">' + fmtPct(i.change_pct) + '</td></tr>').join("")}
            </tbody></table>
          </div>
          <div class="card"><div class="card-title">News & sentiment</div>
            ${nw.slice(0, 6).map(n => '<div class="kn-item"><div class="kn-ic">' + (n.sentiment === "Positive" ? "🟢" : n.sentiment === "Negative" ? "🔴" : "🟡") + '</div><div class="grow"><b>' + esc(n.title) + '</b><div class="muted small">' + esc(n.source) + ' · ' + esc(n.time) + '</div></div><span class="badge ' + (n.sentiment === "Positive" ? "ok" : n.sentiment === "Negative" ? "err" : "wait") + '">' + n.sentiment + '</span></div>').join("")}
            ${!nw.length ? '<div class="muted small">No news yet.</div>' : ""}
          </div>
        </div>
        <div class="grid-3" style="margin-top:16px">
          ${mktQuick("💡", "Idea Indicator", "All signals explained in plain language", "ideas")}
          ${mktQuick("🎛️", "Screener", "Find stocks by score & sector", "screener")}
          ${mktQuick("📈", "Backtest", "Test the EMA+RSI strategy", "backtest")}
        </div>`;
      bindAutoTrading();
      $$("#mktBody [data-goto]").forEach(c => c.addEventListener("click", () => {
        const target = c.dataset.goto;
        if (target === "ideas") navigate("ideas");
        else navigate("market", target);
      }));
    });
  }

  function bindAutoTrading() {
    const card = $("#autoTradeCard");
    if (!card) return;
    const startBtn = $("#atStart"), stopBtn = $("#atStop");
    let timer = null;
    const paint = (t) => {
      if (!t) return;
      const running = t.running === true;
      $("#atBadge").textContent = running ? "🟢 RUNNING" : "⏸ STOPPED";
      $("#atBadge").className = "badge " + (running ? "ok" : "wait");
      $("#atIcon").textContent = running ? "🤖" : "💤";
      startBtn.disabled = running;
      stopBtn.disabled = !running;
      const sub = [];
      sub.push((running ? "Running" : "Stopped") + " · " + (t.cycles || 0) + " cycles");
      if (t.interval_sec) sub.push("every " + t.interval_sec + "s");
      if (t.agent) sub.push("agent: " + t.agent);
      if (t.mode) sub.push(t.mode);
      $("#atSub").textContent = sub.join(" · ");
      $("#atDetail").textContent = (running && t.last_cycle_at ? "Last cycle " + esc(t.last_cycle_at) : t.stop_reason ? "Stopped: " + esc(t.stop_reason) : "") +
        (t.last_result ? "\nLast result: " + esc(String(t.last_result).slice(0, 220)) : "");
    };
    const refresh = () => window.MarketClient.tradingStatus().then(j => { paint((j && j.trading) || null); }).catch(() => {});
    startBtn.addEventListener("click", () => {
      window.MarketClient.tradingStart().then(j => { paint((j && j.trading) || null); toast("Auto trading", "Started. Each cycle runs the risk agent through the safety gates.", "ok"); })
        .catch(e => toast("Start failed", e.message, "err"));
    });
    stopBtn.addEventListener("click", () => {
      window.MarketClient.tradingStop().then(j => { paint((j && j.trading) || null); toast("Auto trading", "Stopped. No further cycles will run.", "warn"); })
        .catch(e => toast("Stop failed", e.message, "err"));
    });
    refresh();
    timer = setInterval(refresh, 5000);
    window.addEventListener("hashchange", () => { clearInterval(timer); }, { once: true });
  }
  function mktQuick(ic, t, d, view) {
    return '<div class="card hoverable" data-goto="' + view + '" style="cursor:pointer"><div class="row"><span class="avatar sm">' + ic + '</span><b>' + t + '</b></div><div class="card-sub" style="margin-top:8px">' + d + '</div><div style="margin-top:10px"><span class="badge">Open →</span></div></div>';
  }

  function mBrief(body) {
    body.innerHTML = mktLoading();
    window.MarketClient.brief().then(b => {
      body.innerHTML = `
        <div class="mkt-banner">
          <div class="row" style="flex-wrap:wrap;gap:12px">
            <span class="badge ${b.regime.regime.toLowerCase().includes("bear") ? "err" : b.regime.regime.toLowerCase().includes("bull") ? "ok" : "wait"}">${esc(b.regime.regime)}</span>
            <span class="badge">${esc(b.regime.tone)}</span>
            <span class="muted small grow text-right">${esc(b.data_status)}</span>
          </div>
          <div class="mkt-summary">${esc(b.summary)}</div>
          <div class="mkt-interp">🤖 ${esc(b.ai_interpretation)}</div>
        </div>
        <div class="grid-2" style="margin-top:16px">
          <div class="card"><div class="card-title">Top gainers</div>
            <table class="tbl"><tbody>${b.top_gainers.map(g => '<tr><td><b>' + esc(g.symbol) + '</b></td><td class="up">' + fmtPct(g.change_pct) + '</td></tr>').join("") || '<tr><td class="muted">None</td></tr>'}</tbody></table>
          </div>
          <div class="card"><div class="card-title">Top losers</div>
            <table class="tbl"><tbody>${b.top_losers.map(g => '<tr><td><b>' + esc(g.symbol) + '</b></td><td class="down">' + fmtPct(g.change_pct) + '</td></tr>').join("") || '<tr><td class="muted">None</td></tr>'}</tbody></table>
          </div>
        </div>
        <div class="card" style="margin-top:16px"><div class="card-title">Headlines</div>
          ${b.news.map(n => '<div class="kn-item"><div class="kn-ic">' + (n.sentiment === "Positive" ? "🟢" : n.sentiment === "Negative" ? "🔴" : "🟡") + '</div><div class="grow"><b>' + esc(n.title) + '</b><div class="muted small">' + esc(n.source) + '</div></div></div>').join("")}
        </div>
        <div class="card" style="margin-top:16px"><div class="card-title">Indices</div>
          <table class="tbl"><thead><tr><th>Index</th><th>Value</th><th>Change</th><th>Status</th></tr></thead><tbody>
          ${b.indices.map(i => '<tr><td><b>' + esc(i.symbol) + '</b></td><td>' + fmtNum(i.value, 2) + '</td><td class="' + (i.change_pct >= 0 ? "up" : "down") + '">' + fmtPct(i.change_pct) + '</td><td class="muted small">' + esc(i.status) + '</td></tr>').join("")}
          </tbody></table>
        </div>`;
    }).catch(e => { body.innerHTML = mktError(e); });
  }

  function mStocks(body) {
    body.innerHTML = mktLoading();
    window.MarketClient.stocks().then(list => {
      body.innerHTML = `
        <div class="toolbar"><div class="search-input"><span>🔍</span><input id="mktStockSearch" placeholder="Filter stocks by symbol or name…"></div>
        <span class="muted small">${list.length} symbols · ${esc(list[0] ? list[0].quality : "")}</span></div>
        <div class="table-wrap" style="margin-top:12px"><table class="tbl">
          <thead><tr><th>Symbol</th><th>Name</th><th>Sector</th><th>Price</th><th>Change</th><th>Mkt Cap</th><th>P/E</th><th></th></tr></thead>
          <tbody id="mktStockRows"></tbody></table></div>`;
      const rows = $("#mktStockRows");
      const render = (q) => {
        const ql = (q || "").toLowerCase();
        rows.innerHTML = list.filter(s => !ql || (s.symbol + " " + s.name).toLowerCase().includes(ql)).map(s =>
          '<tr data-sym="' + esc(s.symbol) + '" style="cursor:pointer"><td><b>' + esc(s.symbol) + '</b></td><td>' + esc(s.name) + '</td><td class="muted">' + esc(s.sector) + '</td>' +
          '<td>' + fmtNum(s.price, 2) + '</td><td class="' + (s.change_pct >= 0 ? "up" : "down") + '">' + fmtPct(s.change_pct) + '</td>' +
          '<td>' + fmtInr(s.market_cap) + '</td><td>' + s.pe + '</td><td><button class="btn btn-sm btn-soft" data-open="' + esc(s.symbol) + '">Analyze</button></td></tr>').join("") ||
          '<tr><td colspan="8" class="muted">No matches.</td></tr>';
      };
      render("");
      $("#mktStockSearch").addEventListener("input", e => render(e.target.value));
      $$("#mktStockRows [data-open]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); navigate("market/stock/" + encodeURIComponent(b.dataset.open)); }));
      $$("#mktStockRows tr[data-sym]").forEach(tr => tr.addEventListener("click", () => navigate("market/stock/" + encodeURIComponent(tr.dataset.sym))));
    }).catch(e => { body.innerHTML = mktError(e); });
  }

  function mStock(body, route) {
    const symbol = (route.rest && route.rest[1]) || "RELIANCE";
    body.innerHTML = mktLoading();
    const jobs = [window.MarketClient.quote(symbol), window.MarketClient.technical(symbol), window.MarketClient.fundamental(symbol), window.MarketClient.score(symbol), window.MarketClient.signal(symbol), window.MarketClient.ohlc(symbol, 120)];
    Promise.allSettled(jobs).then(([q, t, f, sc, sg, oc]) => {
      if (q.status !== "fulfilled") { body.innerHTML = mktError(q.reason); return; }
      const quote = q.value, tech = t.status === "fulfilled" ? t.value : null, fund = f.status === "fulfilled" ? f.value : null, score = sc.status === "fulfilled" ? sc.value : null, sig = sg.status === "fulfilled" ? sg.value : null, ohlc = oc.status === "fulfilled" ? oc.value : [];
      const closes = ohlc.map(o => o.close);
      body.innerHTML = `
        <div class="mkt-banner">
          <div class="row" style="flex-wrap:wrap;gap:12px;align-items:center">
            <span class="avatar ai lg" style="font-size:20px">📈</span>
            <div class="grow"><b style="font-size:18px">${esc(quote.symbol)}</b> <span class="muted">${esc(quote.name)} · ${esc(quote.exchange)}</span>
            <div><span style="font-size:20px;font-weight:700">${fmtNum(quote.price, 2)}</span> <span class="${quote.change_pct >= 0 ? "up" : "down"}">${fmtPct(quote.change_pct)}</span></div></div>
            <span class="muted small">${esc(quote.quality)}<br>${esc(quote.timestamp)}</span>
          </div>
          ${sig ? '<div class="row" style="gap:10px;margin-top:12px;flex-wrap:wrap"><span class="badge ' + sigClass(sig.signal) + '" style="font-size:13px;padding:6px 12px">Signal: ' + esc(sig.signal) + '</span><span class="badge">Confidence ' + sig.confidence + '%</span><span class="badge">Evidence ' + sig.evidence_strength + '</span><button class="btn btn-sm btn-soft" data-wl="' + esc(quote.symbol) + '">⭐ Watchlist</button><button class="btn btn-sm" data-paper="' + esc(quote.symbol) + '">🧪 Paper trade</button></div>' : ""}
        </div>
        <div class="dash-grid" style="margin-top:14px">
          ${score ? mktStat("AI Score", score.score, "/100") : ""}
          ${tech ? mktStat("Trend", tech.trend.value, tech.momentum.value + " momentum") : ""}
          ${tech ? mktStat("Volatility", tech.volatility.value, tech.volume.value + " volume") : ""}
          ${tech ? mktStat("Support", fmtNum(tech.support, 0), "Resist " + fmtNum(tech.resistance, 0)) : ""}
        </div>
        <div class="grid-2" style="margin-top:16px">
          <div class="card"><div class="card-title">Price (120 sessions)</div><div style="height:120px">${sparkSvg(closes)}</div>
            <div class="row small muted" style="margin-top:8px"><span>VWAP ${fmtNum(tech && tech.vwap, 2)}</span><span class="grow"></span><span>ATR ${fmtNum(tech && tech.atr, 2)}</span><span>RSI ${tech ? tech.rsi : "—"}</span></div>
          </div>
          <div class="card"><div class="card-title">Fundamentals</div>
            <div class="grid-3" style="gap:10px">
              ${fund ? [["Mkt Cap", fmtInr(fund.market_cap)], ["P/E", fund.pe], ["P/B", fund.pb], ["ROE", fund.roe + "%"], ["ROCE", fund.roce + "%"], ["D/E", fund.de], ["Div yield", fund.div_yield + "%"], ["EPS", "₹" + fund.eps], ["Promoter", fund.promoter + "%"]].map(([k, v]) => '<div class="card" style="padding:10px"><div class="kpi-label">' + k + '</div><div class="kpi-value" style="font-size:16px">' + v + '</div></div>').join("") : ""}
            </div>
            <div class="muted small" style="margin-top:10px">${fund ? esc(fund.note) : ""}</div>
          </div>
        </div>
        ${score ? '<div class="card" style="margin-top:16px"><div class="card-title">AI score factors — ${score.model}</div>' +
          score.factors.map(fx => '<div class="spread" style="padding:7px 0;border-bottom:1px solid var(--border)"><span><b>' + esc(fx.name) + '</b> <span class="muted small">' + esc(fx.evidence) + '</span></span><span class="badge">' + fx.score + '</span></div>').join("") + '</div>' : ""}
        ${sig ? '<div class="card" style="margin-top:16px"><div class="card-title">Signal checks (composite)</div>' +
          sig.checks.map(c => '<div class="spread" style="padding:7px 0;border-bottom:1px solid var(--border)"><span>' + esc(c.factor) + ' <span class="muted small">' + esc(c.evidence) + '</span></span><span class="badge ' + (c.support === "BUY" ? "ok" : "err") + '">' + c.support + '</span></div>').join("") +
          '<div class="muted small" style="padding-top:8px">' + esc(sig.disclaimer) + '</div></div>' : ""}`;
      const wlBtn = $("[data-wl]", body);
      if (wlBtn) wlBtn.addEventListener("click", () => { const wl = JSON.parse(localStorage.getItem("acc-wl") || "[]"); if (!wl.includes(symbol)) { wl.push(symbol); localStorage.setItem("acc-wl", JSON.stringify(wl)); mktAudit("WATCHLIST + " + symbol); toast("Watchlist", symbol + " added.", "ok"); } else toast("Watchlist", symbol + " already saved.", "warn"); });
      const pBtn = $("[data-paper]", body);
      if (pBtn) pBtn.addEventListener("click", () => navigate("market/paper"));
    });
  }

  function mScreener(body) {
    const sectors = ["Energy", "Automobile", "IT Services", "Banking", "FMCG", "Telecom", "Infrastructure", "Consumer", "Pharma", "Finance"];
    body.innerHTML = `
      <div class="card"><div class="row" style="flex-wrap:wrap;gap:12px">
        <div class="field grow" style="margin:0"><label>Min AI score</label><input type="number" id="scrScore" value="50" min="0" max="100"></div>
        <div class="field grow" style="margin:0"><label>Sector</label><select id="scrSector"><option value="">Any</option>${sectors.map(s => '<option>' + s + '</option>').join("")}</select></div>
        <div class="field grow" style="margin:0"><label>Max P/E</label><input type="number" id="scrPe" value="40" min="0"></div>
        <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="scrRun">Run screener</button></div>
      </div></div>
      <div id="scrResults" style="margin-top:16px"></div>`;
    const run = () => {
      const res = $("#scrResults");
      res.innerHTML = mktLoading();
      window.MarketClient.screener({ min_score: parseFloat($("#scrScore").value) || 0, sector: $("#scrSector").value || null, max_pe: parseFloat($("#scrPe").value) || null }).then(list => {
        res.innerHTML = '<div class="card" style="padding:0;overflow:hidden"><table class="tbl"><thead><tr><th>Symbol</th><th>Name</th><th>Sector</th><th>Price</th><th>Change</th><th>P/E</th><th>RSI</th><th>AI Score</th><th></th></tr></thead><tbody>' +
          list.map(s => '<tr data-sym="' + esc(s.symbol) + '" style="cursor:pointer"><td><b>' + esc(s.symbol) + '</b></td><td>' + esc(s.name) + '</td><td class="muted">' + esc(s.sector) + '</td><td>' + fmtNum(s.price, 2) + '</td><td class="' + (s.change_pct >= 0 ? "up" : "down") + '">' + fmtPct(s.change_pct) + '</td><td>' + s.pe + '</td><td>' + (s.rsi ?? "—") + '</td><td><span class="badge">' + s.ai_score + '</span></td><td><button class="btn btn-sm btn-soft" data-open="' + esc(s.symbol) + '">Analyze</button></td></tr>').join("") +
          '</tbody></table>' + (list.length ? '<div class="muted small" style="padding:10px">' + list.length + ' stocks matched.</div>' : '<div class="empty" style="display:flex"><div class="empty-ic">🎛️</div><h3>No matches</h3><p>Loosen the filters to see more stocks.</p></div>') + '</div>';
        $$("#scrResults [data-open]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); navigate("market/stock/" + encodeURIComponent(b.dataset.open)); }));
        $$("#scrResults tr[data-sym]").forEach(tr => tr.addEventListener("click", () => navigate("market/stock/" + encodeURIComponent(tr.dataset.sym))));
      }).catch(e => { res.innerHTML = mktError(e); });
    };
    $("#scrRun").addEventListener("click", run);
    run();
  }

  function mWatchlist(body) {
    body.innerHTML = `
      <div class="toolbar"><div class="search-input"><span>＋</span><input id="wlAdd" placeholder="Add symbol, e.g. TCS"></div><button class="btn" id="wlAddBtn">Add to watchlist</button>
      <button class="btn btn-ghost" id="wlClear">Clear all</button></div>
      <div id="wlList" style="margin-top:16px"></div>`;
    const render = () => {
      const wl = JSON.parse(localStorage.getItem("acc-wl") || "[]");
      const list = $("#wlList");
      if (!wl.length) { list.innerHTML = '<div class="empty" style="display:flex"><div class="empty-ic">⭐</div><h3>Watchlist is empty</h3><p>Add symbols to track them here.</p></div>'; return; }
      list.innerHTML = mktLoading();
      Promise.allSettled(wl.map(s => window.MarketClient.signal(s))).then(results => {
        list.innerHTML = '<div class="grid-cards">' + results.map((r, i) => {
          if (r.status !== "fulfilled") return '<div class="card"><b>' + esc(wl[i]) + '</b><div class="muted small">' + esc(r.reason.message) + '</div></div>';
          const s = r.value;
          return '<div class="card"><div class="spread"><b>' + esc(s.symbol) + '</b><span class="badge ' + sigClass(s.signal) + '">' + esc(s.signal) + '</span></div><div class="card-sub" style="margin-top:6px">' + esc(s.name) + '</div><div class="row small muted" style="margin-top:10px"><span>Conf ' + s.confidence + '%</span><span class="grow"></span></div><div class="row" style="margin-top:10px"><button class="btn btn-sm btn-soft" data-open="' + esc(s.symbol) + '">Analyze</button><button class="btn btn-sm btn-ghost" data-rm="' + esc(s.symbol) + '">✕</button></div></div>';
        }).join("") + '</div>';
        $$("#wlList [data-open]").forEach(b => b.addEventListener("click", () => navigate("market/stock/" + encodeURIComponent(b.dataset.open))));
        $$("#wlList [data-rm]").forEach(b => b.addEventListener("click", () => { let w = JSON.parse(localStorage.getItem("acc-wl") || "[]"); w = w.filter(x => x !== b.dataset.rm); localStorage.setItem("acc-wl", JSON.stringify(w)); render(); }));
      });
    };
    const add = () => {
      const v = $("#wlAdd").value.trim().toUpperCase();
      if (!v) return;
      let w = JSON.parse(localStorage.getItem("acc-wl") || "[]");
      if (!w.includes(v)) { w.push(v); localStorage.setItem("acc-wl", JSON.stringify(w)); toast("Watchlist", v + " added.", "ok"); }
      $("#wlAdd").value = "";
      render();
    };
    $("#wlAddBtn").addEventListener("click", add);
    $("#wlAdd").addEventListener("keydown", e => { if (e.key === "Enter") add(); });
    $("#wlClear").addEventListener("click", () => { localStorage.setItem("acc-wl", "[]"); render(); toast("Watchlist", "Cleared.", "warn"); });
    render();
  }

  function mSignals(body) {
    body.innerHTML = mktLoading();
    window.MarketClient.stocks().then(list => {
      Promise.allSettled(list.map(s => window.MarketClient.signal(s.symbol))).then(results => {
        const rows = results.map((r, i) => r.status === "fulfilled" ? r.value : { symbol: list[i].symbol, signal: "ERROR", confidence: 0, checks: [], evidence_strength: "Low" });
        const buys = rows.filter(r => r.signal.includes("BUY")).length;
        const sells = rows.filter(r => r.signal.includes("SELL")).length;
        const watch = rows.filter(r => r.signal === "HOLD / WATCH").length;
        body.innerHTML = `
          <div class="dash-grid">
            ${mktStat("Buy candidates", buys, "of " + rows.length)}${mktStat("Hold / Watch", watch, "of " + rows.length)}${mktStat("Sell / Reduce", sells, "of " + rows.length)}${mktStat("Universe", rows.length, "stocks")}
          </div>
          <div class="table-wrap" style="margin-top:16px"><table class="tbl"><thead><tr><th>Symbol</th><th>Name</th><th>Signal</th><th>Confidence</th><th>Evidence</th><th></th></tr></thead><tbody>
          ${rows.map(r => '<tr data-sym="' + esc(r.symbol) + '" style="cursor:pointer"><td><b>' + esc(r.symbol) + '</b></td><td>' + esc(r.name) + '</td><td><span class="badge ' + sigClass(r.signal) + '">' + esc(r.signal) + '</span></td><td>' + r.confidence + '%</td><td>' + r.evidence_strength + '</td><td><button class="btn btn-sm btn-soft" data-open="' + esc(r.symbol) + '">Analyze</button></td></tr>').join("")}
          </tbody></table></div>
          <div class="muted small" style="margin-top:10px">Composite signals combine trend, momentum, volume, fundamentals and sentiment. No single indicator drives a signal.</div>`;
        $$("#mktBody [data-open]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); navigate("market/stock/" + encodeURIComponent(b.dataset.open)); }));
        $$("#mktBody tr[data-sym]").forEach(tr => tr.addEventListener("click", () => navigate("market/stock/" + encodeURIComponent(tr.dataset.sym))));
      });
    }).catch(e => { body.innerHTML = mktError(e); });
  }

  /* ---------------- Option Chain Intelligence ---------------- */
  const OPT_UNDERLYINGS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"];
  const OPT_TABS = [
    ["overview", "📊", "Overview"],
    ["chain", "📋", "Chain"],
    ["oi", "🏔️", "OI & PCR"],
    ["iv", "🌊", "IV & Greeks"],
    ["move", "🎯", "Max Pain / Move"],
    ["unusual", "⚡", "Unusual Activity"],
    ["strategy", "🧪", "Strategy Lab"],
    ["intel", "🧠", "Intel"],
    ["paper", "📝", "Option Paper"],
    ["backtest", "⏱️", "Option Backtest"],
  ];

  function optQualityBadge(q) {
    if (!q) return '<span class="badge wait">Quality —</span>';
    const status = q.status || "";
    const cls = status.includes("🟢") ? "ok" : status.includes("🔴") ? "err" : "wait";
    return '<span class="badge ' + cls + '">' + esc(status) + '</span>';
  }

  function optHeader(meta, analytics) {
    const iv = (analytics && analytics.iv) || {};
    return `
      <div class="mkt-banner">
        <div class="row" style="flex-wrap:wrap;gap:12px;align-items:center">
          <span class="avatar ai lg" style="font-size:20px">🧾</span>
          <div class="grow"><b style="font-size:18px">${esc(meta.underlying)}</b> <span class="muted">${esc(meta.expiry)} · ${esc(meta.market_state)} · ${esc(meta.source)}</span>
          <div><span style="font-size:20px;font-weight:700">${fmtNum(meta.spot, 2)}</span> <span class="muted small">spot</span></div></div>
          ${optQualityBadge(meta.quality)}
        </div>
        <div class="row" style="gap:10px;margin-top:12px;flex-wrap:wrap">
          ${analytics ? '<span class="badge">PCR(OI) ' + (analytics.pcr.pcr_oi ?? "—") + '</span><span class="badge">PCR(Vol) ' + (analytics.pcr.pcr_volume ?? "—") + '</span><span class="badge">ATM IV ' + (iv.atm_iv ? (iv.atm_iv * 100).toFixed(1) + "%" : "—") + '</span><span class="badge ' + (iv.regime === "HIGH" ? "err" : iv.regime === "LOW" ? "ok" : "wait") + '">IV ' + esc(iv.regime) + '</span><span class="badge">Max pain ' + fmtNum(analytics.max_pain.max_pain, 0) + '</span>' : ""}
          <span class="muted small grow text-right">${esc(meta.timestamp)}</span>
        </div>
      </div>`;
  }

  function mOptions(body) {
    body.innerHTML = `
      <div class="row" style="flex-wrap:wrap;gap:12px;align-items:flex-end">
        <div class="field" style="margin:0"><label>Underlying</label>
          <select id="optUnder">${OPT_UNDERLYINGS.map(u => '<option' + (u === "NIFTY" ? " selected" : "") + '>' + u + '</option>').join("")}</select></div>
        <div class="field grow" style="margin:0"><label>Expiry</label><select id="optExpiry"><option value="">Loading…</option></select></div>
        <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="optLoad">Load chain</button></div>
      </div>
      <div class="seg" id="optTabs" style="margin-top:14px;overflow-x:auto">
        ${OPT_TABS.map(t => '<button data-t="' + t[0] + '"><span>' + t[1] + '</span>' + t[2] + '</button>').join("")}
      </div>
      <div id="optBody" style="margin-top:16px"></div>`;
    const expirySel = $("#optExpiry");
    const optState = { underlying: "NIFTY", expiry: "", analysis: null };
    window.__optIntelState = optState;

    const loadExpiries = () => {
      window.OptionsClient.expiries(optState.underlying).then(list => {
        expirySel.innerHTML = list.map(e => '<option value="' + esc(e) + '"' + (e === optState.expiry ? " selected" : "") + '>' + esc(e) + '</option>').join("") || '<option value="">No expiries</option>';
        if (!optState.expiry && list.length) { optState.expiry = list[0]; }
      }).catch(e => { expirySel.innerHTML = '<option value="">' + esc(e.message) + '</option>'; });
    };

    const renderTab = () => {
      const t = document.querySelector("#optTabs button.sel");
      const active = (t && t.dataset.t) || "overview";
      const a = optState.analysis;
      if (!a) { $("#optBody").innerHTML = '<div class="empty" style="display:flex"><div class="empty-ic">🧾</div><h3>No data yet</h3><p>Select an expiry and press "Load chain".</p></div>'; return; }
      const subs = { overview: optOverview, chain: optChain, oi: optOI, iv: optIV, move: optMove, unusual: optUnusual, strategy: optStrategy, intel: optIntel, paper: optPaper, backtest: optBacktest };
      (subs[active] || optOverview)($("#optBody"), a);
    };

    const load = () => {
      const b = $("#optBody");
      b.innerHTML = mktLoading();
      window.OptionsClient.analysis(optState.underlying, optState.expiry).then(a => {
        optState.analysis = a;
        renderTab();
        mktAudit("OPTIONS " + optState.underlying + " " + (optState.expiry || "") + " loaded");
      }).catch(e => { b.innerHTML = mktError(e); });
    };

    $("#optUnder").addEventListener("change", e => { optState.underlying = e.target.value; optState.expiry = ""; expirySel.innerHTML = '<option value="">Loading…</option>'; loadExpiries(); });
    $("#optExpiry").addEventListener("change", e => { optState.expiry = e.target.value; });
    $("#optLoad").addEventListener("click", load);
    $$("#optTabs button").forEach(btn => btn.addEventListener("click", () => {
      $$("#optTabs button").forEach(x => x.classList.remove("sel"));
      btn.classList.add("sel");
      renderTab();
    }));
    loadExpiries();
    load();
  }

  function optOverview(body, a) {
    const m = a.meta, an = a.analytics, srz = a.support_resistance, sig = a.signal;
    body.innerHTML = `
      ${optHeader(m, an)}
      <div class="dash-grid" style="margin-top:14px">
        ${mktStat("Signal score", sig.score + "/100", sig.label + " · conf " + sig.confidence)}
        ${mktStat("PCR (OI)", an.pcr.pcr_oi ?? "—", "volume " + (an.pcr.pcr_volume ?? "—"))}
        ${mktStat("Max pain", fmtNum(an.max_pain.max_pain, 0), (an.max_pain.distance_pct >= 0 ? "+" : "") + an.max_pain.distance_pct + "% from spot")}
        ${mktStat("Expected move", fmtNum(an.expected_move.lower, 0) + " – " + fmtNum(an.expected_move.upper, 0), an.expected_move.days + " day · 1σ")}
      </div>
      <div class="grid-2" style="margin-top:16px">
        <div class="card"><div class="card-title">Support (OI clusters)</div>
          <table class="tbl"><thead><tr><th>Strike</th><th>Distance</th><th>OI</th></tr></thead><tbody>
          ${(srz.support || []).slice(0, 6).map(x => '<tr><td><b>' + fmtNum(x.strike, 0) + '</b></td><td class="down">' + x.distance_pct + '%</td><td>' + fmtNum(x.oi, 0) + '</td></tr>').join("") || '<tr><td colspan="3" class="muted">No support cluster.</td></tr>'}
          </tbody></table>
          <div class="muted small" style="margin-top:8px">${esc(srz.note || "")} · Confidence ${esc(srz.confidence || "")}</div>
        </div>
        <div class="card"><div class="card-title">Resistance (OI clusters)</div>
          <table class="tbl"><thead><tr><th>Strike</th><th>Distance</th><th>OI</th></tr></thead><tbody>
          ${(srz.resistance || []).slice(0, 6).map(x => '<tr><td><b>' + fmtNum(x.strike, 0) + '</b></td><td class="up">' + x.distance_pct + '%</td><td>' + fmtNum(x.oi, 0) + '</td></tr>').join("") || '<tr><td colspan="3" class="muted">No resistance cluster.</td></tr>'}
          </tbody></table>
          <div class="muted small" style="margin-top:8px">${esc(srz.note || "")}</div>
        </div>
      </div>
      <div class="card" style="margin-top:16px"><div class="card-title">Scenarios (not predictions)</div>
        ${a.scenarios.map(sc => '<div class="spread" style="padding:8px 0;border-bottom:1px solid var(--border)"><span><b>' + esc(sc.name) + '</b> → target <b>' + fmtNum(sc.target, 0) + '</b><div class="muted small">Invalidated: ' + (sc.invalidated_below ? "below " + fmtNum(sc.invalidated_below, 0) : "") + (sc.invalidated_above ? "above " + fmtNum(sc.invalidated_above, 0) : "") + '</div></span><span class="badge">' + esc(sc.label) + '</span></div>').join("")}
      </div>
      <div class="card" style="margin-top:16px"><div class="card-title">Signal breakdown</div>
        <div class="row" style="flex-wrap:wrap;gap:8px">
          ${Object.keys(sig.components || {}).map(k => '<span class="badge">' + k + ' ' + (sig.components[k] * 100).toFixed(0) + '</span>').join("")}
        </div>
        <div class="muted small" style="margin-top:8px">${esc(sig.disclaimer || "")}</div>
      </div>
      <div class="card" style="margin-top:16px"><div class="card-title">Suggested strategies</div>
        <div class="grid-cards">
          ${a.suggestions.map(s => '<div class="card"><div class="spread"><b>' + esc(s.display) + '</b><span class="badge ' + (s.max_loss < 0 && s.max_profit > 0 ? "ok" : "wait") + '">' + esc(s.risk_label) + '</span></div><div class="row small muted" style="margin-top:8px"><span>Max P ' + fmtNum(s.max_profit, 0) + '</span><span>Max L ' + fmtNum(s.max_loss, 0) + '</span><span>Margin ' + fmtInr(s.est_margin) + '</span></div></div>').join("")}
        </div>
        <div class="muted small" style="margin-top:8px">Estimates only. Verify margin with your broker before trading.</div>
      </div>`;
  }

  function optChain(body, a) {
    const an = a.analytics, contracts = a.contracts || [];
    body.innerHTML = `
      ${optHeader(a.meta, an)}
      <div class="toolbar" style="margin-top:12px"><div class="search-input"><span>🔍</span><input id="optChainSearch" placeholder="Filter by strike…"></div>
      <span class="muted small">${contracts.length} contracts · Greeks are Black-Scholes "Calculated" values</span></div>
      <div class="table-wrap" style="margin-top:12px"><table class="tbl" style="font-size:12px">
        <thead><tr><th>CALL LTP</th><th>CALL OI</th><th>CALL ΔOI</th><th>Vol</th><th>IV</th><th>STRIKE</th><th>IV</th><th>Vol</th><th>PUT ΔOI</th><th>PUT OI</th><th>PUT LTP</th></tr></thead>
        <tbody id="optChainRows"></tbody></table></div>`;
    const rows = $("#optChainRows");
    const byStrike = {};
    contracts.forEach(c => { (byStrike[c.strike] = byStrike[c.strike] || {})[c.option_type] = c; });
    const strikes = Object.keys(byStrike).map(Number).sort((x, y) => y - x);
    const render = (q) => {
      const ql = (q || "").toLowerCase();
      rows.innerHTML = strikes.filter(s => !ql || String(s).includes(ql)).map(s => {
        const ce = byStrike[s].CE, pe = byStrike[s].PE;
        const cell = (c, side) => {
          if (!c) return '<td class="muted">—</td><td class="muted">—</td><td class="muted">—</td><td class="muted">—</td><td class="muted">—</td>';
          if (side === "ce") return '<td><b>' + fmtNum(c.ltp, 2) + '</b></td><td>' + fmtNum(c.oi, 0) + '</td><td class="' + (c.change_oi >= 0 ? "up" : "down") + '">' + (c.change_oi >= 0 ? "+" : "") + fmtNum(c.change_oi, 0) + '</td><td>' + fmtNum(c.volume, 0) + '</td><td>' + (c.iv * 100).toFixed(1) + '</td>';
          return '<td>' + (c.iv * 100).toFixed(1) + '</td><td>' + fmtNum(c.volume, 0) + '</td><td class="' + (c.change_oi >= 0 ? "up" : "down") + '">' + (c.change_oi >= 0 ? "+" : "") + fmtNum(c.change_oi, 0) + '</td><td>' + fmtNum(c.oi, 0) + '</td><td><b>' + fmtNum(c.ltp, 2) + '</b></td>';
        };
        const atm = s === an.max_pain.max_pain ? " style='background:var(--accent-soft)'" : "";
        return '<tr' + atm + '>' + cell(ce, "ce") + '<td style="font-weight:700;border-left:1px solid var(--border);border-right:1px solid var(--border)">' + fmtNum(s, 0) + '</td>' + cell(pe, "pe") + '</tr>';
      }).join("") || '<tr><td colspan="11" class="muted">No strikes match.</td></tr>';
    };
    render("");
    $("#optChainSearch").addEventListener("input", e => render(e.target.value));
  }

  function optOI(body, a) {
    const m = a.meta, an = a.analytics;
    const oi = an.oi, contracts = a.contracts || [];
    const ce = {}, pe = {};
    contracts.forEach(c => { (c.option_type === "CE" ? ce : pe)[c.strike] = c; });
    const strikes = Object.keys(ce).map(Number).sort((x, y) => y - x).filter(s => pe[s]);
    const maxCe = Math.max(...Object.values(ce).map(c => c.oi), 1);
    const maxPe = Math.max(...Object.values(pe).map(c => c.oi), 1);
    const bar = (v, max, dir) => {
      const w = Math.max(4, Math.round(v / max * 100));
      return '<div class="oi-bar"><div class="oi-fill ' + (dir === "ce" ? "ce" : "pe") + '" style="width:' + w + '%"></div></div>';
    };
    body.innerHTML = `
      ${optHeader(m, an)}
      <div class="dash-grid" style="margin-top:14px">
        ${mktStat("Total CE OI", fmtNum(oi.total_ce_oi, 0), "Δ " + (oi.ce_change_oi >= 0 ? "+" : "") + fmtNum(oi.ce_change_oi, 0))}
        ${mktStat("Total PE OI", fmtNum(oi.total_pe_oi, 0), "Δ " + (oi.pe_change_oi >= 0 ? "+" : "") + fmtNum(oi.pe_change_oi, 0))}
        ${mktStat("PCR (OI)", oi.pcr_oi ?? "—", "positioning gauge")}
        ${mktStat("PCR (Vol)", an.pcr.pcr_volume ?? "—", "")}
      </div>
      <div class="card" style="margin-top:16px"><div class="card-title">Open Interest by strike</div>
        <table class="tbl"><thead><tr><th>Strike</th><th>CE OI</th><th>CE bar</th><th>PE bar</th><th>PE OI</th></tr></thead><tbody>
        ${strikes.slice(0, 26).map(s => '<tr><td><b>' + fmtNum(s, 0) + '</b></td><td>' + fmtNum(ce[s].oi, 0) + '</td><td style="min-width:120px">' + bar(ce[s].oi, maxCe, "ce") + '</td><td style="min-width:120px">' + bar(pe[s].oi, maxPe, "pe") + '</td><td>' + fmtNum(pe[s].oi, 0) + '</td></tr>').join("")}
        </tbody></table>
        <div class="muted small" style="margin-top:8px">PCR is a positioning gauge, not a timing signal.</div>
      </div>`;
  }

  function optIV(body, a) {
    const m = a.meta, an = a.analytics;
    const iv = an.iv, contracts = a.contracts || [];
    const ce = {}, pe = {};
    contracts.forEach(c => { (c.option_type === "CE" ? ce : pe)[c.strike] = c; });
    const strikes = Object.keys(ce).map(Number).sort((x, y) => x - y).filter(s => pe[s]);
    const pts = strikes.map(s => '<span style="font-size:11px;opacity:.75">' + (ce[s].iv * 100).toFixed(0) + '</span>').join("");
    body.innerHTML = `
      ${optHeader(m, an)}
      <div class="dash-grid" style="margin-top:14px">
        ${mktStat("ATM IV", iv.atm_iv ? (iv.atm_iv * 100).toFixed(1) + "%" : "—", "regime " + esc(iv.regime))}
        ${mktStat("ATM Put IV", iv.atm_put_iv ? (iv.atm_put_iv * 100).toFixed(1) + "%" : "—", "spread " + (iv.iv_spread ? (iv.iv_spread * 100).toFixed(1) + "%" : "—"))}
        ${mktStat("Skew CE slope", iv.skew_ce_slope, "per strike")}
        ${mktStat("Skew PE slope", iv.skew_pe_slope, "per strike")}
      </div>
      <div class="grid-2" style="margin-top:16px">
        <div class="card"><div class="card-title">IV smile (ATM-centred)</div>
          <div style="height:140px;display:flex;align-items:flex-end;gap:2px">${strikes.map(s => '<div style="flex:1;background:' + (s === an.max_pain.max_pain ? "var(--ok)" : "var(--accent)") + ';height:' + Math.max(6, (ce[s].iv / Math.max(iv.atm_iv, 0.01)) * 100) + '%;opacity:.85" title="' + s + ' ' + (ce[s].iv * 100).toFixed(1) + '%"></div>').join("")}</div>
          <div class="row small muted" style="margin-top:6px"><span>CE IV bars · dark = max pain</span><span class="grow"></span><span>${esc(iv.note)}</span></div>
        </div>
        <div class="card"><div class="card-title">Term structure (ATM IV by expiry)</div>
          <div class="muted small">Loaded from live chain for each expiry.</div>
        </div>
      </div>
      <div class="card" style="margin-top:16px"><div class="card-title">Sample Greeks (Black-Scholes "Calculated")</div>
        <table class="tbl"><thead><tr><th>Strike</th><th>Type</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th><th>IV</th></tr></thead><tbody>
        ${contracts.filter(c => Math.abs(c.strike - m.spot) < 200).slice(0, 10).map(c => '<tr><td><b>' + fmtNum(c.strike, 0) + '</b></td><td>' + c.option_type + '</td><td>' + c.delta + '</td><td>' + c.gamma + '</td><td>' + c.theta + '</td><td>' + c.vega + '</td><td>' + (c.iv * 100).toFixed(1) + '%</td></tr>').join("")}
        </tbody></table>
      </div>`;
  }

  function optMove(body, a) {
    const m = a.meta, an = a.analytics;
    const mp = an.max_pain, em = an.expected_move;
    const lo = Math.min(em.lower, mp.max_pain), hi = Math.max(em.upper, mp.max_pain);
    const span = (hi - lo) || 1;
    const pos = s => Math.max(2, Math.min(98, (s - lo) / span * 100));
    body.innerHTML = `
      ${optHeader(m, an)}
      <div class="dash-grid" style="margin-top:14px">
        ${mktStat("Max pain", fmtNum(mp.max_pain, 0), (mp.distance_pct >= 0 ? "+" : "") + mp.distance_pct + "% vs spot")}
        ${mktStat("Exp. move (1σ)", fmtNum(em.lower, 0) + " – " + fmtNum(em.upper, 0), em.days + " day · ATM IV " + (em.atm_iv * 100).toFixed(1) + "%")}
        ${mktStat("Spot", fmtNum(m.spot, 2), "")}
      </div>
      <div class="card" style="margin-top:16px">
        <div class="card-title">Strike ladder</div>
        <div style="position:relative;height:40px;margin:30px 10px 10px">
          <div style="position:absolute;top:0;bottom:0;left:${pos(m.spot)}%;border-left:2px solid var(--ok)">
            <span class="badge ok" style="position:absolute;top:-24px;transform:translateX(-50%);white-space:nowrap">Spot ${fmtNum(m.spot, 0)}</span>
          </div>
          <div style="position:absolute;top:8px;bottom:8px;left:${pos(em.lower)}%;right:${100 - pos(em.upper)}%;background:var(--accent-soft);border:1px dashed var(--accent)"><span class="badge" style="position:absolute;bottom:-22px;left:50%;transform:translateX(-50%);white-space:nowrap">1σ range</span></div>
          <div style="position:absolute;top:0;bottom:0;left:${pos(mp.max_pain)}%;border-left:2px solid var(--accent)"><span class="badge wait" style="position:absolute;top:-24px;transform:translateX(-50%);white-space:nowrap">Max pain</span></div>
        </div>
        <div class="muted small" style="margin-top:10px">${esc(em.label)} · ${esc(mp.note)}</div>
      </div>`;
  }

  function optUnusual(body, a) {
    const m = a.meta, an = a.analytics;
    const ua = an.unusual_activity || [];
    body.innerHTML = `
      ${optHeader(m, an)}
      <div class="card" style="margin-top:16px"><div class="card-title">Unusual activity (volume-to-OI spikes)</div>
        <table class="tbl"><thead><tr><th>Contract</th><th>Strike</th><th>Type</th><th>Volume</th><th>OI</th><th>Vol/OI</th><th>ΔOI</th><th>IV</th></tr></thead><tbody>
        ${ua.map(u => '<tr><td><b>' + esc(u.contract) + '</b></td><td>' + fmtNum(u.strike, 0) + '</td><td>' + u.option_type + '</td><td>' + fmtNum(u.volume, 0) + '</td><td>' + fmtNum(u.oi, 0) + '</td><td><span class="badge">' + u.vol_oi_ratio + 'x</span></td><td class="' + (u.change_oi >= 0 ? "up" : "down") + '">' + (u.change_oi >= 0 ? "+" : "") + fmtNum(u.change_oi, 0) + '</td><td>' + (u.iv * 100).toFixed(1) + '%</td></tr>').join("") || '<tr><td colspan="8" class="muted">No unusual activity above the threshold.</td></tr>'}
        </tbody></table>
        <div class="muted small" style="margin-top:8px">Evidence of order flow, never a trade recommendation.</div>
      </div>`;
  }

  function optStrategy(body, a) {
    const m = a.meta, an = a.analytics, sig = a.signal, srz = a.support_resistance || {}, scen = a.scenarios || [];
    const strategies = a.strategies || [];
    if (!strategies.length) { body.innerHTML = mktError(new Error("No strategy data available.")); return; }
    const byName = {};
    strategies.forEach(s => { byName[s.name] = s; });
    const iv = an.iv || {}, pcr = an.pcr || {}, exp = an.expected_move || {};
    const quality = m.quality && m.quality.status ? m.quality.status : "";
    const stale = /delayed|partial|unavailable/i.test(quality);

    const VIEWS = {
      strong_bull: { heroEmoji: "🟢", hero: "Strongly Bullish", label: "Rise Strongly", color: "ok",
        meaning: "The option chain and momentum point to a strongly bullish scenario. Call activity and price momentum are both positive.",
        base: "Continue higher" },
      mod_bull: { heroEmoji: "🟢", hero: "Moderately Bullish", label: "Rise Moderately", color: "ok",
        meaning: "The market is showing more signs of moving upward, but strong resistance may limit the rise.",
        base: "Range to mildly bullish" },
      range: { heroEmoji: "🟡", hero: "Sideways / Range-bound", label: "Stay in a Range", color: "wait",
        meaning: "Signals are balanced — neither buyers nor sellers clearly dominate. The market may stay inside a range.",
        base: "Range / sideways" },
      mod_bear: { heroEmoji: "🔴", hero: "Moderately Bearish", label: "Fall Moderately", color: "err",
        meaning: "The market is showing more signs of moving downward, but strong support may limit the fall.",
        base: "Range to mildly bearish" },
      strong_bear: { heroEmoji: "🔴", hero: "Strongly Bearish", label: "Fall Strongly", color: "err",
        meaning: "The option chain and momentum point to a strongly bearish scenario. Put activity and price momentum are both negative.",
        base: "Continue lower" },
      big_move: { heroEmoji: "⚡", hero: "Big Move Expected", label: "Move Sharply, Direction Unknown", color: "wait",
        meaning: "Signals are stretched in both directions — the market may be preparing for a sharp move without a clear direction.",
        base: "Large move, direction unclear" },
    };
    const META = {
      long_call: { emoji: "📈", view: "Strong Bullish", risk: "🟢 Limited Risk", when: "Expect strong upside",
        simple: "You expect NIFTY to rise significantly. You pay a premium for the right to benefit from an upward move.",
        whyFit: "it captures the upside while your loss stays limited to the premium" },
      long_put: { emoji: "📉", view: "Strong Bearish", risk: "🟢 Limited Risk", when: "Expect strong downside",
        simple: "You expect NIFTY to fall significantly. You pay a premium for the right to benefit from a downward move.",
        whyFit: "it captures the downside while your loss stays limited to the premium" },
      bull_call_spread: { emoji: "📈", view: "Moderately Bullish", risk: "🟢 Limited Risk", when: "Expect controlled upside",
        simple: "You are betting that NIFTY will move higher, but you also limit your risk by buying one call and selling another call.",
        whyFit: "it limits the maximum loss while keeping a defined profit zone" },
      bear_put_spread: { emoji: "📉", view: "Moderately Bearish", risk: "🟢 Limited Risk", when: "Expect controlled downside",
        simple: "You expect NIFTY to fall, but want to keep the maximum loss limited.",
        whyFit: "it limits the maximum loss while keeping a defined profit zone" },
      strangle: { emoji: "↔️", view: "Big Move", risk: "🟢 Limited Risk", when: "Direction uncertain",
        simple: "You expect NIFTY to make a large move, but you are unsure whether it will move up or down.",
        whyFit: "it profits from a large move in either direction without betting on the direction" },
      iron_condor: { emoji: "🛡️", view: "Sideways / Range", risk: "🟢 Limited Risk", when: "Expect a range",
        simple: "You expect NIFTY to stay inside a range. You sell far-away options for premium and buy protection, keeping the loss limited.",
        whyFit: "it collects premium in a range while the bought legs cap the risk" },
      covered_call: { emoji: "🛡️", view: "Mildly Bullish", risk: "🟡 Moderate Risk", when: "You hold NIFTY-linked assets",
        simple: "You already hold NIFTY-related assets and want to earn option premium while accepting limited upside.",
        whyFit: "it earns premium on exposure you already hold" },
    };
    const WHY = {
      long_call: "Your market view is bullish. A Long Call gives you full upside if NIFTY rises sharply, and your loss is limited to the premium you pay.",
      long_put: "Your market view is bearish. A Long Put gives you full downside benefit if NIFTY falls sharply, with loss limited to the premium you pay.",
      bull_call_spread: "You expect NIFTY to rise but want a known, limited worst-case loss. Buying one call and selling a higher call lowers the cost and caps the maximum loss.",
      bear_put_spread: "You expect NIFTY to fall but want a known, limited worst-case loss. Buying one put and selling a lower put lowers the cost and caps the maximum loss.",
      strangle: "Direction is uncertain but you expect a large move. Buying both a call and a put lets you profit from a sharp move either way.",
      iron_condor: "You expect NIFTY to stay inside a range. Selling far-away options collects premium, while the bought legs cap the loss if it breaks out.",
      covered_call: "You hold NIFTY exposure and expect limited upside. Selling a call earns premium, but you give up gains above the sold strike.",
    };
    const FITS = {
      strong_bull: { long_call: 94, bull_call_spread: 86, covered_call: 80, iron_condor: 32, strangle: 38, long_put: 8, bear_put_spread: 8 },
      mod_bull: { bull_call_spread: 93, covered_call: 86, long_call: 74, iron_condor: 50, strangle: 42, long_put: 14, bear_put_spread: 14 },
      range: { iron_condor: 90, strangle: 76, covered_call: 62, bull_call_spread: 56, bear_put_spread: 56, long_call: 32, long_put: 32 },
      mod_bear: { bear_put_spread: 93, long_put: 74, iron_condor: 50, strangle: 42, covered_call: 24, long_call: 14, bull_call_spread: 14 },
      strong_bear: { long_put: 94, bear_put_spread: 86, iron_condor: 32, strangle: 38, covered_call: 6, long_call: 8, bull_call_spread: 8 },
      big_move: { strangle: 90, iron_condor: 68, long_call: 55, long_put: 55, bull_call_spread: 40, bear_put_spread: 40, covered_call: 22 },
    };
    const VIEW_STRATS = {
      strong_bull: ["long_call", "bull_call_spread", "covered_call"],
      mod_bull: ["bull_call_spread", "long_call", "covered_call"],
      range: ["iron_condor", "strangle", "covered_call"],
      mod_bear: ["bear_put_spread", "long_put"],
      strong_bear: ["long_put", "bear_put_spread"],
      big_move: ["strangle", "iron_condor"],
    };
    const signalToView = { "Strongly Bullish": "strong_bull", "Mildly Bullish": "mod_bull", "Neutral": "range", "Mildly Bearish": "mod_bear", "Strongly Bearish": "strong_bear" };
    const autoView = signalToView[sig.label] || "range";
    const sup0 = (srz.support && srz.support[0] && srz.support[0].strike) || exp.lower || m.spot * 0.995;
    const res0 = (srz.resistance && srz.resistance[0] && srz.resistance[0].strike) || exp.upper || m.spot * 1.005;
    const majLow = exp.lower || m.spot * 0.99;
    const majHigh = exp.upper || m.spot * 1.01;

    const tip = (t) => '<span class="tip" tabindex="0" title="' + esc(t) + '" aria-label="' + esc(t) + '">ⓘ</span>';
    const labState = { selected: null, view: autoView, override: false, advanced: false, today: false };

    function fitFor(name, v) {
      let f = (FITS[v] || {})[name];
      if (f === undefined) f = 45;
      if ((a.suggestions || []).some(x => x.name === name)) f += 6;
      if (stale) f -= 5;
      return Math.max(5, Math.min(99, Math.round(f)));
    }
    function volLabel() {
      const r = (iv.regime || "").toUpperCase();
      if (r === "LOW") return "🟢 Low";
      if (r === "HIGH") return "🔴 High";
      return "🟡 Moderate";
    }
    function sigCell(v) {
      if (v === undefined || v === null) return '<span class="badge wait">—</span>';
      if (v >= 0.55) return '<span class="badge ok">🟢 Positive</span>';
      if (v <= 0.45) return '<span class="badge err">🔴 Negative</span>';
      return '<span class="badge wait">🟡 Neutral</span>';
    }
    function edges(s) {
      const ys = (s.payoff && s.payoff.y) || [];
      const n = ys.length;
      if (n < 4) return { risingLeft: false, risingRight: false, fallingLeft: false, fallingRight: false };
      return {
        risingLeft: ys[1] - ys[3] > 0,
        risingRight: ys[n - 1] - ys[n - 3] > 0,
        fallingLeft: ys[1] - ys[3] < 0,
        fallingRight: ys[n - 1] - ys[n - 3] < 0,
      };
    }
    function dirWord(s) {
      if (s.name === "long_put" || s.name === "bear_put_spread") return "below";
      if (s.name === "strangle" || s.name === "iron_condor") return "outside";
      return "above";
    }
    function beDisplay(s) {
      const bs = (s.breakevens || []).map(b => fmtNum(b, 0));
      if (!bs.length) return "—";
      if (bs.length === 1) return (dirWord(s) === "below" ? "Below" : "Above") + " ₹" + bs[0];
      return "Outside ₹" + bs[0] + " – ₹" + bs[1];
    }
    function bePhrase(s) {
      const bs = (s.breakevens || []).map(b => fmtNum(b, 0));
      if (!bs.length) return "—";
      if (bs.length === 1) return dirWord(s) + " ₹" + bs[0];
      return "outside ₹" + bs[0] + " – ₹" + bs[1];
    }
    function bestFor(v) {
      return strategies.slice().sort((x, y) => fitFor(y.name, v) - fitFor(x.name, v))[0];
    }

    function payoffSVG(s) {
      const pay = s.payoff || { x: [], y: [] };
      const xs = pay.x || [], ys = pay.y || [];
      if (!xs.length || !ys.length || xs.length !== ys.length) return '<div class="muted small">Payoff data unavailable for this strategy.</div>';
      const W = 460, H = 230, mL = 16, mR = 14, mT = 18, mB = 26;
      const x0 = xs[0], x1 = xs[xs.length - 1];
      let ymin = Math.min.apply(null, ys), ymax = Math.max.apply(null, ys);
      if (ymax - ymin < 1) { ymax += 1; ymin -= 1; }
      const pad = (ymax - ymin) * 0.12;
      const Ymin = ymin - pad, Ymax = ymax + pad;
      const px = v => mL + (v - x0) / (x1 - x0) * (W - mL - mR);
      const py = v => mT + (Ymax - v) / (Ymax - Ymin) * (H - mT - mB);
      const zeroY = py(0);
      const regions = [];
      let cur = null;
      for (let i = 0; i < ys.length; i++) {
        const sgn = ys[i] > 0 ? 1 : ys[i] < 0 ? -1 : 0;
        if (!cur || cur.sgn !== sgn) { cur = { sgn, pts: [[px(xs[i]), py(ys[i])]] }; regions.push(cur); }
        else cur.pts.push([px(xs[i]), py(ys[i])]);
      }
      const fills = regions.filter(r => r.sgn !== 0).map(r => {
        const d = "M" + r.pts.map(p => p[0].toFixed(2) + "," + p[1].toFixed(2)).join(" L")
          + " L" + r.pts[r.pts.length - 1][0].toFixed(2) + "," + zeroY.toFixed(2)
          + " L" + r.pts[0][0].toFixed(2) + "," + zeroY.toFixed(2) + " Z";
        return '<path d="' + d + '" fill="' + (r.sgn > 0 ? "var(--ok-soft)" : "var(--err-soft)") + '" opacity="0.75"/>';
      }).join("");
      const poly = xs.map((x, i) => px(x).toFixed(2) + "," + py(ys[i]).toFixed(2)).join(" ");
      const zeroAxis = '<line x1="' + px(x0).toFixed(2) + '" y1="' + zeroY.toFixed(2) + '" x2="' + px(x1).toFixed(2) + '" y2="' + zeroY.toFixed(2) + '" stroke="var(--border-strong)" stroke-width="1" stroke-dasharray="3 3"/>';
      const spotX = px(m.spot);
      const spotM = '<line x1="' + spotX.toFixed(2) + '" y1="' + mT + '" x2="' + spotX.toFixed(2) + '" y2="' + (H - mB) + '" stroke="var(--accent)" stroke-width="1.3" stroke-dasharray="4 3"/>'
        + '<text x="' + (spotX + 3).toFixed(1) + '" y="' + (mT + 9) + '" font-size="9" fill="var(--accent)" font-weight="700">NIFTY ' + fmtNum(m.spot, 0) + '</text>';
      const bps = (s.breakevens || []).map(b => {
        const bx = px(b).toFixed(2);
        return '<line x1="' + bx + '" y1="' + (zeroY - 4) + '" x2="' + bx + '" y2="' + (zeroY + 4) + '" stroke="#fff" stroke-width="3" stroke-linecap="round"/>'
          + '<line x1="' + bx + '" y1="' + (zeroY - 4) + '" x2="' + bx + '" y2="' + (zeroY + 4) + '" stroke="var(--text)" stroke-width="1.2" stroke-linecap="round"/>'
          + '<text x="' + bx + '" y="' + (zeroY - 7) + '" font-size="9" fill="var(--text-3)" text-anchor="middle">BE ' + fmtNum(b, 0) + '</text>';
      }).join("");
      let mi = 0, li = 0;
      ys.forEach((y, i) => { if (y > ys[mi]) mi = i; if (y < ys[li]) li = i; });
      const mp = '<circle cx="' + px(xs[mi]).toFixed(2) + '" cy="' + py(ys[mi]).toFixed(2) + '" r="3.2" fill="var(--ok)"/><text x="' + px(xs[mi]).toFixed(2) + '" y="' + (py(ys[mi]) - 5).toFixed(2) + '" font-size="9" fill="var(--ok)" text-anchor="middle" font-weight="700">Max P</text>';
      const ml = '<circle cx="' + px(xs[li]).toFixed(2) + '" cy="' + py(ys[li]).toFixed(2) + '" r="3.2" fill="var(--err)"/><text x="' + px(xs[li]).toFixed(2) + '" y="' + (py(ys[li]) + 12).toFixed(2) + '" font-size="9" fill="var(--err)" text-anchor="middle" font-weight="700">Max L</text>';
      const xlabels = [[x0, "start"], [m.spot, "middle"], [x1, "end"]].map(p => '<text x="' + px(p[0]).toFixed(1) + '" y="' + (H - 8) + '" font-size="9" fill="var(--text-3)" text-anchor="' + p[1] + '">' + fmtNum(p[0], 0) + '</text>').join("");
      const ylabels = [Ymax, 0, Ymin].map(v => '<text x="4" y="' + (py(v) + 3).toFixed(1) + '" font-size="9" fill="var(--text-3)">' + fmtInr(v) + '</text>').join("");
      return '<div style="position:relative;width:100%;max-width:780px">'
        + '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;height:auto;opacity:' + (labState.today ? 0.55 : 1) + '" role="img" aria-label="Payoff chart for ' + esc(s.display) + '">'
        + fills + zeroAxis
        + '<polyline points="' + poly + '" fill="none" stroke="var(--text)" stroke-width="1.8" stroke-linejoin="round"/>'
        + spotM + bps + mp + ml + xlabels + ylabels
        + '</svg>'
        + '<div class="lab-payoff-legend">'
        + '<span><span class="dot ok"></span> Profit</span>'
        + '<span><span class="dot err"></span> Loss</span>'
        + '<span><span class="dot wait"></span> Break-even</span>'
        + '<span>📍 Current NIFTY</span>'
        + '</div>'
        + '<div class="muted small" style="margin-top:6px">X = NIFTY price at expiry · Y = profit/loss per lot (₹).</div>'
        + '</div>';
    }

    function renderDetail() {
      const s = labState.selected; if (!s) return;
      const e = edges(s);
      const profitUnlimited = e.risingLeft || e.risingRight;
      const lossUnlimited = e.fallingLeft || e.fallingRight;
      const riskHTML = s.name === "covered_call"
        ? '<div style="font-size:20px;font-weight:900">🟡 Moderate Risk</div><p class="muted small" style="margin-top:6px">Your main risk is the value of the NIFTY-linked asset you hold falling. The premium you earn offsets only part of that decline.</p>'
        : lossUnlimited
          ? '<div style="font-size:20px;font-weight:900">🔴 High Risk</div><p class="muted small" style="margin-top:6px">Your potential loss may increase substantially if the market moves against you.</p>'
          : '<div style="font-size:20px;font-weight:900">🟢 Limited Risk</div><p class="muted small" style="margin-top:6px">Your maximum loss is known before entering the trade.</p>';
      const el = document.getElementById("labDetail");
      if (!el) return;
      el.innerHTML = `
        <div class="card" style="margin-top:14px"><div class="card-title">Profit &amp; Loss — ${esc(s.display)} ${tip("These are calculated from today's option prices at expiry. Actual results before expiry can differ because of time value, implied volatility and other factors.")}</div>
          <div class="lab-pnl" style="margin-top:12px">
            <div class="lab-pnl-card ok"><div style="font-weight:700">💰 Maximum Profit</div>
              <div class="val">${profitUnlimited ? "Unlimited" : fmtInr(s.max_profit)}</div>
              ${profitUnlimited ? '<p>Profit can keep increasing if NIFTY moves further in the favourable direction (reaches ₹' + fmtNum(s.max_profit, 0) + ' at the edge of this chart).</p>' : "<p>Best-case result if the move is large enough.</p>"}
            </div>
            <div class="lab-pnl-card err"><div style="font-weight:700">🔴 Maximum Loss</div>
              <div class="val">${lossUnlimited ? "Potentially Unlimited" : fmtInr(-s.max_loss)}</div>
              ${lossUnlimited ? "<p>Your potential loss may increase substantially if the market moves against you.</p>" : "<p>Your maximum loss is known before entering the trade.</p>"}
            </div>
            <div class="lab-pnl-card info"><div style="font-weight:700">📍 Profit Starts</div>
              <div class="val">${beDisplay(s)}</div>
              <p>At expiry, NIFTY generally needs to be ${bePhrase(s)} for the strategy to move into profit.</p>
            </div>
          </div>
        </div>
        <div class="card" style="margin-top:14px">
          <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
            <div class="card-title grow" style="margin:0">⚠️ Risk Level</div>
          </div>
          ${riskHTML}
        </div>
        <div class="card" style="margin-top:14px">
          <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
            <div class="card-title grow" style="margin:0">📍 Break-even</div>
            ${tip("Break-even is the approximate price where the strategy moves from loss to profit at expiry, based on the calculated payoff.")}
          </div>
          <div class="row" style="flex-wrap:wrap;gap:12px;align-items:center;margin-top:8px">
            <div style="font-size:26px;font-weight:900">${beDisplay(s)}</div>
          </div>
          <p class="muted small" style="margin-top:8px">At expiry, the strategy generally needs NIFTY ${bePhrase(s)} to move into profit. Real-world P&amp;L before expiry can differ because of time value, implied volatility and other factors.</p>
        </div>
        <div id="labPayoff" style="margin-top:14px"></div>
        <div class="card" style="margin-top:14px"><div class="card-title">💡 In simple words</div>
          <p style="font-size:16px;line-height:1.6;margin:8px 0 0;color:var(--text-2)">${esc(META[s.name].simple)}</p>
        </div>`;
      renderPayoff();
    }

    function renderPayoff() {
      const el = document.getElementById("labPayoff");
      if (!el || !labState.selected) return;
      el.innerHTML = `
        <div class="card">
          <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
            <div class="card-title grow" style="margin:0">📈 Profit / Loss ${labState.today ? "(Today)" : "(At Expiry)"}</div>
            <div class="lab-seg" role="tablist" aria-label="Payoff horizon">
              <button data-pay="expiry" class="${labState.today ? "" : "sel"}">At Expiry</button>
              <button data-pay="today" class="${labState.today ? "sel" : ""}">Today</button>
            </div>
          </div>
          ${payoffSVG(labState.selected)}
          <div id="labTodayNote" class="hint" style="margin-top:8px">${labState.today ? "Same-day P&amp;L depends on live implied volatility and time decay (theta), which we do not estimate accurately — so the curve above is still the at-expiry payoff. Your intraday result will differ." : ""}</div>
        </div>`;
      $$("#labPayoff [data-pay]").forEach(b => b.addEventListener("click", () => {
        labState.today = b.dataset.pay === "today";
        renderPayoff();
      }));
    }

    function selectStrategy(name) {
      const s = byName[name];
      if (!s) return;
      labState.selected = s;
      mktAudit("LAB select " + name);
      renderDetail();
      renderCompare();
    }

    function renderRec() {
      const el = document.getElementById("labRec");
      if (!el) return;
      const v = VIEWS[labState.view];
      const s = bestFor(labState.view);
      const fit = fitFor(s.name, labState.view);
      el.innerHTML = `
        <div class="card lab-rec" style="margin-top:14px">
          <div class="card-title">🎯 Strategy Recommendation</div>
          ${labState.override ? '<div class="badge wait" style="margin-top:8px">You selected: ' + esc(v.hero) + ' — overrides the market view (' + esc(VIEWS[autoView].hero) + '). <button class="btn btn-soft" id="labResetView" style="margin-left:8px">Use market view</button></div>' : ""}
          <div class="row" style="flex-wrap:wrap;gap:16px;margin-top:10px;align-items:center">
            <div class="grow">
              <div class="lab-rec-name">⭐ ${META[s.name].emoji} ${esc(s.display)}</div>
              <div class="muted" style="margin-top:2px">Market view: ${v.heroEmoji} ${esc(v.hero)}</div>
              <div class="row" style="gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap">
                <div class="grow" style="max-width:320px"><div class="muted small" style="margin-bottom:4px">Strategy Fit ${tip("A heuristic score (0–100) of how well the strategy matches the market view. It is a match score, not a profit forecast.")}</div>
                  <div class="lab-bar ${v.color}"><i style="width:${fit}%"></i></div></div>
                <div style="font-size:20px;font-weight:900">${fit} / 100</div>
              </div>
              <div class="muted small" style="margin-top:10px">This strategy currently matches the selected market scenario — it is not a guarantee of profit.</div>
            </div>
          </div>
          <div class="lab-rec-why"><b>Why this strategy?</b> ${esc(WHY[s.name])}</div>
        </div>`;
      const reset = document.getElementById("labResetView");
      if (reset) reset.addEventListener("click", () => { labState.view = autoView; labState.override = false; renderViews(); renderRec(); renderFiltered(); renderAI(); });
    }

    function renderCompare() {
      const el = document.getElementById("labCompare");
      if (!el) return;
      el.innerHTML = strategies.map(s => `
        <div class="lab-cmp${labState.selected && labState.selected.name === s.name ? " sel" : ""}" data-lab-strat="${esc(s.name)}" role="button" tabindex="0" aria-label="Show ${esc(s.display)} details">
          <h4>${META[s.name].emoji} ${esc(s.display)}</h4>
          <div class="m">
            <div class="spread"><span>View</span><b>${esc(META[s.name].view)}</b></div>
            <div class="spread"><span>Risk</span><b>${META[s.name].risk}</b></div>
            <div class="spread"><span>Best when</span><b>${esc(META[s.name].when)}</b></div>
            <div class="spread"><span>Fit (current view)</span><b>${fitFor(s.name, labState.view)} / 100</b></div>
          </div>
        </div>`).join("");
      $$("#labCompare [data-lab-strat]").forEach(c => c.addEventListener("click", () => selectStrategy(c.dataset.labStrat)));
      $$("#labCompare [data-lab-strat]").forEach(c => c.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectStrategy(c.dataset.labStrat); }
      }));
    }

    function renderFiltered() {
      const el = document.getElementById("labFiltered");
      if (!el) return;
      const names = VIEW_STRATS[labState.view] || [];
      el.innerHTML = '<div class="muted small" style="margin:10px 0 6px">Strategies matching your view — ' + esc(VIEWS[labState.view].hero) + ':</div>' + names.map(n => {
        const s = byName[n]; if (!s) return "";
        const fit = fitFor(n, labState.view);
        return '<div class="lab-fit-row card" data-lab-strat="' + esc(n) + '" role="button" tabindex="0" style="cursor:pointer">'
          + '<b>' + META[n].emoji + ' ' + esc(s.display) + '</b>'
          + '<span class="badge">Fit ' + fit + '/100</span>'
          + '<span class="muted small grow">' + esc(META[n].when) + '</span>'
          + '<span class="muted small">' + esc(META[n].simple) + '</span>'
          + '</div>';
      }).join("");
      $$("#labFiltered [data-lab-strat]").forEach(c => c.addEventListener("click", () => selectStrategy(c.dataset.labStrat)));
    }

    function renderViews() {
      const el = document.getElementById("labViews");
      if (!el) return;
      el.innerHTML = Object.keys(VIEWS).map(k => {
        const v = VIEWS[k];
        return '<button data-view="' + k + '" class="' + (labState.view === k ? "sel" : "") + '">' + v.heroEmoji + " " + esc(v.label) + '</button>';
      }).join("");
      $$("#labViews [data-view]").forEach(b => b.addEventListener("click", () => {
        labState.view = b.dataset.view;
        labState.override = b.dataset.view !== autoView;
        renderViews();
        renderRec();
        renderFiltered();
        renderAI();
      }));
    }

    function renderAI() {
      const el = document.getElementById("labAI");
      if (!el) return;
      const v = VIEWS[labState.view];
      const rec = labState.selected;
      const pcrTxt = pcr.pcr_oi === undefined || pcr.pcr_oi === null ? "" : (pcr.pcr_oi > 1 ? "Put buyers have been slightly more active" : "Call buyers have been slightly more active") + " (Put/Call Ratio " + pcr.pcr_oi + "). ";
      const ivTxt = iv.atm_iv ? "Implied volatility is " + (iv.atm_iv * 100).toFixed(1) + "% — " + ((iv.regime || "").toUpperCase() === "LOW" ? "the market is not pricing large swings right now." : (iv.regime || "").toUpperCase() === "HIGH" ? "the market is pricing larger swings right now." : "a normal level of expected movement.") + " " : "";
      const srTxt = "Option-chain support is near " + fmtNum(sup0, 0) + " and resistance near " + fmtNum(res0, 0) + ". ";
      const overrideNote = labState.override ? "Based on the scenario you selected, " : "Based on the current scenario, ";
      el.innerHTML = '<div class="card lab-ai" style="margin-top:14px"><div class="card-title">🤖 AI Market Summary</div><div class="lab-ai-body">'
        + '<p><b>' + v.heroEmoji + " NIFTY is currently " + v.hero + ".</b></p>"
        + "<p>" + pcrTxt + ivTxt + srTxt + "</p>"
        + "<p><b>Base scenario:</b> " + v.base + ". <b>Bullish trigger:</b> sustained move above " + fmtNum(res0, 0) + ". <b>Bearish trigger:</b> break below " + fmtNum(sup0, 0) + ".</p>"
        + "<p>" + overrideNote + "a <b>" + esc(rec.display) + "</b> has a better-defined risk profile than a naked position because " + esc(META[rec.name].whyFit) + ".</p>"
        + '</div><div class="muted small" style="margin-top:8px">Generated from the live option chain. Analytical estimate, not investment advice.</div></div>';
    }

    function renderAdvanced() {
      const el = document.getElementById("labAdvanced");
      if (!el) return;
      const byStrike = {};
      (a.contracts || []).forEach(c => {
        if (!byStrike[c.strike]) byStrike[c.strike] = {};
        byStrike[c.strike][c.option_type] = c;
      });
      const strikes = Object.keys(byStrike).map(Number).sort((x, y) => Math.abs(x - m.spot) - Math.abs(y - m.spot)).slice(0, 6).sort((x, y) => x - y);
      const rows = strikes.map(k => {
        const ce = byStrike[k].CE, pe = byStrike[k].PE;
        const cell = (c) => c ? fmtNum(c.ltp, 1) + '<div class="muted small">IV ' + (c.iv ? (c.iv * 100).toFixed(1) + "%" : "—") + " · ΔOI " + (c.change_oi >= 0 ? "+" : "") + fmtNum(c.change_oi, 0) + "</div>" : "—";
        return '<tr><td><b>' + fmtNum(k, 0) + '</b></td><td>' + cell(ce) + '</td><td>' + cell(pe) + '</td></tr>';
      }).join("");
      const greeksTip = (name, g, t) => name + " " + tip(t);
      el.innerHTML = `
        <div class="card" style="margin-top:14px"><div class="card-title">⚙️ Advanced — Option Chain &amp; Metrics</div>
          <div class="row" style="flex-wrap:wrap;gap:8px;margin-top:8px;font-size:12.5px;color:var(--text-3)">
            ${greeksTip("IV", "", "Implied Volatility shows how much movement the options market is expecting.")}
            ${greeksTip("OI", "", "Open Interest shows the number of active option contracts.")}
            ${greeksTip("PCR", "", "Put/Call Ratio compares put activity with call activity.")}
            ${greeksTip("Delta", "", "Delta estimates how much an option's price may change when NIFTY moves by 1 point.")}
            ${greeksTip("Gamma", "", "Gamma shows how much Delta itself changes as NIFTY moves.")}
            ${greeksTip("Theta", "", "Theta represents the effect of time passing on an option's value.")}
            ${greeksTip("Vega", "", "Vega shows how an option's price reacts to a 1% change in implied volatility.")}
          </div>
          <div class="table-wrap" style="margin-top:10px"><table class="tbl">
            <thead><tr><th>Strike</th><th>Call (CE) ${tip("A call gives the right to buy at the strike.")}</th><th>Put (PE) ${tip("A put gives the right to sell at the strike.")}</th></tr></thead>
            <tbody>${rows}</tbody>
          </table></div>
          <div class="grid-2" style="margin-top:12px;gap:10px">
            <div class="card" style="padding:12px"><div class="card-sub" style="margin-bottom:6px">Chain metrics</div>
              <div class="spread"><span class="muted">PCR (OI)</span><span>${pcr.pcr_oi ?? "—"}</span></div>
              <div class="spread"><span class="muted">PCR (Volume)</span><span>${pcr.pcr_volume ?? "—"}</span></div>
              <div class="spread"><span class="muted">ATM IV</span><span>${iv.atm_iv ? (iv.atm_iv * 100).toFixed(1) + "%" : "—"}</span></div>
              <div class="spread"><span class="muted">IV regime</span><span>${esc(iv.regime || "—")}</span></div>
              <div class="spread"><span class="muted">Max pain</span><span>${fmtNum((an.max_pain || {}).max_pain, 0)}</span></div>
              <div class="spread"><span class="muted">Expected move</span><span>${fmtNum(exp.lower, 0)} – ${fmtNum(exp.upper, 0)}</span></div>
              <div class="spread"><span class="muted">CE OI / PE OI</span><span>${fmtNum((an.oi || {}).total_ce_oi, 0)} / ${fmtNum((an.oi || {}).total_pe_oi, 0)}</span></div>
              <div class="spread"><span class="muted">Liquidity</span><span>${esc((an.liquidity || {}).grade || "—")}</span></div>
              <div class="spread"><span class="muted">Unusual activity</span><span>${(an.unusual_activity || []).length} item(s)</span></div>
              <div class="spread"><span class="muted">Data quality</span><span>${esc(quality)}</span></div>
            </div>
            <div class="card" style="padding:12px"><div class="card-sub" style="margin-bottom:6px">Signal components (0–1) ${tip("Raw sub-scores behind the market view. Higher = more positive for the bullish side.")}</div>
              ${Object.keys((sig.components || {})).map(k => '<div class="spread"><span class="muted">' + esc(k) + '</span><span>' + Number(sig.components[k]).toFixed(2) + " × w" + Number((sig.weights || {})[k] || 0).toFixed(2) + '</span></div>').join("")}
              <div class="spread" style="margin-top:6px"><span class="muted">Signal score</span><b>${sig.score}/100 · ${esc(sig.label)}</b></div>
            </div>
          </div>
          <div class="muted small" style="margin-top:8px">${esc((sig.disclaimer || "") + " " + (exp.label || ""))}</div>
        </div>`;
    }

    function renderSnapshotChange() {
      const el = document.getElementById("labChange");
      if (!el) return;
      const hdr = {};
      const tok = window.API_TOKEN || (localStorage && localStorage.getItem("api_token")) || "";
      if (tok) hdr["X-API-Key"] = tok;
      fetch("/api/v1/market/indices", { headers: hdr })
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (!d) return;
          const arr = Array.isArray(d) ? d : (d.indices || []);
          const n = arr.find(x => (x.symbol || "") === "NIFTY 50") || arr.find(x => /^NIFTY(\s|$)/.test(x.symbol || ""));
          if (!n || n.change === undefined) return;
          const cls = n.change >= 0 ? "up" : "down";
          el.innerHTML = '<span class="' + cls + '">' + (n.change >= 0 ? "+" : "") + fmtNum(n.change, 2) + " (" + (n.change >= 0 ? "+" : "") + n.change_pct.toFixed(2) + "%)</span>";
          el.classList.remove("muted");
        })
        .catch(() => { el.innerHTML = "—"; });
    }

    const comp = (sig.components) || {};
    const v = VIEWS[autoView];
    const supW = Math.max(4, (sup0 - majLow) / (majHigh - majLow) * 100);
    const rangeW = Math.max(4, (res0 - sup0) / (majHigh - majLow) * 100);
    const bullW = Math.max(4, 100 - supW - rangeW);
    const spotPct = Math.max(3, Math.min(97, (m.spot - majLow) / (majHigh - majLow) * 100));
    const staleBadge = stale ? '<span class="lab-stale">⚠️ Market data delayed</span>' : "";
    const supStrong = (srz.support || []).length ? '<span class="badge ok">🟢 Strong</span>' : '<span class="badge wait">🟡 Light</span>';
    const resStrong = (srz.resistance || []).length ? '<span class="badge wait">🟡 Important</span>' : '<span class="badge ok">🟢 Open</span>';

    body.innerHTML = `
      <div class="lab">
        <div class="row" style="flex-wrap:wrap;gap:14px;align-items:flex-start">
          <div class="grow">
            <h1 class="lab-title">🧠 NIFTY Strategy Lab</h1>
            <div class="muted" style="margin-top:2px">Understand the market first. Choose the strategy second.</div>
            <div class="lab-expiry">NIFTY · <b>${esc(m.expiry)}</b> Expiry</div>
          </div>
          <div class="lab-mode" id="labMode" role="tablist" aria-label="View mode">
            <button data-lab-mode="beginner" class="sel">👤 Beginner</button>
            <button data-lab-mode="advanced">⚙️ Advanced</button>
          </div>
        </div>

        <div class="card" style="margin-top:16px">
          <div class="row" style="flex-wrap:wrap;gap:8px;align-items:center">
            <div class="card-title grow" style="margin:0">📊 NIFTY Market Snapshot</div>
            <span class="muted small">Data updated: ${esc(m.timestamp)}</span> ${staleBadge}
          </div>
          <div class="lab-kpis">
            <div class="lab-kpi"><div class="lab-kpi-label">NIFTY Spot</div><div class="lab-kpi-value">₹${fmtNum(m.spot, 2)}</div><div class="muted small">${esc(m.market_state)} · ${esc(m.source)}</div></div>
            <div class="lab-kpi"><div class="lab-kpi-label">Today's Change</div><div class="lab-kpi-value muted" id="labChange">…</div><div class="muted small">vs previous close</div></div>
            <div class="lab-kpi"><div class="lab-kpi-label">Market Trend ${tip("Derived from option-chain signals (PCR, OI positioning, momentum). It is a probabilistic read, not a prediction.")}</div><div class="lab-kpi-value">${v.heroEmoji} ${esc(v.hero)}</div><div class="muted small">based on the option chain</div></div>
            <div class="lab-kpi"><div class="lab-kpi-label">Confidence ${tip("A 0–100 score for how consistently the current option-chain signals agree. Lower confidence means the picture is more mixed.")}</div><div class="lab-kpi-value">${sig.score} / 100</div><div class="muted small">${esc(sig.confidence || "—")} confidence</div></div>
            <div class="lab-kpi"><div class="lab-kpi-label">Volatility ${tip("Volatility shows how much movement the options market expects. Low = cheaper options and smaller expected swings; High = bigger expected swings.")}</div><div class="lab-kpi-value">${volLabel()}</div><div class="muted small">ATM IV ${iv.atm_iv ? (iv.atm_iv * 100).toFixed(1) + "%" : "—"}</div></div>
          </div>
          <div class="muted small" style="margin-top:10px">Put/Call Ratio ${pcr.pcr_oi ?? "—"} ${tip("Put/Call Ratio compares put activity with call activity. Above ~1 = more put activity; below ~1 = more call activity.")} shows the balance between put and call activity.</div>
        </div>

        <div class="card lab-outlook ${v.color}" style="margin-top:14px">
          <div class="card-title">🔮 NIFTY Outlook</div>
          <div class="lab-outlook-big">${v.heroEmoji} ${esc(v.hero)}</div>
          <div class="lab-outlook-mean">${esc(v.meaning)}</div>
          <div class="row" style="gap:12px;align-items:center;margin-top:12px;flex-wrap:wrap">
            <div class="grow" style="max-width:460px">
              <div class="muted small" style="margin-bottom:4px">Confidence</div>
              <div class="lab-bar ${v.color}"><i style="width:${sig.score}%"></i></div>
            </div>
            <div style="font-size:22px;font-weight:900">${sig.score} / 100</div>
          </div>
          <div class="muted small" style="margin-top:10px">This is an analysis of current signals — it is not a guarantee of what NIFTY will do next.</div>
        </div>

        <div class="card" style="margin-top:14px">
          <div class="card-title">🎯 Possible NIFTY Scenarios</div>
          <div class="lab-zonebar" role="img" aria-label="Scenario map">
            <div class="zone bear" style="flex:0 0 ${supW}%">🔴<span>Below ${fmtNum(sup0, 0)}</span><small>Bearish Zone</small></div>
            <div class="zone range" style="flex:0 0 ${rangeW}%">🟡<span>${fmtNum(sup0, 0)} – ${fmtNum(res0, 0)}</span><small>Range / Sideways</small></div>
            <div class="zone bull" style="flex:0 0 ${bullW}%">🟢<span>Above ${fmtNum(res0, 0)}</span><small>Bullish Zone</small></div>
            <div class="spotmark" style="left:${spotPct}%"><b>NIFTY ${fmtNum(m.spot, 0)}</b></div>
          </div>
          <div class="muted small" style="margin-top:8px">Levels are estimates from the option chain (OI + implied move) — not guaranteed targets.</div>
          <div class="lab-zone-grid">
            <div class="lab-zone ok"><h4>🟢 Bullish Scenario</h4><p>If NIFTY breaks and sustains above ${fmtNum(res0, 0)}, the upside scenario becomes stronger.</p></div>
            <div class="lab-zone wait"><h4>🟡 Range Scenario</h4><p>If NIFTY remains between ${fmtNum(sup0, 0)} and ${fmtNum(res0, 0)}, the market may move sideways.</p></div>
            <div class="lab-zone err"><h4>🔴 Bearish Scenario</h4><p>If NIFTY breaks below ${fmtNum(sup0, 0)}, downside risk increases.</p></div>
          </div>
        </div>

        <div class="lab-sr" style="margin-top:14px">
          <div class="lab-sr-card ok"><div style="font-weight:700">🟢 Support</div>
            <div class="num">${fmtNum(sup0, 0)}</div>
            <p>A price area where buying interest may appear ${tip("Support is estimated from where option open interest (OI) is concentrated. It is an area of possible buying, not a guaranteed floor.")}</p>
            <div class="maj">Major support: ${fmtNum(majLow, 0)}</div>
          </div>
          <div class="lab-sr-card err"><div style="font-weight:700">🔴 Resistance</div>
            <div class="num">${fmtNum(res0, 0)}</div>
            <p>A price area where selling pressure may appear ${tip("Resistance is estimated from where option open interest (OI) is concentrated. It is an area of possible selling, not a guaranteed ceiling.")}</p>
            <div class="maj">Major resistance: ${fmtNum(majHigh, 0)}</div>
          </div>
        </div>

        <div class="card" style="margin-top:14px">
          <div class="card-title">🔍 Why this market view?</div>
          <div class="table-wrap" style="margin-top:10px"><table class="tbl lab-signal">
            <thead><tr><th>Signal</th><th>Reading</th><th>What it means</th></tr></thead>
            <tbody>
              <tr><td>Price Trend ${tip("Whether option-chain momentum is trending up or down.")}</td><td>${sigCell(comp.momentum)}</td><td class="muted">Prices and positioning are trending ${(comp.momentum || 0) >= 0.55 ? "up" : (comp.momentum || 0) <= 0.45 ? "down" : "sideways"}</td></tr>
              <tr><td>Option Positioning ${tip("How option buyers are positioned, measured through the Put/Call Ratio.")}</td><td>${sigCell(comp.positioning)}</td><td class="muted">Buyers favour ${(comp.positioning || 0) >= 0.55 ? "calls" : (comp.positioning || 0) <= 0.45 ? "puts" : "a balance of both"}</td></tr>
              <tr><td>Activity / Breadth ${tip("Whether fresh activity is concentrated in calls or puts.")}</td><td>${sigCell(comp.activity)}</td><td class="muted">Fresh activity is ${(comp.activity || 0) >= 0.55 ? "in calls" : (comp.activity || 0) <= 0.45 ? "in puts" : "balanced"}</td></tr>
              <tr><td>Support ${tip("Buying interest estimated from option OI concentration.")}</td><td>${supStrong}</td><td class="muted">Buyers active near support</td></tr>
              <tr><td>Resistance ${tip("Selling pressure estimated from option OI concentration.")}</td><td>${resStrong}</td><td class="muted">Upside may face selling</td></tr>
              <tr><td>Volatility ${tip("How large a move the options market expects.")}</td><td><span class="badge">${volLabel()}</span></td><td class="muted">${(iv.regime || "").toUpperCase() === "LOW" ? "Smaller moves expected — cheaper options" : (iv.regime || "").toUpperCase() === "HIGH" ? "Larger moves expected — pricier options" : "Moderate expected movement"}</td></tr>
            </tbody>
          </table></div>
          <details style="margin-top:10px"><summary class="muted small" style="cursor:pointer">Advanced details ▼</summary>
            <div class="grid-2" style="margin-top:8px;gap:10px">
              <div class="card" style="padding:12px"><div class="card-sub" style="margin-bottom:6px">Signal components (0–1) ${tip("Raw sub-scores behind the view. Higher = more bullish. Weights sum to 1.")}</div>
                ${Object.keys(comp).map(k => '<div class="spread"><span class="muted">' + esc(k) + '</span><span>' + Number(comp[k]).toFixed(2) + " × w" + Number((sig.weights || {})[k] || 0).toFixed(2) + '</span></div>').join("")}
                <div class="spread" style="margin-top:6px"><span class="muted">Signal score</span><b>${sig.score}/100 · ${esc(sig.label)}</b></div>
              </div>
              <div class="card" style="padding:12px"><div class="card-sub" style="margin-bottom:6px">Chain snapshot</div>
                <div class="spread"><span class="muted">PCR (OI)</span><span>${pcr.pcr_oi ?? "—"}</span></div>
                <div class="spread"><span class="muted">PCR (Volume)</span><span>${pcr.pcr_volume ?? "—"}</span></div>
                <div class="spread"><span class="muted">ATM IV</span><span>${iv.atm_iv ? (iv.atm_iv * 100).toFixed(1) + "%" : "—"}</span></div>
                <div class="spread"><span class="muted">Max pain</span><span>${fmtNum((an.max_pain || {}).max_pain, 0)}</span></div>
                <div class="spread"><span class="muted">Data quality</span><span>${esc(quality)}</span></div>
              </div>
            </div>
            <div class="muted small" style="margin-top:8px">${esc(sig.disclaimer || "")}</div>
          </details>
        </div>

        <div id="labRec"></div>
        <div id="labDetail"></div>

        <div class="card" style="margin-top:14px">
          <div class="card-title">🔎 Compare Strategies</div>
          <div class="lab-compare" id="labCompare"></div>
          <div class="muted small" style="margin-top:10px">Each strategy below is shown with its market view, risk and best use. Click one to see its full payoff.</div>
        </div>

        <div class="card" style="margin-top:14px">
          <div class="card-title">Which strategy should I look at?</div>
          <div class="muted small">What do you expect NIFTY to do? Pick a view and matching strategies appear below.</div>
          <div class="lab-views" id="labViews"></div>
          <div id="labFiltered"></div>
        </div>

        <div id="labAI"></div>

        <div class="lab-disclaimer">⚠️ <b>Risk Warning:</b> Options involve significant risk and are not suitable for every investor. Market scenarios and strategy scores are analytical estimates, not guaranteed predictions or investment advice. Review the complete payoff, costs, liquidity and risk before taking any position.</div>

        <div id="labAdvanced" hidden></div>
      </div>`;

    labState.selected = bestFor(autoView);
    renderSnapshotChange();
    renderRec();
    renderDetail();
    renderCompare();
    renderFiltered();
    renderViews();
    renderAI();

    $$("#labMode button").forEach(btn => btn.addEventListener("click", () => {
      $$("#labMode button").forEach(x => x.classList.remove("sel"));
      btn.classList.add("sel");
      labState.advanced = btn.dataset.labMode === "advanced";
      const adv = document.getElementById("labAdvanced");
      if (adv) { adv.hidden = !labState.advanced; if (labState.advanced) renderAdvanced(); }
    }));
  }

  /* ---------------- Option Intelligence layer ---------------- */
  function optIntel(body) {
    const holder = document.createElement("div");
    holder.innerHTML = mktLoading();
    body.appendChild(holder);
    const state = window.__optIntelState || { underlying: "NIFTY", expiry: "" };
    const q = window.OptionsClient.qs({ underlying: state.underlying, expiry: state.expiry, record: "true" });
    const url = "/api/v1/options/intel" + q;

    const iqBadge = (q) => q && q.grade ? '<span class="badge ' + (q.grade === "HIGH" ? "ok" : q.grade === "MEDIUM" ? "wait" : "err") + '">Liq ' + q.grade + ' · ' + q.score + '/100</span>' : "";
    const ivrBadge = (v) => {
      if (!v || v.error) return '<span class="badge wait">IV rank —</span>';
      const r = v.iv_rank;
      return '<span class="badge ' + (r > 70 ? "err" : r > 40 ? "wait" : "ok") + '">IV rank ' + r + '%</span>';
    };
    const crushBadge = (c) => c && c.crush_detected
      ? '<span class="badge err">IV CRUSH ' + c.iv_change_pct + '%</span>'
      : c && c.iv_change_pct !== undefined
        ? '<span class="badge ok">IV ' + (c.iv_change_pct >= 0 ? "+" : "") + c.iv_change_pct + '% Δ</span>'
        : '<span class="badge wait">Crush —</span>';

    const _hdr = {};
    const _tok = window.API_TOKEN || (localStorage && localStorage.getItem("api_token")) || "";
    if (_tok) _hdr["X-API-Key"] = _tok;
    fetch(url, { headers: _hdr }).then(r => r.ok ? r.json() : Promise.reject(new Error("intel " + r.status))).then(d => {
      holder.innerHTML = `
        <div class="mkt-banner">
          <div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
            <span class="avatar ai lg" style="font-size:20px">🧠</span>
            <div class="grow"><b style="font-size:17px">Decision Intel · ${esc(d.meta.underlying)}</b>
            <div class="muted small">${esc(d.meta.expiry)} · ${esc(d.meta.market_state)} · ${esc(d.meta.timestamp)}</div></div>
            <span class="badge ${(d.no_trade && d.no_trade.tradeable) ? "ok" : "err"}">${(d.no_trade && d.no_trade.decision) || "—"}</span>
          </div>
        </div>
        <div class="grid-2" style="margin-top:12px">
          <div class="card"><div class="card-title">Futures (ESTIMATED model)</div>
            <div class="spread"><span class="muted">Future</span><b>${fmtNum(d.futures.future, 2)}</b></div>
            <div class="spread"><span class="muted">Basis</span><span>${fmtNum(d.futures.basis_points, 2)} pts (${d.futures.basis_pct}%)</span></div>
            <div class="spread"><span class="muted">Carry / DTE</span><span>${d.futures.carry_rate}% · ${d.futures.dte}d</span></div>
            <div class="hint" style="margin-top:8px">Labelled ESTIMATED — cost-of-carry, not a broker quote.</div>
          </div>
          <div class="card"><div class="card-title">Volatility</div>
            <div class="row" style="flex-wrap:wrap;gap:8px">
              ${ivrBadge(d.vol_stats && d.vol_stats.rank)}
              ${crushBadge(d.vol_stats && d.vol_stats.crush)}
              <span class="badge">Skew ${(d.vol_stats && d.vol_stats.skew && d.vol_stats.skew.skew) || "—"}</span>
              <span class="badge">Smile ${(d.vol_stats && d.vol_stats.smile && d.vol_stats.smile.smile) || "—"}</span>
            </div>
            <div class="muted small" style="margin-top:8px">Rank from recorded snapshots; more history = more meaningful.</div>
          </div>
          <div class="card"><div class="card-title">Liquidity / Execution</div>
            ${iqBadge(d.liquidity && d.liquidity.atm)}
            <div class="spread" style="margin-top:8px"><span class="muted">Spread</span><span>${fmtNum(d.liquidity.atm.spread, 2)} (${fmtNum(d.liquidity.atm.spread_pct, 2)}%)</span></div>
            <div class="spread"><span class="muted">Depth / Vol</span><span>${fmtNum(d.liquidity.atm.depth, 0)} / ${fmtNum(d.liquidity.atm.volume, 0)}</span></div>
          </div>
          <div class="card"><div class="card-title">Velocity (snapshot window)</div>
            <div class="spread"><span class="muted">ATM IV Δ/hr</span><b class="${(d.velocity.atm_iv_change || 0) < 0 ? "down" : "up"}">${d.velocity.atm_iv_change === null ? "—" : fmtNum(d.velocity.atm_iv_change, 4)}</b></div>
            <div class="spread"><span class="muted">CE / PE OI Δ/hr</span><span>${fmtNum(d.velocity.ce_oi_change, 0)} / ${fmtNum(d.velocity.pe_oi_change, 0)}</span></div>
            <div class="spread"><span class="muted">Spot Δ/hr</span><span>${fmtNum(d.velocity.spot_change, 2)}</span></div>
            <div class="hint" style="margin-top:8px">Points: ${d.velocity.points} snapshot(s) in window.</div>
          </div>
        </div>
        <div class="card" style="margin-top:12px"><div class="card-title">Decision Card</div>
          <div class="row" style="flex-wrap:wrap;gap:8px">
            <span class="badge">Trade quality ${d.trade_quality ? (d.trade_quality.score + "/100 · " + d.trade_quality.grade) : "—"}</span>
            <span class="badge">Breadth ${d.breadth && d.breadth.regime_read}</span>
            ${(d.events || []).map(e => '<span class="badge wait">' + esc(e.name) + (e.typical_iv_impact ? " ±" + (e.typical_iv_impact * 100).toFixed(0) + "% IV" : "") + '</span>').join("")}
          </div>
          ${d.no_trade && d.no_trade.reasons && d.no_trade.reasons.length
            ? '<div class="alert" style="margin-top:10px"><b>NO TRADE</b> — ' + d.no_trade.message + '<ul>' + d.no_trade.reasons.map(r => '<li>' + esc(r.reason) + ' · ' + esc(r.detail) + '</li>').join("") + '</ul></div>'
            : '<div class="hint" style="margin-top:10px">' + esc((d.no_trade && d.no_trade.narrative) || "No explicit blocker.") + '</div>'}
        </div>
        <div class="row" style="margin-top:12px;flex-wrap:wrap;gap:8px">
          <span class="muted small" style="font-size:12px">${esc(d.monitoring.failover.note || "")}</span>
          <span class="badge ${d.breadth && d.breadth.indices_breadth >= 0.5 ? "ok" : ""}">Indices breadth ${d.breadth ? d.breadth.indices_breadth : 0}</span>
        </div>`;
    }).catch(e => { holder.innerHTML = mktError(e); });
  }

  function optPaper(body, a) {
    const m = a.meta;
    body.innerHTML = `
      ${optHeader(m, a.analytics)}
      <div class="card" style="margin-top:16px"><div class="card-title">Paper option trade (simulated — no real money)</div>
        <div class="row" style="flex-wrap:wrap;gap:12px">
          <div class="field grow" style="margin:0"><label>Expiry</label><input id="opExpiry" value="' + esc(m.expiry) + '"></div>
          <div class="field grow" style="margin:0"><label>Strike</label><input id="opStrike" type="number" placeholder="' + fmtNum(m.spot, 0) + '"></div>
          <div class="field grow" style="margin:0"><label>Type</label><select id="opType"><option value="CE">Call (CE)</option><option value="PE">Put (PE)</option></select></div>
          <div class="field grow" style="margin:0"><label>Action</label><select id="opAction"><option value="BUY">Buy</option><option value="SELL">Sell (short)</option></select></div>
          <div class="field grow" style="margin:0"><label>Qty (lots × ${esc(m.underlying)})</label><input id="opQty" type="number" value="1" min="1"></div>
          <div class="field grow" style="margin:0"><label>Entry price (₹)</label><input id="opEntry" type="number" step="0.05"></div>
          <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="opOpen">Open paper trade</button></div>
        </div>
        <div class="hint" style="margin-top:10px">PAPER TRADE only. There is no live option routing in this system.</div>
      </div>
      <div id="optPaperList" style="margin-top:16px"></div>`;
    const list = $("#optPaperList");
    const render = () => {
      list.innerHTML = mktLoading();
      window.OptionsClient.paperPositions().then(ps => {
        list.innerHTML = '<div class="card"><div class="card-title">Paper positions</div>' +
          '<table class="tbl"><thead><tr><th>ID</th><th>Contract</th><th>Action</th><th>Qty</th><th>Entry</th><th>Status</th><th>P&L</th></tr></thead><tbody>' +
          (ps.map(p => '<tr><td class="muted small">' + esc(p.id) + '</td><td><b>' + esc(p.underlying + " " + p.strike + p.option_type + " " + p.expiry) + '</b></td><td><span class="badge ' + (p.action === "BUY" ? "ok" : "err") + '">' + p.action + '</span></td><td>' + p.quantity + '</td><td>' + fmtNum(p.entry_price, 2) + '</td><td class="muted">' + p.status + '</td><td class="' + (p.pnl >= 0 ? "up" : p.pnl < 0 ? "down" : "") + '">' + fmtNum(p.pnl, 0) + '</td></tr>').join("") ||
          '<tr><td colspan="7" class="muted">No paper option positions yet.</td></tr>') +
          '</tbody></table></div>';
      }).catch(e => { list.innerHTML = mktError(e); });
    };
    $("#opOpen").addEventListener("click", () => {
      const payload = {
        underlying: a.meta.underlying,
        expiry: $("#opExpiry").value.trim(),
        strike: parseFloat($("#opStrike").value),
        option_type: $("#opType").value,
        action: $("#opAction").value,
        quantity: parseInt($("#opQty").value, 10),
        entry_price: parseFloat($("#opEntry").value),
      };
      if (!payload.expiry || !payload.strike || !payload.entry_price || payload.quantity < 1) { toast("Option paper", "Fill expiry, strike, quantity and entry price.", "err"); return; }
      window.OptionsClient.paperOpen(payload).then(r => { toast("Option paper", "Paper position opened " + r.opened.id, "ok"); mktAudit("OPTIONS PAPER " + payload.action + " " + payload.strike + payload.option_type); render(); }).catch(e => toast("Option paper", e.message, "err"));
    });
    render();
  }

  function optBacktest(body, a) {
    body.innerHTML = `
      <div class="card"><div class="card-title">Option strategy backtest (simulated path)</div>
        <div class="row" style="flex-wrap:wrap;gap:12px">
          <div class="field grow" style="margin:0"><label>Strategy</label><select id="obStrat"><option value="iron_condor">Iron Condor</option><option value="strangle">Strangle</option><option value="bull_call_spread">Bull Call Spread</option><option value="bear_put_spread">Bear Put Spread</option><option value="covered_call">Covered Call</option></select></div>
          <div class="field grow" style="margin:0"><label>Notional (₹)</label><input id="obNotional" type="number" value="100000" min="1000"></div>
          <div class="field grow" style="margin:0"><label>Hold days</label><input id="obDays" type="number" value="30" min="1" max="250"></div>
          <div class="field grow" style="margin:0"><label>Premium %</label><input id="obPrem" type="number" value="4" min="0.5" max="50" step="0.5"></div>
          <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="obRun">Run backtest</button></div>
        </div>
        <div class="hint" style="margin-top:10px">Simulated path using a configured win-rate with costs and slippage. Not a real result.</div>
      </div>
      <div id="obBody" style="margin-top:16px"></div>`;
    const run = () => {
      const b = $("#obBody");
      b.innerHTML = mktLoading();
      window.OptionsClient.backtest({
        strategy: $("#obStrat").value,
        notional: parseFloat($("#obNotional").value) || 100000,
        hold_days: parseInt($("#obDays").value, 10) || 30,
        premium_pct: (parseFloat($("#obPrem").value) || 4) / 100,
      }).then(r => {
        mktAudit("OPTIONS backtest " + r.strategy + " -> " + r.net_pnl);
        b.innerHTML = `
          <div class="dash-grid">
            ${mktStat("Gross P&L", fmtInr(r.gross_pnl), "win rate " + r.win_rate + "")}
            ${mktStat("Costs", fmtInr(r.costs), "fees + slippage")}
            ${mktStat("Net P&L", fmtInr(r.net_pnl), r.net_pnl >= 0 ? "positive" : "negative")}
            ${mktStat("Wins / Losses", r.wins + " / " + r.losses, r.hold_days + " days")}
          </div>
          <div class="card" style="margin-top:16px"><div class="card-title">Daily P&L path</div>
            <div style="height:130px;display:flex;align-items:flex-end;gap:2px">${r.daily.map(d => '<div style="flex:1;background:' + (d.pnl >= 0 ? "var(--ok)" : "var(--err)") + ';height:' + Math.max(4, Math.min(100, Math.abs(d.pnl) / Math.max(...r.daily.map(x => Math.abs(x.pnl)), 1) * 100)) + '%;opacity:.85" title="Day ' + d.day + ' ' + fmtInr(d.pnl) + '"></div>').join("")}</div>
            <div class="muted small" style="margin-top:6px">${esc(r.disclaimer)}</div>
          </div>`;
      }).catch(e => { b.innerHTML = mktError(e); });
    };
    $("#obRun").addEventListener("click", run);
    run();
  }

  function mPortfolio(body) {
    body.innerHTML = mktLoading();
    Promise.allSettled([window.MarketClient.paperPortfolio(), window.MarketClient.portfolioRisk()]).then(([p, r]) => {
      if (p.status !== "fulfilled") { body.innerHTML = mktError(p.reason); return; }
      const pf = p.value, risk = r.status === "fulfilled" ? r.value : null;
      body.innerHTML = `
        <div class="dash-grid">
          ${mktStat("Total value", fmtInr(pf.total_value), "P&L " + (pf.pnl >= 0 ? "+" : "") + fmtInr(pf.pnl))}
          ${mktStat("Cash", fmtInr(pf.cash), "exposure " + pf.exposure_pct + "%")}
          ${mktStat("Positions", pf.positions.length, "paper mode")}
          ${mktStat("Realized P&L", fmtInr(pf.realized_pnl), "win rate " + pf.win_rate_pct + "%")}
        </div>
        ${risk ? '<div class="card" style="margin-top:16px"><div class="card-title">Risk snapshot</div>' +
          '<div class="row" style="flex-wrap:wrap;gap:10px">' +
          mktStat("Exposure", risk.exposure_pct + "%", "of capital") +
          mktStat("Top sector", risk.top_sector, risk.top_sector_pct + "%") +
          mktStat("Max position", risk.max_position_pct + "%", "single") +
          '</div>' + risk.flags.map(f => '<div class="insight" style="margin-top:6px"><span class="ic">⚠️</span><span>' + esc(f) + '</span></div>').join("") + '</div>' : ""}
        <div class="card" style="margin-top:16px"><div class="card-title">Positions</div>
          <table class="tbl"><thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Price</th><th>Value</th><th>Unrealized</th><th>Stop</th></tr></thead><tbody>
          ${pf.positions.map(pos => '<tr><td><b>' + esc(pos.symbol) + '</b></td><td>' + pos.quantity + '</td><td>' + fmtNum(pos.entry, 2) + '</td><td>' + fmtNum(pos.price, 2) + '</td><td>' + fmtInr(pos.value) + '</td><td class="' + (pos.unrealized_pnl >= 0 ? "up" : "down") + '">' + (pos.unrealized_pnl >= 0 ? "+" : "") + fmtInr(pos.unrealized_pnl) + '</td><td>' + fmtNum(pos.stop_loss, 2) + '</td></tr>').join("") || '<tr><td colspan="7" class="muted">No open positions.</td></tr>'}
          </tbody></table>
        </div>`;
    });
  }

  function mPaper(body) {
    body.innerHTML = `
      <div class="card"><div class="card-title">Execute a paper trade (simulated — no real money)</div>
        <div class="row" style="flex-wrap:wrap;gap:12px">
          <div class="field grow" style="margin:0"><label>Symbol</label><input id="ppSym" placeholder="e.g. TCS"></div>
          <div class="field grow" style="margin:0"><label>Quantity</label><input type="number" id="ppQty" value="10" min="1"></div>
          <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="ppBuy">Buy</button></div>
          <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn" id="ppSell">Sell</button></div>
        </div>
        <div class="hint" style="margin-top:10px">Orders execute at the current quoted price (live Moneycontrol, demo fallback offline). Capital starts at ₹1,000,000.</div>
      </div>
      <div id="ppBody" style="margin-top:16px"></div>`;
    const render = () => {
      const b = $("#ppBody");
      b.innerHTML = mktLoading();
      window.MarketClient.paperPortfolio().then(pf => {
        b.innerHTML = `
          <div class="dash-grid">
            ${mktStat("Cash", fmtInr(pf.cash), "")}
            ${mktStat("Total value", fmtInr(pf.total_value), "P&L " + (pf.pnl >= 0 ? "+" : "") + fmtInr(pf.pnl))}
            ${mktStat("Positions", pf.positions.length, "open")}
            ${mktStat("Return", fmtPct(pf.return_pct), "all-time")}
          </div>
          <div class="grid-2" style="margin-top:16px">
            <div class="card"><div class="card-title">Open positions</div>
              <table class="tbl"><thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Price</th><th>Value</th><th>P&L</th></tr></thead><tbody>
              ${pf.positions.map(pos => '<tr><td><b>' + esc(pos.symbol) + '</b></td><td>' + pos.quantity + '</td><td>' + fmtNum(pos.entry, 2) + '</td><td>' + fmtNum(pos.price, 2) + '</td><td>' + fmtInr(pos.value) + '</td><td class="' + (pos.unrealized_pnl >= 0 ? "up" : "down") + '">' + (pos.unrealized_pnl >= 0 ? "+" : "") + fmtInr(pos.unrealized_pnl) + '</td></tr>').join("") || '<tr><td colspan="6" class="muted">No open positions.</td></tr>'}
              </tbody></table>
            </div>
            <div class="card"><div class="card-title">Trade history</div>
              <table class="tbl"><thead><tr><th>Time</th><th>Side</th><th>Symbol</th><th>Qty</th><th>Price</th><th>P&L</th></tr></thead><tbody>
              ${pf.trades.slice().reverse().slice(0, 12).map(t => '<tr><td class="muted small">' + esc(t.time) + '</td><td><span class="badge ' + (t.type === "BUY" ? "ok" : "err") + '">' + t.type + '</span></td><td><b>' + esc(t.symbol) + '</b></td><td>' + t.quantity + '</td><td>' + fmtNum(t.price, 2) + '</td><td>' + (t.pnl ? fmtInr(t.pnl) : "—") + '</td></tr>').join("") || '<tr><td colspan="6" class="muted">No trades yet.</td></tr>'}
              </tbody></table>
            </div>
          </div>`;
      }).catch(e => { b.innerHTML = mktError(e); });
    };
    const act = (side) => {
      const sym = $("#ppSym").value.trim().toUpperCase();
      const qty = parseInt($("#ppQty").value, 10);
      if (!sym || !qty || qty < 1) { toast("Paper trade", "Enter a symbol and quantity.", "err"); return; }
      const fn = side === "buy" ? window.MarketClient.paperBuy : window.MarketClient.paperSell;
      fn(sym, qty).then(r => { toast("Paper trade", r.status + " — " + r.symbol + " x " + r.quantity, "ok"); mktAudit("PAPER " + side.toUpperCase() + " " + sym + " x " + qty); render(); $("#ppSym").value = ""; }).catch(e => toast("Paper trade", e.message, "err"));
    };
    $("#ppBuy").addEventListener("click", () => act("buy"));
    $("#ppSell").addEventListener("click", () => act("sell"));
    render();
  }

  function mBacktest(body) {
    body.innerHTML = `
      <div class="card"><div class="card-title">Strategy backtest (EMA + RSI on real historical OHLC)</div>
        <div class="row" style="flex-wrap:wrap;gap:12px">
          <div class="field grow" style="margin:0"><label>Symbol</label><input id="btSym" value="RELIANCE"></div>
          <div class="field grow" style="margin:0"><label>Stop loss %</label><input type="number" id="btStop" value="8" min="1" max="40"></div>
          <div class="field grow" style="margin:0"><label>Target % (optional)</label><input type="number" id="btTarget" value=""></div>
          <div class="field grow" style="margin:0"><label>Sessions</label><input type="number" id="btDays" value="500" min="60" max="750"></div>
          <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="btRun">Run backtest</button></div>
        </div>
        <div class="hint" style="margin-top:10px">Backtests use real historical OHLC where available (deterministic demo fallback offline). Past performance does not guarantee future results.</div>
      </div>
      <div id="btBody" style="margin-top:16px"></div>`;
    const run = () => {
      const b = $("#btBody");
      b.innerHTML = mktLoading();
      window.MarketClient.backtest($("#btSym").value.trim().toUpperCase(), {
        stop_loss_pct: parseFloat($("#btStop").value) || 8,
        target_pct: $("#btTarget").value ? parseFloat($("#btTarget").value) : undefined,
        days: parseInt($("#btDays").value, 10) || 500,
      }).then(r => {
        mktAudit("BACKTEST " + r.symbol + " -> " + r.total_return_pct + "% (" + r.n_trades + " trades)");
        b.innerHTML = `
          <div class="dash-grid">
            ${mktStat("Total return", fmtPct(r.total_return_pct), "CAGR " + fmtPct(r.cagr_pct))}
            ${mktStat("Max drawdown", fmtPct(r.max_drawdown_pct), "")}
            ${mktStat("Sharpe", r.sharpe, "Sortino " + r.sortino)}
            ${mktStat("Trades", r.n_trades, "win rate " + r.win_rate_pct + "%")}
          </div>
          <div class="grid-2" style="margin-top:16px">
            <div class="card"><div class="card-title">Equity curve (₹100 start)</div><div style="height:140px">${sparkSvg(r.equity_curve, 380, 120)}</div></div>
            <div class="card"><div class="card-title">Strategy quality</div>
              <div class="badge ${r.strategy_quality.grade === "Good" ? "ok" : r.strategy_quality.grade === "Poor" ? "err" : "wait"}" style="font-size:14px;padding:6px 12px">Grade: ${r.strategy_quality.grade}</div>
              <div class="card-sub" style="margin-top:10px">${esc(r.strategy_quality.reason)}</div>
              <div class="row small muted" style="margin-top:10px"><span>Profit factor ${r.profit_factor}</span><span class="grow"></span><span>Avg trade ${fmtPct(r.avg_trade_pct)}</span><span>Risk/reward ${r.risk_reward}</span></div>
            </div>
          </div>
          <div class="card" style="margin-top:16px"><div class="card-title">Setup</div>
            <div class="row small muted" style="gap:14px;flex-wrap:wrap"><span>Entry: ${esc(r.entry_rule)}</span><span>Exit: ${esc(r.exit_rule)}</span><span>Stop: ${r.stop_loss_pct}%</span><span>Target: ${r.target_pct ? r.target_pct + "%" : "none"}</span><span>Days: ${r.days}</span></div>
            <div class="muted small" style="margin-top:10px">${esc(r.disclaimer)}</div>
          </div>`;
      }).catch(e => { b.innerHTML = mktError(e); });
    };
    $("#btRun").addEventListener("click", run);
    run();
  }

  function mRisk(body) {
    body.innerHTML = `
      <div class="card"><div class="card-title">Position size calculator — max risk ÷ stop distance</div>
        <div class="row" style="flex-wrap:wrap;gap:12px">
          <div class="field grow" style="margin:0"><label>Symbol</label><input id="rkSym" value="RELIANCE"></div>
          <div class="field grow" style="margin:0"><label>Capital (₹)</label><input type="number" id="rkCap" value="500000" min="1000"></div>
          <div class="field grow" style="margin:0"><label>Risk per trade %</label><input type="number" id="rkRisk" value="2" min="0.1" max="10" step="0.1"></div>
          <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="rkRun">Size position</button></div>
        </div>
        <div class="hint" style="margin-top:10px">Position size = (capital × risk%) ÷ (price × stop distance%). This caps the max loss on the trade.</div>
      </div>
      <div id="rkBody" style="margin-top:16px"></div>`;
    const run = () => {
      const b = $("#rkBody");
      b.innerHTML = mktLoading();
      window.MarketClient.positionSize($("#rkSym").value.trim().toUpperCase(), parseFloat($("#rkCap").value) || 500000, parseFloat($("#rkRisk").value) || 2).then(r => {
        b.innerHTML = `
          <div class="dash-grid">
            ${mktStat("Position size", r.max_quantity + " units", "notional " + fmtInr(r.notional))}
            ${mktStat("Max risk", fmtInr(r.max_risk), r.risk_per_trade_pct + "% of capital")}
            ${mktStat("Stop loss", fmtNum(r.stop_loss_price, 2), r.stop_distance_pct + "% below price")}
            ${mktStat("Price", fmtNum(r.price, 2), "")}
          </div>
          <div class="card" style="margin-top:16px"><div class="insight" style="margin:0"><span class="ic">🧮</span><span>${esc(r.explanation)}</span></div>
          <div class="muted small" style="margin-top:8px">Always confirm stop placement and regime (scale risk down in Bear / high-volatility markets) before acting.</div></div>`;
      }).catch(e => { b.innerHTML = mktError(e); });
    };
    $("#rkRun").addEventListener("click", run);
    run();
  }

  function mAlerts(body) {
    const rules = JSON.parse(localStorage.getItem("acc-alerts") || "[]");
    body.innerHTML = `
      <div class="card"><div class="card-title">Alert rules</div>
        <div class="row" style="flex-wrap:wrap;gap:12px">
          <div class="field grow" style="margin:0"><label>Symbol</label><input id="alSym" placeholder="e.g. TCS"></div>
          <div class="field grow" style="margin:0"><label>Rule</label><select id="alRule">
            <option value="signal_buy">Signal becomes BUY candidate</option>
            <option value="score_70">AI score ≥ 70</option>
            <option value="signal_sell">Signal becomes SELL / REDUCE</option>
          </select></div>
          <div class="field" style="margin:0"><label>&nbsp;</label><button class="btn btn-primary" id="alAdd">Add rule</button></div>
        </div>
      </div>
      <div id="alList" style="margin-top:16px"></div>`;
    const render = () => {
      const list = $("#alList");
      if (!rules.length) { list.innerHTML = '<div class="empty" style="display:flex"><div class="empty-ic">🚨</div><h3>No alert rules</h3><p>Add rules to get notified about signal changes and scores.</p></div>'; return; }
      list.innerHTML = '<div class="grid-cards">' + rules.map((r, i) => '<div class="card"><div class="spread"><b>' + esc(r.symbol) + '</b><span class="badge">' + esc(r.rule) + '</span></div><div class="card-sub" style="margin-top:6px">Watch for ' + esc(r.rule.replace(/_/g, " ")) + '</div><div class="row" style="margin-top:10px"><button class="btn btn-sm btn-ghost" data-rm="' + i + '">Remove</button></div></div>').join("") + '</div>';
      $$("#alList [data-rm]").forEach(b => b.addEventListener("click", () => { rules.splice(+b.dataset.rm, 1); localStorage.setItem("acc-alerts", JSON.stringify(rules)); render(); }));
    };
    $("#alAdd").addEventListener("click", () => {
      const sym = $("#alSym").value.trim().toUpperCase();
      if (!sym) { toast("Alert", "Enter a symbol.", "err"); return; }
      rules.push({ symbol: sym, rule: $("#alRule").value });
      localStorage.setItem("acc-alerts", JSON.stringify(rules));
      toast("Alert", "Rule added for " + sym + ".", "ok");
      $("#alSym").value = "";
      render();
    });
    render();
  }

  function mJournal(body) {
    body.innerHTML = mktLoading();
    window.MarketClient.paperPortfolio().then(pf => {
      body.innerHTML = `
        <div class="dash-grid">
          ${mktStat("Trades", pf.trades.length, "all-time")}
          ${mktStat("Realized P&L", fmtInr(pf.realized_pnl), "")}
          ${mktStat("Win rate", pf.win_rate_pct + "%", "paper")}
          ${mktStat("Positions closed", pf.trades.filter(t => t.type === "SELL").length, "sells")}
        </div>
        <div class="card" style="margin-top:16px"><div class="card-title">Trade journal</div>
          <table class="tbl"><thead><tr><th>Time</th><th>Side</th><th>Symbol</th><th>Qty</th><th>Price</th><th>P&L</th></tr></thead><tbody>
          ${pf.trades.slice().reverse().map(t => '<tr><td class="muted small">' + esc(t.time) + '</td><td><span class="badge ' + (t.type === "BUY" ? "ok" : "err") + '">' + t.type + '</span></td><td><b>' + esc(t.symbol) + '</b></td><td>' + t.quantity + '</td><td>' + fmtNum(t.price, 2) + '</td><td class="' + (t.pnl > 0 ? "up" : t.pnl < 0 ? "down" : "") + '">' + (t.pnl ? (t.pnl > 0 ? "+" : "") + fmtInr(t.pnl) : "—") + '</td></tr>').join("") || '<tr><td colspan="6" class="muted">No trades logged yet — use Paper Trading.</td></tr>'}
          </tbody></table>
        </div>`;
    }).catch(e => { body.innerHTML = mktError(e); });
  }

  function mMonitoring(body) {
    body.innerHTML = mktLoading();
    Promise.allSettled([window.MarketClient.status(), window.MarketClient.quote("TCS"), window.MarketClient.score("TCS"), window.MarketClient.signal("TCS")]).then(([st, q, sc, sg]) => {
      if (st.status !== "fulfilled") { body.innerHTML = mktError(st.reason); return; }
      const s = st.value, quote = q.status === "fulfilled" ? q.value : null, score = sc.status === "fulfilled" ? sc.value : null, sig = sg.status === "fulfilled" ? sg.value : null;
      body.innerHTML = `
        <div class="card"><div class="card-title">Data provider</div>
          <div class="row small" style="gap:12px;flex-wrap:wrap"><span class="badge ok">● ${esc(s.provider)}</span><span class="badge wait">${esc(s.data)}</span><span class="badge">${esc(s.mode)}</span></div>
          <div class="muted small" style="margin-top:8px">Updated: ${esc(s.updated)}</div>
        </div>
        <div class="grid-2" style="margin-top:16px">
          <div class="card"><div class="card-title">Sample quote integrity (TCS)</div>
            ${quote ? '<div class="row small muted"><span>Price ${fmtNum(quote.price, 2)}</span><span class="grow"></span><span>Source: ${esc(quote.source)}</span></div><div class="muted small" style="margin-top:6px">Quality: ${esc(quote.quality)} · ${esc(quote.timestamp)}</div>' : '<div class="muted small">Unavailable.</div>'}
          </div>
          <div class="card"><div class="card-title">Model versions</div>
            <div class="row small muted" style="gap:10px;flex-wrap:wrap">
              ${score ? '<span class="badge">score: ' + esc(score.model) + '</span>' : ""}
              ${sig ? '<span class="badge">signal: ' + esc(sig.model) + '</span>' : ""}
              <span class="badge">data: moneycontrol-live</span>
            </div>
            <div class="muted small" style="margin-top:8px">Score transparency: every factor is shown with evidence, so results are auditable.</div>
          </div>
        </div>`;
    });
  }

  function mAudit(body) {
    const log = JSON.parse(localStorage.getItem("acc-audit") || "[]");
    body.innerHTML = `
      <div class="card"><div class="card-title">Action audit log</div>
        <div class="row" style="gap:10px"><span class="muted small">Actions you take in Market AI are logged here for transparency.</span><span class="grow"></span><button class="btn btn-ghost btn-sm" id="audClear">Clear log</button></div>
        <table class="tbl" style="margin-top:10px"><thead><tr><th>Time</th><th>Action</th></tr></thead><tbody>
        ${log.slice().reverse().map(e => '<tr><td class="muted small">' + esc(e.t) + '</td><td>' + esc(e.a) + '</td></tr>').join("") || '<tr><td colspan="2" class="muted">No actions logged yet.</td></tr>'}
        </tbody></table>
      </div>`;
    $("#audClear").addEventListener("click", () => { localStorage.setItem("acc-audit", "[]"); render(); toast("Audit", "Log cleared.", "warn"); });
  }
  function mktAudit(action) {
    const log = JSON.parse(localStorage.getItem("acc-audit") || "[]");
    log.push({ t: new Date().toLocaleString(), a: action });
    localStorage.setItem("acc-audit", JSON.stringify(log.slice(-100)));
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
