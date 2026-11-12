// Job matches - front end. Talks to server.py's /api endpoints, or, on the static
// GitHub Pages copy (window.JOBFINDER_STATIC), reads jobs.json and keeps marks in the browser.
const $ = (s, el = document) => el.querySelector(s);
const STATIC = !!window.JOBFINDER_STATIC;
const state = { jobs: [], today: null, view: "open", cats: null, prevVisit: null, polling: null };

const CAT_ORDER = ["hr", "frontdesk", "patient", "psych", "service", "general", "sales"];
const SETTINGS_KEY = "jobfinder.filters";
const MARKS_KEY = "jobfinder.marks";

function store(key, val) {
  try { if (val === undefined) return JSON.parse(localStorage.getItem(key)); localStorage.setItem(key, JSON.stringify(val)); }
  catch { return null; }
}

function visitMarker() {
  // Jobs first seen after her previous visit get a NEW badge (kept for the whole browser session).
  let prev = null;
  try {
    prev = sessionStorage.getItem("prevVisit");
    if (prev === null) {
      prev = localStorage.getItem("lastVisit") || "";
      sessionStorage.setItem("prevVisit", prev);
    }
    // Local-time ISO string, to compare with the server's local first_seen timestamps.
    const now = new Date();
    localStorage.setItem("lastVisit", new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 19));
  } catch { /* storage blocked: no NEW badges */ }
  return prev || null;
}

async function getJson(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function api(path, body) {
  if (STATIC) return staticApi(path, body);
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

// Same answers as server.py, from the files build_static.py writes.
async function staticApi(path, body) {
  if (path === "/api/jobs") {
    const d = await getJson("jobs.json");
    const marks = store(MARKS_KEY) || {};
    d.jobs.forEach(j => { j.mark = marks[j.uid] || null; });
    const now = new Date();
    d.today = new Date(now - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
    return d;
  }
  if (path.startsWith("/api/job?")) {
    const uid = new URLSearchParams(path.split("?")[1]).get("uid");
    const j = state.jobs.find(x => x.uid === uid);
    return j ? getJson(`desc/${j.desc_key}.json`) : { description: "" };
  }
  if (path === "/api/mark") {
    const marks = store(MARKS_KEY) || {};
    if (!body.status || body.status === "none") delete marks[body.uid]; else marks[body.uid] = body.status;
    store(MARKS_KEY, marks);
    return { ok: true };
  }
  return {};
}

function daysAgo(iso) {
  if (!iso) return null;
  const d = (new Date(state.today) - new Date(iso.slice(0, 10))) / 86400000;
  return Math.max(0, Math.round(d));
}
function postedText(iso) {
  const d = daysAgo(iso);
  if (d === null) return "Posting date unknown";
  if (d === 0) return "Posted today";
  if (d === 1) return "Posted yesterday";
  return `Posted ${d} days ago`;
}
function scoreClass(s) { return s >= 80 ? "great" : s >= 65 ? "good" : s >= 50 ? "fair" : "low"; }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// ---------------------------------------------------------------- filters
function readFilters() {
  return {
    q: $("#q").value.trim().toLowerCase(),
    min: +$("#min-score").value,
    days: +$("#days").value,
    commute: +$("#commute").value,
    remote: $("#remote-ok").checked,
    sort: $("#sort").value,
    cats: [...document.querySelectorAll("#cats input:checked")].map(i => i.value),
  };
}
function saveFilters() { store(SETTINGS_KEY, { ...readFilters(), q: "" }); }
function restoreFilters() {
  const f = store(SETTINGS_KEY);
  if (!f) return;
  $("#min-score").value = f.min ?? 50;
  $("#days").value = f.days ?? 14;
  $("#commute").value = f.commute ?? 60;
  $("#remote-ok").checked = f.remote ?? true;
  $("#sort").value = f.sort ?? "score";
  state.cats = f.cats || null;
}

function buildCats() {
  const counts = {}, labels = {};
  for (const j of state.jobs) {
    counts[j.category] = (counts[j.category] || 0) + 1;
    labels[j.category] = j.category_label;
  }
  const keys = Object.keys(counts).sort((a, b) => CAT_ORDER.indexOf(a) - CAT_ORDER.indexOf(b));
  const checked = new Set(state.cats || keys.filter(k => k !== "sales"));
  $("#cats").innerHTML = keys.map(k => `
    <label class="check"><input type="checkbox" value="${k}" ${checked.has(k) ? "checked" : ""}>
      ${esc(labels[k])}<span class="count">${counts[k]}</span></label>`).join("");
}

function visible() {
  const f = readFilters();
  const catSet = new Set(f.cats);
  let out = state.jobs.filter(j => {
    const mark = j.mark || "";
    if (state.view === "open" && (mark === "hidden" || mark === "applied" || j.closed)) return false;
    if (state.view === "saved" && mark !== "saved") return false;
    if (state.view === "applied" && mark !== "applied") return false;
    if (state.view === "hidden" && mark !== "hidden") return false;
    if (state.view === "open") {
      if (j.score < f.min) return false;
      const d = daysAgo(j.posted);
      if (d !== null && d > f.days) return false;
      const loc = j.loc || {};
      const isRemote = loc.place === "Remote";
      if (isRemote && !f.remote) return false;
      if (!isRemote && loc.minutes != null && loc.minutes > f.commute) return false;
      if (!catSet.has(j.category)) return false;
    }
    if (f.q) {
      const hay = `${j.title} ${j.company} ${j.location} ${j.snippet}`.toLowerCase();
      if (!hay.includes(f.q)) return false;
    }
    return true;
  });
  const by = {
    score: (a, b) => b.score - a.score || (b.posted || "").localeCompare(a.posted || ""),
    posted: (a, b) => (b.posted || "").localeCompare(a.posted || "") || b.score - a.score,
    commute: (a, b) => ((a.loc?.minutes ?? 999) - (b.loc?.minutes ?? 999)) || b.score - a.score,
  }[f.sort];
  out.sort(by);
  // The same job is often posted several times (one per opening): show it once.
  const seen = new Map();
  return out.filter(j => {
    const key = `${j.company}|${j.title.trim().toLowerCase()}|${j.loc?.place}`;
    const first = seen.get(key);
    if (first) { first._dupes += 1; return false; }
    j._dupes = 0;
    seen.set(key, j);
    return true;
  });
}

// ---------------------------------------------------------------- render
function isNew(j) { return state.prevVisit && j.first_seen > state.prevVisit; }

function renderStats(list) {
  const open = state.jobs.filter(j => !j.closed && j.mark !== "hidden" && j.mark !== "applied");
  const s = [
    [list.length, "shown"],
    [open.filter(isNew).length, "new since last visit"],
    [open.filter(j => j.score >= 80).length, "great matches (80+)"],
    [state.jobs.filter(j => j.mark === "saved").length, "saved"],
    [state.jobs.filter(j => j.mark === "applied").length, "applied"],
  ];
  $("#stats").innerHTML = s.map(([n, l]) => `<div class="stat"><b>${n}</b><span>${l}</span></div>`).join("");
}

function card(j) {
  const el = $("#card-tpl").content.firstElementChild.cloneNode(true);
  const sc = $(".score", el);
  sc.classList.add(scoreClass(j.score));
  $(".num", sc).textContent = j.score;
  const t = $(".title", el);
  t.textContent = j.title;
  t.href = j.url;
  $(".open", el).href = j.url;
  if (isNew(j)) $(".badge.new", el).classList.remove("hidden");
  $(".company", el).textContent = j.company + (j.setting ? ` · ${j.setting}` : "");

  const loc = j.loc || {};
  const where = loc.place === "Remote" ? "Remote" :
    `${esc(j.location.length > 70 ? loc.place : j.location)}${loc.minutes != null ? ` · ~${loc.minutes} min from Pilsen` : ""}`;
  const bits = [`<span>📍 ${where}</span>`, `<span>🗓 ${postedText(j.posted)}</span>`];
  if (j.salary) bits.push(`<span>💵 ${esc(j.salary)}</span>`);
  if (j.employment) bits.push(`<span>${esc(j.employment)}</span>`);
  if (j.closed) bits.push(`<span><b>No longer listed</b></span>`);
  $(".meta", el).innerHTML = bits.join("");

  const chips = [`<span class="chip">${esc(j.category_label)}</span>`];
  if (j._dupes) chips.push(`<span class="chip">+${j._dupes} similar opening${j._dupes > 1 ? "s" : ""}</span>`);
  if (loc.hybrid) chips.push(`<span class="chip">Hybrid</span>`);
  for (const f of j.flags || []) {
    const good = /plus!/i.test(f);
    chips.push(`<span class="chip ${good ? "good" : "warn"}">${esc(f)}</span>`);
  }
  $(".tags", el).innerHTML = chips.join("");
  $(".snippet", el).textContent = (j.snippet || "").replace(/\s+/g, " ").slice(0, 400);

  const setBtn = (cls, status, on, off) => {
    const b = $(cls, el);
    const active = j.mark === status;
    b.textContent = active ? on : off;
    b.classList.toggle("on", active);
    b.onclick = async () => {
      const next = active ? "none" : status;
      await api("/api/mark", { uid: j.uid, status: next });
      j.mark = next === "none" ? null : next;
      render();
    };
  };
  setBtn(".save", "saved", "★ Saved", "☆ Save");
  setBtn(".applied", "applied", "✓ Applied", "Mark applied");
  setBtn(".hide", "hidden", "Unhide", "Hide");
  if (j.mark === "hidden") el.classList.add("is-hidden");

  $(".why", el).onclick = async () => {
    const box = $(".why-box", el);
    if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
    box.innerHTML = whyTable(j) + `<div class="desc">Loading description...</div>`;
    box.classList.remove("hidden");
    try {
      const d = await api("/api/job?uid=" + encodeURIComponent(j.uid));
      $(".desc", box).textContent = d.description || "No description saved for this posting - open it to read more.";
    } catch { $(".desc", box).textContent = "Couldn't load the description."; }
  };
  return el;
}

function whyTable(j) {
  const rows = (j.breakdown || []).map(p => `
    <tr><td><b>${esc(p.part)}</b><div class="bar"><i style="width:${Math.round(100 * p.points / p.max)}%"></i></div></td>
      <td class="pts">${p.points} / ${p.max}</td><td>${(p.notes || []).map(esc).join("<br>")}</td></tr>`).join("");
  return `<table>${rows}</table>`;
}

function render() {
  const list = visible();
  renderStats(list);
  const box = $("#list");
  box.replaceChildren(...list.slice(0, 300).map(card));
  $("#empty").classList.toggle("hidden", list.length > 0);
}

function renderSources(status, lastRun) {
  // Live status if a refresh ran in this server session, else what the last finished run recorded.
  if (!Object.keys(status.sources || {}).length && lastRun?.summary) status = lastRun.summary;
  const rows = Object.entries(status.sources || {}).map(([name, s]) => `
    <tr><td>${esc(name)}</td><td>${esc(s.state)}</td><td>${s.listed ?? ""}</td><td>${s.kept ?? ""}</td><td>${esc(s.error || "")}</td></tr>`).join("");
  $("#sources").innerHTML = rows ? `<table><tr><th>Source</th><th>Status</th><th>Listed</th><th>Kept</th><th>Note</th></tr>${rows}</table>`
    : `<p class="muted">Source details appear after a refresh in this session.</p>`;
  $("#log").textContent = (status.log || []).join("\n");
  if (lastRun?.finished) {
    const when = new Date(lastRun.finished);
    $("#last-run").textContent = `Updated ${when.toLocaleDateString()} ${when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`
      + (STATIC ? " · refreshes every morning and afternoon" : "");
  } else {
    $("#last-run").textContent = "Not refreshed yet";
  }
}

function renderProgress(status) {
  const running = status.running;
  $("#progress").classList.toggle("hidden", !running);
  $("#refresh-btn").disabled = running;
  $("#refresh-btn").textContent = running ? "Refreshing..." : "Refresh now";
  if (!running) return;
  const src = Object.entries(status.sources || {});
  const done = src.filter(([, s]) => s.state === "ok" || s.state === "error").length;
  $("#progress-text").textContent = `Checking job boards... ${done} of ${src.length} done`;
  $("#progress-sources").innerHTML = src.filter(([, s]) => s.state !== "waiting").map(([n, s]) =>
    `<span class="chip ${s.state === "error" ? "err" : ""}">${esc(n)}${s.state === "ok" ? ` ✓ ${s.kept}` : s.state === "error" ? " ✗" : " …"}</span>`).join("");
}

// ---------------------------------------------------------------- data
async function load() {
  const d = await api("/api/jobs");
  state.jobs = d.jobs;
  state.today = d.today;
  $("#home").textContent = d.home;
  const title = d.name ? `${d.name}'s Job Matches` : "Chicago Job Matches";
  $("#page-title").textContent = title;
  document.title = title;
  buildCats();
  render();
  renderSources(d.status, d.last_run);
  renderProgress(d.status);
  if (d.status.running) poll();
}

function poll() {
  if (state.polling) return;
  state.polling = setInterval(async () => {
    const s = await api("/api/status");
    renderProgress(s);
    if (!s.running) {
      clearInterval(state.polling);
      state.polling = null;
      await load();
    }
  }, 2000);
}

function wire() {
  const rerender = () => { saveFilters(); render(); };
  $("#filters-toggle").onclick = () => {
    const open = $("#filters").classList.toggle("open");
    $("#filters-toggle").setAttribute("aria-expanded", open);
  };
  ["#q", "#min-score"].forEach(s => $(s).addEventListener("input", () => {
    $("#min-score-val").textContent = $("#min-score").value; rerender();
  }));
  ["#days", "#commute", "#remote-ok", "#sort"].forEach(s => $(s).addEventListener("change", rerender));
  $("#cats").addEventListener("change", () => { state.cats = readFilters().cats; rerender(); });
  document.querySelectorAll("#view button").forEach(b => b.onclick = () => {
    state.view = b.dataset.view;
    document.querySelectorAll("#view button").forEach(x => x.classList.toggle("on", x === b));
    render();
  });
  $("#refresh-btn").onclick = async () => { await api("/api/refresh", {}); poll(); renderProgress({ running: true, sources: {} }); };
  $("#rescore-btn").onclick = async (e) => {
    e.target.disabled = true;
    const r = await api("/api/rescore", {});
    e.target.disabled = false;
    e.target.textContent = `Re-scored ${r.count}`;
    await load();
  };
}

state.prevVisit = visitMarker();
if (STATIC) {
  // Refreshing and re-scoring happen on GitHub's schedule, not from the page.
  $("#refresh-btn").classList.add("hidden");
  $("#rescore-btn").classList.add("hidden");
  $(".tips p:last-of-type").classList.add("hidden");
}
restoreFilters();
$("#min-score-val").textContent = $("#min-score").value;
wire();
load().catch(err => {
  $("#list").innerHTML = STATIC
    ? `<p class="empty">Couldn't load the job list (${esc(err.message)}). Try reloading the page.</p>`
    : `<p class="empty">Couldn't reach the job finder server (${esc(err.message)}). Start it with <b>Start Job Finder.bat</b>.</p>`;
});
