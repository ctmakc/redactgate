// RedactGate Desktop UI — vanilla JS, no network beyond 127.0.0.1.
import { STR, GROUPS } from "./i18n.js";

// ── token & theme ──────────────────────────────────────────────────────────
const hashTok = new URLSearchParams(location.hash.slice(1)).get("t");
if (hashTok) {
  try { sessionStorage.setItem("rg-t", hashTok); } catch (e) {}
  history.replaceState(null, "", location.pathname);
}
let TOKEN = hashTok;
try { TOKEN = TOKEN || sessionStorage.getItem("rg-t"); } catch (e) {}
const prefs = (() => { try { return JSON.parse(localStorage.getItem("rg-prefs") || "{}"); } catch (e) { return {}; } })();
const savePrefs = () => { try { localStorage.setItem("rg-prefs", JSON.stringify(prefs)); } catch (e) {} };
if (prefs.theme === "light") document.documentElement.dataset.theme = "light";

const S = {
  st: null, lang: "uk", projects: [], pid: prefs.pid || null, tab: "anon",
  results: {}, sel: {}, entities: [], reveal: false, sample: {}, restoreOut: null, restoreIn: "",
  busy: false, opts: {}, pasteOpen: false,
};
const T = (k) => (STR[S.lang] && STR[S.lang][k]) || STR.en[k] || k;
const CAT = (k) => (STR[S.lang].categories || {})[k] || k;

// ── tiny DOM helper (text only via textContent — no innerHTML with data) ─────
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.setAttribute("style", v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const $app = document.getElementById("app");
const $dlg = document.getElementById("dlg");

function toast(msg) {
  const t = h("div", { class: "toast", role: "status" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 2400);
}

// ── API ────────────────────────────────────────────────────────────────────
async function api(path, { method = "GET", body, raw, headers = {} } = {}) {
  const opt = { method, headers: { "X-RG-Token": TOKEN || "", ...headers } };
  if (raw !== undefined) opt.body = raw;
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  const r = await fetch("/api/" + path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(j.message || r.status); e.code = j.error; e.status = r.status; throw e; }
  return j;
}
const dl = (id) => `/api/results/${id}?t=${encodeURIComponent(TOKEN)}`;

// ── label highlighting (client mirror of app/desktop/labels.py stems) ───────
const STEMS = ["Фізособ", "Физлиц", "Person", "Юрособ", "Юрлиц", "Compan", "Рахун", "Счёт", "Счет", "Account", "Картк", "Карт", "Card",
  "ЄДРПОУ", "ЕГРПОУ", "EDRPOU", "ІПН", "ИНН", "TaxID", "SIN", "SSN", "EIN", "ITIN", "BN", "ПДВ", "НДС", "VAT", "UCI", "Паспорт", "Passport",
  "Адрес", "Address", "Телефон", "Phone", "Пошт", "Почт", "Email", "Сум", "Amount", "ДатаНародж", "ДатаРожд", "BirthDate", "IP"];
const LABEL_RE = /(?<![\p{L}\d\-])(\p{L}[\p{L}']*)[-‑](\d{1,6})(?![\p{L}\d])/gu;
const isLabelWord = (w) => STEMS.some((s) => w.toLowerCase().startsWith(s.toLowerCase()));

function chipsFrom(text, animate) {
  const out = [];
  let last = 0, i = 0;
  for (const m of text.matchAll(LABEL_RE)) {
    if (!isLabelWord(m[1])) continue;
    out.push(text.slice(last, m.index));
    const chip = h("span", { class: "chip" }, m[1] + "-", h("b", {}, m[2]));
    if (animate && i < 60) {
      const w = h("span", { class: "seal-in", style: `--d:${i * 70}ms` }, chip);
      out.push(w);
    } else out.push(chip);
    i++;
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

function valuesFrom(text) {
  const vals = S.entities.filter((e) => !e.excluded).flatMap((e) => [e.value, ...e.variants]).filter((v) => v && v.length >= 3);
  if (!vals.length) return [text];
  vals.sort((a, b) => b.length - a.length);
  const esc = vals.slice(0, 600).map((v) => v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp("(" + esc.join("|") + ")", "g");
  const out = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    out.push(text.slice(last, m.index), h("span", { class: "val" }, m[0]));
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}

function withMarks(text, marks, cls) {
  const out = [];
  let last = 0, i = 0;
  for (const m of [...marks].sort((a, b) => a.start - b.start)) {
    out.push(text.slice(last, m.start), h("span", { class: cls, style: `--d:${Math.min(i++, 30) * 60}ms` }, m.new));
    last = m.end;
  }
  out.push(text.slice(last));
  return out;
}

// ── boot ───────────────────────────────────────────────────────────────────
async function boot() {
  if (!TOKEN) {
    $app.replaceChildren(h("div", { class: "empty", style: "padding:60px 36px" }, h("h2", {}, "RedactGate Desktop"),
      h("p", { class: "muted" }, "Запустіть програму з ярлика — вікно відкриється саме.")));
    return;
  }
  S.st = await api("state");
  S.lang = S.st.lang || "uk";
  document.documentElement.lang = S.lang;
  if (S.st.unlocked) await loadProjects();
  render();
  setInterval(() => api("heartbeat").catch(() => {}), 20000);
}

async function loadProjects() {
  S.projects = (await api("projects")).projects;
  if (!S.projects.find((p) => p.id === S.pid)) S.pid = S.projects[0]?.id || null;
  if (S.pid) await loadEntities();
}
async function loadEntities() {
  S.entities = S.pid ? (await api(`projects/${S.pid}/entities`)).entities : [];
}
const proj = () => S.projects.find((p) => p.id === S.pid);

function render() {
  document.title = "RedactGate Desktop";
  if (!S.st.initialized || !S.st.unlocked) return renderGate();
  $app.replaceChildren(h("div", { class: "shell" }, renderRail(), h("main", { class: "main", id: "main" }, renderMain())));
}

// ── gate (create / unlock) ─────────────────────────────────────────────────
const NOTE = {
  uk: [["Договір поставки між ", ["ТОВ «Агро-Трейд»", "Юрособа-1"], " та ", ["ФОП Мельник Ярема Іванович", "Фізособа-1"], ". Оплата на рахунок ", ["UA21 3052 9900 0002 6007 2335 6600 1", "Рахунок-1"], ", код ЄДРПОУ ", ["38123456", "ЄДРПОУ-1"], "."]],
  ru: [["Договор поставки между ", ["ООО «Агро-Трейд»", "Юрлицо-1"], " и ", ["ФЛП Мельник Ярема Иванович", "Физлицо-1"], ". Оплата на счёт ", ["UA21 3052 9900 0002 6007 2335 6600 1", "Счёт-1"], ", код ЕГРПОУ ", ["38123456", "ЕГРПОУ-1"], "."]],
  en: [["Supply agreement between ", ["Northwind Logistics Ltd.", "Company-1"], " and ", ["Daniel MacLeod", "Person-1"], ". Payment to account ", ["1234-5550142", "Account-1"], ", SIN ", ["130 692 544", "SIN-1"], "."]],
};

function renderGate() {
  const isNew = !S.st.initialized;
  const err = h("div", { class: "err", role: "alert" });
  const pw = h("input", { class: "input", type: "password", autocomplete: isNew ? "new-password" : "current-password", id: "pw" });
  const pw2 = isNew ? h("input", { class: "input", type: "password", autocomplete: "new-password", id: "pw2" }) : null;
  const btn = h("button", { class: "btn btn-primary", type: "submit" }, isNew ? T("create") : T("unlock"));
  const form = h("form", { class: "gate-card", onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    if (pw.value.length < 6) return (err.textContent = T("err_short"));
    if (isNew && pw.value !== pw2.value) return (err.textContent = T("err_match"));
    btn.disabled = true;
    try {
      await api(isNew ? "vault/init" : "vault/unlock", { method: "POST", body: { password: pw.value } });
      S.st = await api("state");
      await loadProjects();
      render();
    } catch (x) { err.textContent = x.code === "bad_password" ? T("err_bad") : x.message; btn.disabled = false; pw.select(); }
  } },
    h("div", { class: "brand", style: "padding:0;border:0" }, logo(), h("div", {}, h("div", { class: "brand-name" }, "RedactGate Desktop"), h("div", { class: "brand-sub" }, T("app_sub")))),
    h("h1", {}, isNew ? T("gate_title_new") : T("gate_title")),
    h("p", {}, isNew ? T("gate_text_new") : T("gate_text")),
    h("label", { class: "field" }, h("span", { class: "eyebrow" }, T("password")), pw),
    pw2 && h("label", { class: "field" }, h("span", { class: "eyebrow" }, T("password2")), pw2),
    err, btn, langSeg());
  const note = NOTE[S.lang] || NOTE.uk;
  let i = 0;
  const real = note[0].map((p) => (typeof p === "string" ? p : h("span", { class: "val" }, p[0])));
  const line = note[0].map((p) => (typeof p === "string" ? p : h("span", { class: "seal-in", style: `--d:${500 + (i++) * 240}ms` }, h("span", { class: "chip" }, p[1].replace(/\d+$/, ""), h("b", {}, p[1].match(/\d+$/)[0])))));
  $app.replaceChildren(h("div", { class: "gate" },
    h("section", { class: "gate-note" },
      h("div", { class: "eyebrow" }, T("here")),
      h("div", { class: "doc", style: "margin-top:10px" }, real),
      h("div", { class: "hperim" }, h("span", {}, T("perimeter"))),
      h("div", { class: "eyebrow", style: "color:var(--seal)" }, T("ai_side")),
      h("div", { class: "doc", style: "margin-top:10px" }, line),
      h("p", { class: "muted", style: "margin:28px 0 0;font-size:13.5px" }, T("note_caption"))),
    h("section", { class: "gate-form" }, form)));
  setTimeout(() => pw.focus(), 50);
}

function logo() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 64 64"); svg.setAttribute("class", "brand-mark"); svg.setAttribute("aria-hidden", "true");
  const r = (x, y, w, hh, fill, op) => { const e = document.createElementNS(ns, "rect"); Object.entries({ x, y, width: w, height: hh, rx: 1.5, fill }).forEach(([k, v]) => e.setAttribute(k, v)); if (op) e.setAttribute("opacity", op); svg.append(e); };
  const box = document.createElementNS(ns, "rect"); Object.entries({ width: 64, height: 64, rx: 10, fill: "none", stroke: "currentColor", "stroke-opacity": ".35" }).forEach(([k, v]) => box.setAttribute(k, v)); svg.append(box);
  r(12, 15, 40, 6, "currentColor"); r(12, 29, 26, 7, "rgb(var(--c-seal))"); r(41, 29, 11, 7, "currentColor", ".45"); r(12, 44, 34, 6, "currentColor");
  return svg;
}

function langSeg() {
  return h("div", { class: "seg", role: "group", "aria-label": "Language" }, ["uk", "ru", "en"].map((l) =>
    h("button", { type: "button", "aria-pressed": String(S.lang === l), onclick: async () => {
      S.lang = l; document.documentElement.lang = l;
      await api("settings", { method: "POST", body: { lang: l } }).catch(() => {});
      render();
    } }, l === "uk" ? "УКР" : l === "ru" ? "РУС" : "ENG")));
}
function themeSeg() {
  const cur = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  return h("div", { class: "seg", role: "group", "aria-label": "Theme" }, [["dark", T("theme_dark")], ["light", T("theme_light")]].map(([k, lbl]) =>
    h("button", { type: "button", "aria-pressed": String(cur === k), onclick: () => {
      if (k === "light") document.documentElement.dataset.theme = "light"; else delete document.documentElement.dataset.theme;
      prefs.theme = k; savePrefs(); render();
    } }, lbl)));
}

// ── rail ───────────────────────────────────────────────────────────────────
function renderRail() {
  return h("aside", { class: "rail" },
    h("div", { class: "brand" }, logo(), h("div", {}, h("div", { class: "brand-name" }, "RedactGate Desktop"), h("div", { class: "brand-sub" }, T("app_sub")))),
    h("div", { class: "rail-head" }, h("span", { class: "eyebrow" }, T("projects")), h("button", { class: "btn btn-ghost btn-sm", onclick: newProjectDialog }, "+ " + T("new_project"))),
    h("nav", { class: "projects", "aria-label": T("projects") }, S.projects.map((p) =>
      h("button", { class: "proj", "aria-current": String(p.id === S.pid), onclick: async () => { S.pid = p.id; prefs.pid = p.id; savePrefs(); await loadEntities(); render(); } },
        h("span", { class: "proj-name" }, p.name), h("span", { class: "proj-meta" }, `${p.entities} · ${p.documents} ${T("files_n")}`)))),
    h("div", { class: "rail-foot" },
      h("button", { class: "offline", onclick: selfcheckDialog, title: T("check_title") }, T("offline")),
      h("div", { class: "rail-ctrls" }, langSeg(), themeSeg()),
      h("div", { class: "rail-ctrls" },
        h("button", { class: "btn btn-ghost btn-sm", onclick: vaultDialog }, T("settings")),
        h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await api("vault/lock", { method: "POST" }); S.st = await api("state"); S.results = {}; render(); } }, T("lock")),
        h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await api("quit", { method: "POST" }).catch(() => {}); window.close(); $app.replaceChildren(h("div", { class: "empty", style: "padding:60px 36px" }, h("h2", {}, "RedactGate Desktop"), h("p", { class: "muted" }, "✓"))); } }, T("quit")))));
}

// ── main ───────────────────────────────────────────────────────────────────
function renderMain() {
  const p = proj();
  if (!p) return renderEmpty();
  const tabs = [["anon", T("tab_anon")], ["restore", T("tab_restore")], ["labels", T("tab_labels"), S.entities.length], ["journal", T("tab_journal"), p.documents]];
  return [
    h("header", { class: "topbar" },
      h("div", {}, h("div", { class: "eyebrow" }, `${T("label_lang")}: ${p.label_lang.toUpperCase()} · ${S.entities.length} ${T("labels_n").toLowerCase()}`),
        h("div", { class: "title-row" }, h("h1", { class: "title" }, p.name))),
      h("div", { class: "rail-ctrls" }, h("button", { class: "btn btn-ghost btn-sm", onclick: projectDialog }, T("project_settings")))),
    h("div", { class: "tabs", role: "tablist" }, tabs.map(([k, lbl, n]) =>
      h("button", { class: "tab", role: "tab", "aria-selected": String(S.tab === k), onclick: async () => { S.tab = k; if (k === "labels") await loadEntities(); render(); } },
        lbl, n != null ? h("span", { class: "count" }, n) : null))),
    h("section", { class: "view", id: "view" },
      S.tab === "anon" ? viewAnon() : S.tab === "restore" ? viewRestore() : S.tab === "labels" ? viewLabels() : viewJournal()),
  ];
}

function renderEmpty() {
  return h("section", { class: "view" }, h("div", { class: "empty enter" },
    h("div", { class: "eyebrow" }, "RedactGate Desktop"),
    h("h2", {}, T("no_projects_title")),
    h("p", { class: "muted", style: "margin:0" }, T("no_projects_text")),
    h("ol", { class: "steps" }, ["how1", "how2", "how3", "how4"].map((k) => h("li", {}, T(k)))),
    h("div", { class: "opt-row" },
      h("button", { class: "btn btn-primary", onclick: newProjectDialog }, T("create_project")),
      h("button", { class: "btn btn-seal", onclick: runDemo }, T("demo"))),
    h("p", { class: "muted", style: "margin:0;font-size:13px" }, T("demo_hint"))));
}

async function runDemo() {
  S.busy = true;
  $app.querySelectorAll("button").forEach((b) => (b.disabled = true));
  toast(T("working"));
  try {
    const r = await api("demo", { method: "POST" });
    S.pid = r.project_id; prefs.pid = S.pid; savePrefs();
    S.results[S.pid] = r.results; S.sel[S.pid] = 0; S.sample[S.pid] = r.sample_answer;
    S.tab = "anon";
    await loadProjects();
  } catch (x) { toast(T("failed") + ": " + x.message); }
  S.busy = false;
  render();
}

// selected categories for this project
function selectedCats() {
  const p = proj();
  const def = S.st.categories.filter((c) => c.default_on).map((c) => c.id);
  return new Set(p?.options?.categories || def);
}
async function toggleGroup(cats, on) {
  const p = proj();
  const sel = selectedCats();
  cats.forEach((c) => (on ? sel.add(c) : sel.delete(c)));
  p.options = { ...(p.options || {}), categories: [...sel] };
  await api(`projects/${p.id}`, { method: "PATCH", body: { options: p.options } });
  render();
}

// ── anonymize view ─────────────────────────────────────────────────────────
function viewAnon() {
  const sel = selectedCats();
  const input = h("input", { type: "file", multiple: true, class: "sr", id: "files", onchange: (e) => upload([...e.target.files]) });
  const tray = h("div", { class: "tray", id: "tray",
    ondragover: (e) => { e.preventDefault(); tray.classList.add("drag"); },
    ondragleave: () => tray.classList.remove("drag"),
    ondrop: (e) => { e.preventDefault(); tray.classList.remove("drag"); upload([...e.dataTransfer.files]); } },
    trayIcon(),
    h("div", {}, h("h3", {}, S.busy ? T("working") : T("tray_title")), h("p", {}, T("tray_text"))),
    h("div", {}, input, h("label", { for: "files", class: "btn btn-primary", role: "button", tabindex: "0", onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") input.click(); } }, S.busy ? h("span", { class: "spinner" }) : null, T("choose"))));
  const toggles = h("div", { class: "toggles" }, GROUPS.map(([g, cats]) => {
    const on = cats.some((c) => sel.has(c));
    return h("button", { class: "toggle", "aria-pressed": String(on), onclick: () => toggleGroup(cats, !on) }, CAT(g));
  }));
  const results = S.results[S.pid] || [];
  const paste = S.pasteOpen ? pasteBox() : h("button", { class: "btn-link", onclick: () => { S.pasteOpen = true; render(); } }, S.lang === "en" ? "or paste text instead of a file" : S.lang === "ru" ? "или вставьте текст вместо файла" : "або вставте текст замість файлу");
  return [
    tray,
    h("div", { class: "options" }, h("div", { class: "eyebrow" }, T("what_hide")), toggles,
      !S.st.ocr ? h("div", { class: "callout warn" }, T("no_ocr")) : null, paste),
    results.length ? resultsBlock(results) : null,
  ];
}

function pasteBox() {
  const ta = h("textarea", { class: "textarea", style: "min-height:140px", placeholder: "…" });
  const out = h("div", { class: "doc", style: "min-height:140px" });
  const btn = h("button", { class: "btn btn-primary", onclick: async () => {
    btn.disabled = true;
    try {
      const r = await api(`projects/${S.pid}/anonymize-text`, { method: "POST", body: { text: ta.value } });
      out.replaceChildren(...chipsFrom(r.text, true));
      out.dataset.text = r.text;
      await loadEntities(); await refreshProject();
    } finally { btn.disabled = false; }
  } }, T("tab_anon"));
  return h("div", { class: "custody", style: "margin-top:8px" },
    h("div", { class: "custody-side" }, ta, h("div", { style: "margin-top:8px" }, btn)),
    h("div", { class: "perimeter" }, h("span", {}, T("perimeter"))),
    h("div", { class: "custody-side" }, out, h("div", { style: "margin-top:8px" }, h("button", { class: "btn btn-ghost btn-sm", onclick: () => copyText(out.dataset.text || "") }, T("copy")))));
}

function trayIcon() {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 54 64"); svg.setAttribute("class", "tray-icon"); svg.setAttribute("aria-hidden", "true");
  const add = (tag, at) => { const e = document.createElementNS(ns, tag); Object.entries(at).forEach(([k, v]) => e.setAttribute(k, v)); svg.append(e); };
  add("path", { d: "M3 3h34l14 14v44H3z", fill: "none", stroke: "currentColor", "stroke-opacity": ".5" });
  add("path", { d: "M37 3v14h14", fill: "none", stroke: "currentColor", "stroke-opacity": ".5" });
  [[10, 24, 30], [10, 32, 18], [10, 40, 26], [10, 48, 22]].forEach(([x, y, w], i) => add("rect", { x, y, width: w, height: 4, rx: 1, fill: i === 1 ? "rgb(var(--c-seal))" : "currentColor", opacity: i === 1 ? 1 : 0.35 }));
  return svg;
}

async function upload(files) {
  if (!files.length || S.busy) return;
  S.busy = true; render();
  try {
    const { batch } = await api("batch", { method: "POST" });
    for (const f of files) {
      await api(`batch/${batch}/file`, { method: "PUT", raw: f, headers: { "X-File-Name": encodeURIComponent(f.name), "Content-Type": "application/octet-stream" } })
        .catch((x) => toast(`${f.name}: ${x.code === "unsupported" ? T("unsupported") : x.message}`));
    }
    const r = await api(`projects/${S.pid}/anonymize`, { method: "POST", body: { batch } });
    S.results[S.pid] = [...r.results, ...(S.results[S.pid] || [])].slice(0, 60);
    S.sel[S.pid] = 0;
    await loadEntities(); await refreshProject();
  } catch (x) { toast(x.code === "empty" || x.message === "empty batch" ? T("empty_batch") : T("failed") + ": " + x.message); }
  S.busy = false; render();
}
async function refreshProject() {
  S.projects = (await api("projects")).projects;
}

function countsLine(counts) {
  const order = ["PERSON", "ORG", "ACCOUNT", "EDRPOU", "IPN", "ADDRESS", "PHONE", "EMAIL"];
  const keys = Object.keys(counts || {}).sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
  return h("span", { class: "counts" }, keys.map((k) => h("span", {}, CAT(k) === k ? k : CAT(k), " ", h("b", {}, counts[k]))));
}

function resultsBlock(results) {
  const selIdx = Math.min(S.sel[S.pid] || 0, results.length - 1);
  const cur = results[selIdx];
  const ids = results.filter((r) => r.id).map((r) => r.id);
  const zipName = (proj().name || "redactgate").replace(/[^\p{L}\d]+/gu, "_") + "_anon.zip";
  return h("div", { class: "results" },
    h("div", { class: "bar-actions" },
      h("div", { class: "eyebrow" }, `${results.length} ${T("files_n")}`),
      ids.length > 1 ? h("a", { class: "btn btn-ghost btn-sm", href: `/api/zip/${ids.join(",")}?t=${encodeURIComponent(TOKEN)}&name=${encodeURIComponent(zipName)}` }, T("download_all")) : null),
    h("div", { class: "ledger-wrap" }, h("table", { class: "ledger" },
      h("thead", {}, h("tr", {}, h("th", {}, T("file")), h("th", {}, T("found")), h("th", { class: "hide-sm" }, T("time")), h("th", {}))),
      h("tbody", {}, results.map((r, i) => h("tr", { class: "res-row", "aria-selected": String(i === selIdx), onclick: () => { S.sel[S.pid] = i; render(); } },
        h("td", {}, r.out_name || r.name),
        h("td", {}, r.error ? h("span", { class: "stamp stamp-warn" }, r.error === "unsupported" ? T("unsupported") : T("failed")) : countsLine(r.counts)),
        h("td", { class: "num hide-sm" }, r.seconds != null ? `${r.seconds} s` : ""),
        h("td", { style: "text-align:right" }, r.id ? h("a", { class: "btn btn-seal btn-sm", href: dl(r.id), onclick: (e) => e.stopPropagation() }, T("download")) : null)))))),
    cur && !cur.error ? h("div", { class: "custody", style: "margin-top:22px" },
      h("div", { class: "custody-side" }, h("div", { class: "custody-head" }, h("span", { class: "eyebrow" }, T("here")), h("span", { class: "eyebrow" }, cur.name)),
        h("div", { class: "doc" + (cur.kind === "csv" ? " mono-doc" : "") }, valuesFrom(cur.preview_in))),
      h("div", { class: "perimeter" }, h("span", {}, T("perimeter"))),
      h("div", { class: "custody-side" }, h("div", { class: "custody-head" }, h("span", { class: "eyebrow", style: "color:var(--seal)" }, T("ai_side")), h("span", { class: "eyebrow" }, cur.out_name)),
        h("div", { class: "doc" + (cur.kind === "csv" ? " mono-doc" : "") }, chipsFrom(cur.preview_out, true)))) : null);
}

// ── restore view ───────────────────────────────────────────────────────────
function viewRestore() {
  const ta = h("textarea", { class: "textarea", id: "answer", placeholder: T("paste_answer") + "…" });
  ta.value = S.restoreIn || "";
  ta.addEventListener("input", () => (S.restoreIn = ta.value));
  const outBox = h("div", { class: "doc", id: "restored", style: "min-height:220px" });
  const info = h("div", {});
  const showOut = (r, srcText) => {
    outBox.replaceChildren(...withMarks(srcText, r.marks, "restored"));
    info.replaceChildren(...(r.unknown && r.unknown.length ? [h("div", { class: "callout warn", style: "margin-top:10px" }, T("unknown") + ": " + r.unknown.join(", "))] : []));
    outBox.dataset.text = r.text;
  };
  if (S.restoreOut) showOut(S.restoreOut, S.restoreOut.src);
  const btn = h("button", { class: "btn btn-primary", onclick: async () => {
    btn.disabled = true;
    try {
      const r = await api(`projects/${S.pid}/restore-text`, { method: "POST", body: { text: ta.value } });
      S.restoreOut = { ...r, src: ta.value };
      showOut(r, ta.value);
      await refreshProject();
    } catch (x) { toast(T("failed") + ": " + x.message); }
    btn.disabled = false;
  } }, T("restore_btn"));
  const fileIn = h("input", { type: "file", multiple: true, class: "sr", id: "afiles", onchange: (e) => restoreFiles([...e.target.files]) });
  const drop = h("div", { class: "tray", style: "grid-template-columns:1fr auto;padding:16px 18px;margin-top:10px",
    ondragover: (e) => { e.preventDefault(); drop.classList.add("drag"); }, ondragleave: () => drop.classList.remove("drag"),
    ondrop: (e) => { e.preventDefault(); drop.classList.remove("drag"); restoreFiles([...e.dataTransfer.files]); } },
    h("p", {}, T("drop_answer"), " · DOCX, PDF, TXT, XLSX, CSV"),
    h("div", {}, fileIn, h("label", { for: "afiles", class: "btn btn-ghost btn-sm", role: "button", tabindex: "0" }, T("choose"))));
  const sample = S.sample[S.pid];
  const rres = S.restoreFiles?.[S.pid] || [];
  return [
    h("p", { class: "muted", style: "margin:0 0 18px;max-width:820px" }, T("restore_hint")),
    h("div", { class: "custody" },
      h("div", { class: "custody-side" },
        h("div", { class: "custody-head" }, h("span", { class: "eyebrow", style: "color:var(--seal)" }, T("paste_answer")),
          sample ? h("button", { class: "btn-link", onclick: () => { S.restoreIn = sample; ta.value = sample; } }, T("sample_answer")) : null),
        ta, h("div", { style: "margin-top:10px", class: "opt-row" }, btn), drop),
      h("div", { class: "perimeter" }, h("span", {}, T("perimeter"))),
      h("div", { class: "custody-side" },
        h("div", { class: "custody-head" }, h("span", { class: "eyebrow" }, T("restored") + " · " + T("here").toLowerCase()),
          h("button", { class: "btn btn-ghost btn-sm", onclick: () => copyText(outBox.dataset.text || "") }, T("copy"))),
        outBox, info)),
    rres.length ? h("div", { class: "results" }, h("table", { class: "ledger" },
      h("thead", {}, h("tr", {}, h("th", {}, T("file")), h("th", {}, T("found")), h("th", {}), h("th", {}))),
      h("tbody", {}, rres.map((r) => h("tr", {},
        h("td", { class: "mono" }, r.out_name || r.name),
        h("td", {}, r.error ? h("span", { class: "stamp stamp-warn" }, T("failed")) : countsLine(r.counts)),
        h("td", {}, r.error ? null : h("span", { class: "stamp " + (r.exact ? "stamp-seal" : "") }, r.exact ? T("exact") : T("canonical"))),
        h("td", { style: "text-align:right" }, r.id ? h("a", { class: "btn btn-seal btn-sm", href: dl(r.id) }, T("download")) : null)))))) : null,
  ];
}

async function restoreFiles(files) {
  if (!files.length) return;
  toast(T("working"));
  try {
    const { batch } = await api("batch", { method: "POST" });
    for (const f of files) await api(`batch/${batch}/file`, { method: "PUT", raw: f, headers: { "X-File-Name": encodeURIComponent(f.name) } });
    const r = await api(`projects/${S.pid}/restore`, { method: "POST", body: { batch } });
    S.restoreFiles = S.restoreFiles || {};
    S.restoreFiles[S.pid] = [...r.results, ...(S.restoreFiles[S.pid] || [])];
    await refreshProject();
  } catch (x) { toast(T("failed") + ": " + x.message); }
  render();
}

async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast(T("copied")); }
  catch (e) { const ta = h("textarea", {}, t); document.body.append(ta); ta.select(); document.execCommand("copy"); ta.remove(); toast(T("copied")); }
}

// ── labels view ────────────────────────────────────────────────────────────
function viewLabels() {
  const p = proj();
  const catSel = h("select", { class: "select" }, S.st.categories.map((c) => h("option", { value: c.id }, c.titles[S.lang] || c.id)));
  const val = h("input", { class: "input", placeholder: T("value") });
  const keep = h("button", { class: "toggle", "aria-pressed": "false", type: "button", onclick: () => keep.setAttribute("aria-pressed", String(keep.getAttribute("aria-pressed") !== "true")) }, T("keep"));
  const add = h("form", { class: "addbar", onsubmit: async (e) => {
    e.preventDefault();
    if (!val.value.trim()) return;
    await api(`projects/${p.id}/entities`, { method: "POST", body: { category: catSel.value, value: val.value, excluded: keep.getAttribute("aria-pressed") === "true" } });
    val.value = ""; await loadEntities(); await refreshProject(); render();
  } }, catSel, val, keep, h("button", { class: "btn btn-primary", type: "submit" }, T("add")));
  const ORDER = ["PERSON", "ORG", "ACCOUNT", "CARD", "EDRPOU", "IPN", "ADDRESS", "PHONE", "EMAIL", "PASSPORT"];
  const rank = (c) => { const i = ORDER.indexOf(c); return i < 0 ? 99 : i; };
  const ents = [...S.entities].sort((a, b) => rank(a.category) - rank(b.category) || a.n - b.n);
  const rows = ents.map((e) => {
    const masked = h("span", { class: "masked" + (S.reveal ? "" : " hide"), onmouseenter: (ev) => ev.currentTarget.classList.remove("hide"), onmouseleave: (ev) => { if (!S.reveal) ev.currentTarget.classList.add("hide"); } },
      h("span", { class: "real" }, e.value));
    return h("tr", { class: e.excluded ? "excluded" : "" },
      h("td", {}, h("span", { class: "chip" }, e.labels[p.label_lang].replace(/\d+$/, ""), h("b", {}, e.n))),
      h("td", {}, masked),
      h("td", { class: "hide-sm muted", style: "font-size:13px" }, e.variants.slice(0, 3).join(" · ")),
      h("td", { class: "num" }, e.occurrences),
      h("td", { style: "text-align:right;white-space:nowrap" },
        h("button", { class: "btn btn-ghost btn-sm", onclick: () => editEntity(e) }, T("edit")), " ",
        h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await api(`projects/${p.id}/entities/${e.category}/${e.n}`, { method: "PATCH", body: { excluded: !e.excluded } }); await loadEntities(); render(); } }, e.excluded ? T("hide_again") : T("keep"))));
  });
  return [
    h("div", { class: "bar-actions" }, h("div", {}, h("h2", { style: "margin:0;font:600 18px var(--font-sans)" }, T("labels_title")), h("p", { class: "muted", style: "margin:4px 0 0;font-size:13.5px" }, T("labels_text"))),
      h("button", { class: "btn btn-ghost btn-sm", onclick: () => { S.reveal = !S.reveal; render(); } }, S.reveal ? T("hide_all") : T("show_all"))),
    h("div", { class: "eyebrow", style: "margin:16px 0 8px" }, T("add_term")), add,
    h("div", { class: "ledger-wrap" }, h("table", { class: "ledger" },
      h("thead", {}, h("tr", {}, h("th", {}, T("col_label")), h("th", {}, T("col_value")), h("th", { class: "hide-sm" }, T("col_variants")), h("th", { class: "num" }, T("col_count")), h("th", {}))),
      h("tbody", {}, rows))),
  ];
}

function editEntity(e) {
  const inp = h("input", { class: "input", value: e.value });
  dialog(T("edit") + " · " + e.labels[proj().label_lang], [
    h("label", { class: "field" }, h("span", { class: "eyebrow" }, T("col_value")), inp),
  ], [
    h("button", { class: "btn btn-ghost", onclick: async () => { await api(`projects/${S.pid}/entities/${e.category}/${e.n}`, { method: "DELETE" }); $dlg.close(); await loadEntities(); render(); } }, T("del")),
    h("button", { class: "btn btn-primary", onclick: async () => { await api(`projects/${S.pid}/entities/${e.category}/${e.n}`, { method: "PATCH", body: { value: inp.value } }); $dlg.close(); await loadEntities(); render(); } }, T("save")),
  ]);
}

// ── journal ────────────────────────────────────────────────────────────────
function viewJournal() {
  const box = h("div", {}, h("span", { class: "spinner" }));
  api(`projects/${S.pid}/documents`).then(({ documents }) => {
    if (!documents.length) return box.replaceChildren(h("p", { class: "muted" }, T("journal_empty")));
    box.replaceChildren(h("div", { class: "ledger-wrap" }, h("table", { class: "ledger" },
      h("thead", {}, h("tr", {}, h("th", {}, T("file")), h("th", {}), h("th", {}, T("found")), h("th", { class: "num" }, T("time")))),
      h("tbody", {}, documents.map((d) => h("tr", {},
        h("td", { class: "mono" }, d.name),
        h("td", {}, h("span", { class: "stamp " + (d.direction === "anon" ? "stamp-seal" : "") }, d.direction === "anon" ? T("dir_anon") : T("dir_restore"))),
        h("td", {}, countsLine(d.stats.counts)),
        h("td", { class: "num" }, new Date(d.created * 1000).toLocaleString(S.lang === "en" ? "en-CA" : S.lang))))))));
  });
  return box;
}

// ── dialogs ────────────────────────────────────────────────────────────────
function dialog(title, body, actions) {
  const close = h("button", { class: actions.length ? "btn btn-ghost" : "btn btn-primary", onclick: () => $dlg.close() }, actions.length ? T("cancel") : T("ok"));
  $dlg.replaceChildren(h("h3", {}, title), ...body, h("div", { class: "dlg-actions" }, close, ...actions));
  $dlg.showModal();
}
function langSelect(cur) {
  return h("select", { class: "select" }, [["uk", "Українська — Фізособа-1"], ["ru", "Русский — Физлицо-1"], ["en", "English — Person-1"]].map(([v, l]) => h("option", { value: v, selected: v === cur }, l)));
}
function newProjectDialog() {
  const name = h("input", { class: "input", placeholder: S.lang === "en" ? "Acme audit 2026" : "Аудит 2026" });
  const lang = langSelect(S.lang);
  dialog(T("create_project"), [
    h("label", { class: "field" }, h("span", { class: "eyebrow" }, T("project_name")), name),
    h("label", { class: "field", style: "margin-top:12px" }, h("span", { class: "eyebrow" }, T("label_lang")), lang),
  ], [h("button", { class: "btn btn-primary", onclick: async () => {
    const p = await api("projects", { method: "POST", body: { name: name.value, label_lang: lang.value } });
    $dlg.close(); S.pid = p.id; prefs.pid = p.id; savePrefs(); S.tab = "anon"; await loadProjects(); render();
  } }, T("create_project"))]);
  setTimeout(() => name.focus(), 30);
}
function projectDialog() {
  const p = proj();
  const name = h("input", { class: "input", value: p.name });
  const lang = langSelect(p.label_lang);
  dialog(T("project_settings"), [
    h("label", { class: "field" }, h("span", { class: "eyebrow" }, T("rename")), name),
    h("label", { class: "field", style: "margin-top:12px" }, h("span", { class: "eyebrow" }, T("label_lang")), lang),
    h("div", { style: "margin-top:18px" }, h("button", { class: "btn btn-ghost btn-sm", style: "color:var(--vermillion);border-color:rgb(var(--c-vermillion)/.5)", onclick: async () => {
      if (!confirm(T("delete_confirm"))) return;
      await api(`projects/${p.id}`, { method: "DELETE" }); $dlg.close(); S.pid = null; await loadProjects(); render();
    } }, T("delete_project"))),
  ], [h("button", { class: "btn btn-primary", onclick: async () => {
    await api(`projects/${p.id}`, { method: "PATCH", body: { name: name.value, label_lang: lang.value } }); $dlg.close(); await loadProjects(); render();
  } }, T("save"))]);
}
function vaultDialog() {
  const o = h("input", { class: "input", type: "password", autocomplete: "current-password" });
  const n = h("input", { class: "input", type: "password", autocomplete: "new-password" });
  const err = h("div", { class: "err" });
  dialog(T("settings"), [
    h("div", { class: "opt-row" }, h("a", { class: "btn btn-ghost btn-sm", href: `/api/vault/export?t=${encodeURIComponent(TOKEN)}` }, T("export"))),
    h("div", { class: "eyebrow", style: "margin-top:18px" }, T("password_change")),
    h("label", { class: "field", style: "margin-top:8px" }, h("span", { class: "eyebrow" }, T("old_pw")), o),
    h("label", { class: "field", style: "margin-top:8px" }, h("span", { class: "eyebrow" }, T("new_pw")), n), err,
    h("p", { class: "muted", style: "font-size:12.5px" }, S.st.data_dir),
  ], [h("button", { class: "btn btn-primary", onclick: async () => {
    if (n.value.length < 6) return (err.textContent = T("err_short"));
    try { await api("vault/password", { method: "POST", body: { old: o.value, new: n.value } }); $dlg.close(); toast("✓"); }
    catch (x) { err.textContent = x.code === "bad_password" ? T("err_bad") : x.message; }
  } }, T("save"))]);
}
async function selfcheckDialog() {
  const r = await api("selfcheck");
  dialog(T("check_title"), [
    h("p", { class: "muted", style: "margin-top:0" }, T("check_text")),
    h("table", { class: "ledger" }, h("tbody", {},
      h("tr", {}, h("td", {}, T("check_blocked")), h("td", { class: "num" }, r.netguard.blocked)),
      h("tr", {}, h("td", {}, T("check_out")), h("td", { class: "num" }, r.outbound.length)),
      h("tr", {}, h("td", {}, T("check_sockets")), h("td", { class: "num", style: "font-size:12px" }, r.sockets.map((s) => `${s.laddr}${s.raddr ? " ↔ " + s.raddr : ""}`).join("\n"))))),
  ], []);
}

window.addEventListener("keydown", (e) => { if (e.key === "Escape" && $dlg.open) $dlg.close(); });
boot().catch((e) => { $app.replaceChildren(h("div", { class: "empty", style: "padding:60px 36px" }, h("h2", {}, "RedactGate Desktop"), h("p", { class: "err" }, String(e.message || e)))); });
