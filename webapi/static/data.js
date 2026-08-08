/* ============================================================
   AI COMMAND CENTER — Mock data layer
   Realistic sample data for conversations, tasks, automations,
   files, documents, knowledge, notifications, integrations.
   ============================================================ */

window.AppData = (() => {
  const now = () => new Date();

  const agents = [
    { key: "commander",   label: "Command Center",     icon: "🧠", tag: "Auto / All",         desc: "Auto-selects the right tools across every capability — your default assistant.", caps: ["Chat", "Files", "Email", "Search", "Tasks"], status: "ready" },
    { key: "exec",        label: "Executive Assistant",icon: "🗂️", tag: "Inbox & Tasks",       desc: "Triage your inbox, draft replies, manage tasks and keep your day organised.", caps: ["Gmail", "Drafts", "Search"], status: "ready" },
    { key: "finance",     label: "Finance Tracker",    icon: "📊", tag: "Ledger & Sheets",     desc: "Track income and expenses, produce summaries, flag cash-flow risks.", caps: ["Ledger", "Sheets", "Reports"], status: "ready" },
    { key: "bd",          label: "BD & Proposals",     icon: "🤝", tag: "Business Dev",        desc: "Research leads, prepare RFP responses and draft proposals.", caps: ["Research", "Proposals", "RFP"], status: "ready" },
    { key: "jobsearch",   label: "Career & Resumes",   icon: "💼", tag: "Job Search",          desc: "Score your resume against job descriptions and spot skill gaps.", caps: ["Resume", "Skill Match", "JD Analysis"], status: "ready" },
    { key: "marketing",   label: "Marketing",          icon: "📣", tag: "Campaigns",           desc: "Draft campaign copy, research channels and plan outreach.", caps: ["Copy", "Research", "Plans"], status: "ready" },
    { key: "docs",        label: "Documents",          icon: "📄", tag: "Docs & PDFs",         desc: "Generate, edit and manage documents, reports and presentations.", caps: ["Reports", "PDF", "DOCX"], status: "ready" },
    { key: "coach",       label: "Personal Coach",     icon: "🎯", tag: "Growth",              desc: "Set goals, track habits and get structured guidance.", caps: ["Goals", "Habits", "Notes"], status: "ready" },
    { key: "market",      label: "Market Intelligence",icon: "📈", tag: "Markets",             desc: "Indices, quotes, composite signals, regimes, screening and paper trading on demo data.", caps: ["Indices", "Signals", "Screener", "News"], status: "ready" },
    { key: "risk",        label: "Risk & Position Sizing", icon: "🛡️", tag: "Risk",           desc: "Size positions from risk per trade and stress-test portfolio exposure and concentration.", caps: ["Sizing", "Exposure", "Concentration"], status: "ready" },
  ];

  const conversations = [
    { id: "c1", title: "Monthly project report", agent: "docs",      updated: "2h ago",  pinned: true,  items: 12 },
    { id: "c2", title: "Job application — HCLTech", agent: "jobsearch", updated: "Yesterday", pinned: false, items: 8 },
    { id: "c3", title: "DPIIC Backend prototype", agent: "commander", updated: "Yesterday", pinned: true, items: 21 },
    { id: "c4", title: "Marketing campaign ideas", agent: "marketing", updated: "Mon",      pinned: false, items: 6 },
    { id: "c5", title: "Q2 finance analysis", agent: "finance",     updated: "Sun",      pinned: false, items: 9 },
    { id: "c6", title: "Resume update 2026", agent: "jobsearch",   updated: "Jul 30",   pinned: false, items: 4 },
    { id: "c7", title: "NIFTY regime + TCS signal", agent: "market", updated: "2h ago",  pinned: true,  items: 7 },
  ];

  const tasks = [
    { id: "t1", title: "Summarise this month's finance ledger", agent: "Finance Tracker", status: "done",  priority: "High", created: "Aug 8, 09:12", progress: 100 },
    { id: "t2", title: "Triage inbox and draft replies",         agent: "Executive Assistant", status: "done", priority: "High", created: "Aug 8, 08:40", progress: 100 },
    { id: "t3", title: "Score resume vs HCLTech Senior PM JD",   agent: "Career & Resumes", status: "run",   priority: "High", created: "Aug 8, 10:02", progress: 64 },
    { id: "t4", title: "Send project report to client",          agent: "Executive Assistant", status: "wait", priority: "High", created: "Aug 8, 10:31", progress: 40 },
    { id: "t5", title: "Draft Q3 marketing plan",                agent: "Marketing",         status: "sched", priority: "Med",  created: "Aug 9, 09:00", progress: 0 },
    { id: "t6", title: "Build DPIIC API endpoints",              agent: "Command Center",    status: "fail",  priority: "High", created: "Aug 7, 15:20", progress: 35 },
    { id: "t7", title: "Prepare RFP response — Conduent",        agent: "BD & Proposals",    status: "done",  priority: "Med",  created: "Aug 7, 11:05", progress: 100 },
    { id: "t8", title: "Generate weekly project status summary", agent: "Command Center",    status: "done",  priority: "Med",  created: "Aug 6, 17:00", progress: 100 },
  ];

  const automations = [
    { id: "a1", name: "Weekly Project Report", trigger: { label: "Every Monday — 9:00 AM", icon: "🗓️" }, steps: ["Collect project updates", "Analyze progress", "Identify risks", "Generate report", "Send notification"], status: "active", runs: 24, last: "Aug 4, 09:00" },
    { id: "a2", name: "Inbox Triage", trigger: { label: "Every morning — 8:00 AM", icon: "🌅" }, steps: ["Check unread mail", "Classify importance", "Draft replies", "Flag approvals needed"], status: "active", runs: 41, last: "Aug 8, 08:00" },
    { id: "a3", name: "Job Alert Digest", trigger: { label: "Mon/Wed/Fri — 12:00 PM", icon: "💼" }, steps: ["Search job emails", "Extract roles", "Score vs resume", "Compile digest"], status: "paused", runs: 12, last: "Aug 1, 12:00" },
    { id: "a4", name: "Daily Cash-Flow Check", trigger: { label: "Daily — 6:30 PM", icon: "📊" }, steps: ["Read ledger", "Compute net cash flow", "Flag risks", "Notify"], status: "active", runs: 18, last: "Aug 7, 18:30" },
  ];

  const files = [
    { id: "f1", name: "DPIIC_DPR.pdf",     kind: "pdf",  size: "2.4 MB", updated: "2h ago",  path: "documents/DPIIC_DPR.pdf" },
    { id: "f2", name: "Monthly_Report_Jul.docx", kind: "doc", size: "612 KB", updated: "3h ago",  path: "documents/Monthly_Report_Jul.docx" },
    { id: "f3", name: "Finance_Ledger.csv", kind: "csv",  size: "38 KB",  updated: "Yesterday", path: "data/finance_ledger.csv" },
    { id: "f4", name: "Q2_Revenue.xlsx",   kind: "xls",  size: "1.1 MB", updated: "Yesterday", path: "analytics/Q2_Revenue.xlsx" },
    { id: "f5", name: "sandeep_resume.pdf", kind: "pdf",  size: "318 KB", updated: "Aug 6",   path: "data/sandeep_resume.pdf" },
    { id: "f6", name: "Pitch_Deck_2026.pptx", kind: "ppt", size: "5.8 MB", updated: "Aug 5",  path: "documents/Pitch_Deck_2026.pptx" },
    { id: "f7", name: "Team_Photo.jpg",    kind: "img",  size: "3.2 MB", updated: "Aug 4",   path: "images/Team_Photo.jpg" },
    { id: "f8", name: "api_server.py",     kind: "code", size: "14 KB",  updated: "Aug 3",   path: "code/api_server.py" },
    { id: "f9", name: "Meeting_Notes.md",  kind: "txt",  size: "9 KB",   updated: "Aug 2",   path: "notes/Meeting_Notes.md" },
    { id: "f10", name: "Contract_Sample.pdf", kind: "pdf", size: "1.9 MB", updated: "Jul 30", path: "documents/Contract_Sample.pdf" },
  ];

  const documents = [
    { id: "d1", title: "Monthly Project Report — July 2026", kind: "docx", updated: "2h ago", size: "612 KB" },
    { id: "d2", title: "Weekly Status Summary (Aug 3–7)",    kind: "pdf",  updated: "Yesterday", size: "240 KB" },
    { id: "d3", title: "Finance Summary — July 2026",       kind: "xlsx", updated: "Yesterday", size: "96 KB" },
    { id: "d4", title: "RFP Response — Conduent",           kind: "docx", updated: "Aug 6", size: "1.4 MB" },
    { id: "d5", title: "Marketing Campaign Plan — Q3",      kind: "pptx", updated: "Aug 5", size: "3.1 MB" },
  ];

  const knowledge = [
    { id: "k1", name: "Projects",      icon: "📁", status: "ready", count: 8,   desc: "DPIIC, HCLTech, Conduent, internal PMO" },
    { id: "k2", name: "Resume & Career", icon: "💼", status: "ready", count: 3, desc: "sandeep_resume.pdf, JD samples, interview prep" },
    { id: "k3", name: "Finance Ledger", icon: "📊", status: "ready", count: 1, desc: "Finance_Ledger.csv" },
    { id: "k4", name: "Templates",     icon: "📄", status: "ready", count: 6,  desc: "Reports, RFP, proposals, presentations" },
    { id: "k5", name: "Clients",       icon: "🤝", status: "updating", count: 5, desc: "Syncing contact details…" },
    { id: "k6", name: "Google Drive",  icon: "☁️", status: "error", count: 0,  desc: "Not connected — add credential to sync" },
  ];

  const notifications = [
    { id: "n1", kind: "important", title: "Approval required", desc: "Executive Assistant wants to send the project report to the client.", time: "5m ago", read: false },
    { id: "n2", kind: "important", title: "Task failed", desc: "Build DPIIC API endpoints failed — 4 retries. Review the error details.", time: "1h ago", read: false },
    { id: "n3", kind: "update", title: "Report generated", desc: "Monthly Project Report — July 2026 is ready to preview.", time: "2h ago", read: false },
    { id: "n4", kind: "update", title: "Automation completed", desc: "Weekly Project Report ran successfully (24th run).", time: "Yesterday", read: true },
    { id: "n5", kind: "update", title: "File processed", desc: "DPIIC_DPR.pdf summarised by the Documents agent.", time: "Yesterday", read: true },
  ];

  const integrations = [
    { name: "Gmail", icon: "📧", desc: "Read inbox, threads and draft replies", status: "connected", detail: "dixitsandeep339@gmail.com" },
    { name: "Google Sheets", icon: "📗", desc: "Sync finance ledger to a spreadsheet", status: "not_connected", detail: "Needs service account" },
    { name: "Web Search", icon: "🔍", desc: "DuckDuckGo instant answers (default)", status: "connected", detail: "Optional Brave key for more" },
    { name: "LLM — Groq", icon: "⚡", desc: "openai/gpt-oss-120b via Groq API", status: "connected", detail: "Fast, low-latency" },
    { name: "Slack", icon: "💬", desc: "Send notifications to channels", status: "not_connected", detail: "Add a bot token" },
    { name: "Google Drive", icon: "☁️", desc: "Access files from the cloud", status: "not_connected", detail: "OAuth required" },
    { name: "GitHub", icon: "🐙", desc: "Pull code and open issues", status: "not_connected", detail: "Add a personal access token" },
    { name: "Calendar", icon: "📅", desc: "Read events for scheduling", status: "not_connected", detail: "OAuth required" },
  ];

  // helpers
  function fileIcon(kind) {
    return { pdf: "📕", doc: "📘", xls: "📊", csv: "🗂️", ppt: "📙", img: "🖼️", code: "👨‍💻", txt: "📝" }[kind] || "📄";
  }
  function fileClass(kind) { return "fi-" + (kind || "other"); }

  return {
    agents, conversations, tasks, automations, files, documents, knowledge, notifications, integrations,
    fileIcon, fileClass, now,
  };
})();
