/* Claudio activity log — polls /events and renders a scrolling row-per-event log,
   plus a raw-hook bar graph and live session/subagent presence chips. */

"use strict";

const $ = (id) => document.getElementById(id);

const ICONS = { spawn: "✸", subagent: "✸", fail: "⚠", done: "✓", attention: "●", session: "▸", other: "·" };

const MAX_ROWS = 500;
const POLL_MS = 700;
const NEAR_BOTTOM_PX = 48;
const LABEL_W_KEY = "viz-hook-label-w";
const LABEL_W_MIN = 60;
const LABEL_W_MAX = 320;

// Resizable log columns: css var -> [localStorage key, min, max, default px].
const LOG_COLS = {
  stripe: ["viz-w-stripe", 2, 40, 6],
  time: ["viz-w-time", 40, 200, 74],
  session: ["viz-w-session", 24, 160, 60],
  label: ["viz-w-label", 24, 200, 72],
};

// Fixed hook order so the bar graph layout is stable; unknown hooks append at the end.
const HOOK_ORDER = [
  "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
  "Notification", "PermissionRequest", "Stop", "SubagentStart", "SubagentStop",
  "PreCompact", "SessionEnd", "ContextPressure",
];

// Idx -> color, echoing the audio engine panning each session/subagent to a slot.
const IDX_COLORS = ["#7fb4c9", "#c9d97a", "#a481d9", "#f0a832", "#d4543c", "#7bbf6a", "#f2e14c", "#e07fb4"];
const idxColor = (idx) => IDX_COLORS[idx % IDX_COLORS.length];

let since = 0;
const log = $("log");
const logHeadEl = $("log-head");
const barsEl = $("bars");
const barsWrapEl = barsEl.parentElement; // .bars-track-wrap — hosts --hook-label-w + the resize handle

let prevCounts = {};
let prevSessionN = new Map();
let prevAgentN = new Map();
const hookOrder = [...HOOK_ORDER];
const barEls = new Map(); // hook -> {row, fill, num}

const rowBuf = []; // in-memory buffer of event records, capped at MAX_ROWS
const selectedSessions = new Set(); // empty = "All"
let lastRenderedId = 0; // highest event id currently rendered in #log

function setStatus(ok, text) {
  const el = $("status");
  el.classList.toggle("ok", ok);
  el.classList.toggle("down", !ok);
  el.querySelector(".txt").textContent = text;
}

function isNearBottom() {
  return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM_PX;
}

function buildRow(ev, noAnim) {
  const row = document.createElement("div");
  row.className = noAnim ? `row kind-${ev.kind} no-anim` : `row kind-${ev.kind}`;

  const stripe = document.createElement("span");
  stripe.className = "col-stripe";

  const time = document.createElement("span");
  time.className = "col-time";
  time.textContent = new Date(ev.t * 1000).toLocaleTimeString();

  const icon = document.createElement("span");
  icon.className = "col-icon";
  icon.textContent = ICONS[ev.kind] ?? "";

  const sess = document.createElement("span");
  sess.className = "col-session";
  sess.textContent = ev.session ?? "";

  const label = document.createElement("span");
  label.className = "col-label";
  label.textContent = ev.label ?? "";

  const detail = document.createElement("span");
  detail.className = "col-detail";
  detail.textContent = ev.detail ?? "";
  detail.title = ev.detail ?? "";

  row.append(stripe, time, icon, sess, label, detail);
  return row;
}

function passesFilter(ev) {
  return selectedSessions.size ? ev.session && selectedSessions.has(ev.session) : true;
}

// Full rebuild — only on filter change (session chip / "All" click). Non-animated
// so switching filters doesn't replay row-in on the whole log.
function rebuildLog() {
  const stick = isNearBottom();
  const rows = rowBuf.filter(passesFilter);

  for (const el of [...log.children]) {
    if (el !== logHeadEl) el.remove();
  }
  const frag = document.createDocumentFragment();
  let maxId = 0;
  for (const ev of rows) {
    frag.appendChild(buildRow(ev, true));
    if (ev.id > maxId) maxId = ev.id;
  }
  log.appendChild(frag);
  lastRenderedId = maxId;

  if (stick) log.scrollTop = log.scrollHeight;
}

// Incremental append — every poll. Only appends genuinely new rows, so row-in
// only animates the new arrivals instead of re-running on the whole log.
function appendNewRows() {
  const stick = isNearBottom();
  const newRows = rowBuf.filter((ev) => ev.id > lastRenderedId && passesFilter(ev));

  if (newRows.length) {
    const frag = document.createDocumentFragment();
    let maxId = lastRenderedId;
    for (const ev of newRows) {
      frag.appendChild(buildRow(ev, false));
      if (ev.id > maxId) maxId = ev.id;
    }
    log.appendChild(frag);
    lastRenderedId = maxId;
  }

  // Keep the DOM in sync with rowBuf's cap (log.children includes the sticky header).
  const cap = selectedSessions.size ? rowBuf.filter(passesFilter).length : rowBuf.length;
  while (log.children.length - 1 > cap) log.removeChild(logHeadEl.nextSibling);

  if (stick) log.scrollTop = log.scrollHeight;
}

function bufferEvents(events) {
  if (!events.length) return;
  rowBuf.push(...events);
  if (rowBuf.length > MAX_ROWS) rowBuf.splice(0, rowBuf.length - MAX_ROWS);
}

function flash(el) {
  el.classList.remove("flash");
  void el.offsetWidth; // reflow so a re-trigger within 1s restarts the animation
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1000);
}

function barRow(hook) {
  const row = document.createElement("div");
  row.className = `bar bar-${hook}`;

  const label = document.createElement("span");
  label.className = "bar-label";
  label.textContent = hook;

  const track = document.createElement("span");
  track.className = "bar-track";
  const fill = document.createElement("span");
  fill.className = "bar-fill";
  track.appendChild(fill);

  const num = document.createElement("span");
  num.className = "bar-num";
  num.textContent = "0";

  row.append(label, track, num);
  barsEl.appendChild(row);
  return { row, fill, num };
}

// Sums the per-hook counts of the selected sessions; falls back to the global
// counts when no session filter is active.
function filteredCounts(data) {
  if (!selectedSessions.size) return data.counts ?? {};
  const total = {};
  for (const s of data.sessions ?? []) {
    if (!selectedSessions.has(s.id)) continue;
    for (const [hook, n] of Object.entries(s.counts ?? {})) {
      total[hook] = (total[hook] ?? 0) + n;
    }
  }
  return total;
}

function renderCounts(counts) {
  for (const hook of Object.keys(counts)) {
    if (!hookOrder.includes(hook)) hookOrder.push(hook);
  }
  const maxCount = Math.max(1, ...Object.values(counts));

  for (const hook of hookOrder) {
    let entry = barEls.get(hook);
    if (!entry) {
      entry = barRow(hook);
      barEls.set(hook, entry);
    }
    const count = counts[hook] ?? 0;
    const prev = prevCounts[hook] ?? 0;

    entry.num.textContent = count;
    const pct = count > 0 ? Math.max(4, (Math.sqrt(count) / Math.sqrt(maxCount)) * 100) : 0;
    entry.fill.style.width = `${pct}%`;

    if (count > prev) flash(entry.row);
  }
  prevCounts = counts;
}

function chipKey(idx, id) {
  return `${idx}:${id}`;
}

function toggleSession(id) {
  if (selectedSessions.has(id)) selectedSessions.delete(id);
  else selectedSessions.add(id);
  rebuildLog();
}

function selectAll() {
  selectedSessions.clear();
  rebuildLog();
}

function renderSessionChips(items, prevN) {
  const container = $("sessions");
  const seen = new Set();
  const nextN = new Map();

  // Drop selections for sessions that decayed out of the live list.
  const liveIds = new Set(items.map((s) => s.id));
  for (const id of [...selectedSessions]) {
    if (!liveIds.has(id)) selectedSessions.delete(id);
  }

  let allChip = container.querySelector('[data-key="__all__"]');
  if (!allChip) {
    allChip = document.createElement("button");
    allChip.type = "button";
    allChip.className = "chip chip-all";
    allChip.dataset.key = "__all__";
    allChip.textContent = "All";
    allChip.addEventListener("click", selectAll);
    container.appendChild(allChip);
  }
  allChip.classList.toggle("selected", selectedSessions.size === 0);

  for (const item of items) {
    const key = chipKey(item.idx, item.id);
    seen.add(key);
    nextN.set(key, item.n);

    let chip = container.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!chip) {
      chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip";
      chip.dataset.key = key;
      chip.style.setProperty("--chip-color", idxColor(item.idx));
      chip.addEventListener("click", () => toggleSession(item.id));
      container.appendChild(chip);
    }
    chip.classList.remove("fading");
    chip.classList.toggle("selected", selectedSessions.has(item.id));
    chip.textContent = `S${item.idx} ${item.id}`;
    chip.title = `${item.id} (n=${item.n})`;

    const prev = prevN.get(key);
    if (prev !== undefined && item.n > prev) flash(chip);
  }

  // Remove chips for sessions that dropped out of the live list.
  for (const chip of [...container.children]) {
    const key = chip.dataset.key;
    if (key !== "__all__" && !seen.has(key)) {
      chip.classList.add("fading");
      setTimeout(() => chip.remove(), 300);
    }
  }

  $("sessions-count").textContent = items.length;
  return nextN;
}

// Agent chips are named (agent_type, or a short-id fallback) and colored to
// match their PARENT session, not their own idx -- an agent is that session's
// helper, not a peer session, so it shouldn't get an unrelated color. sessions
// is the latest /events sessions list, used to look up the parent's color +
// "S#" tag by short session id; a parent that's no longer live falls back to
// a muted style instead of a stale/misleading color.
function renderAgentChips(items, prevN, sessions) {
  const container = $("agents");
  const seen = new Set();
  const nextN = new Map();
  const byShortId = new Map(sessions.map((s) => [s.id, s]));

  for (const item of items) {
    const key = chipKey(item.idx, item.id);
    seen.add(key);
    nextN.set(key, item.n);

    let chip = container.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!chip) {
      chip = document.createElement("span");
      chip.className = "chip chip-agent";
      chip.dataset.key = key;
      container.appendChild(chip);
    }
    chip.classList.remove("fading");

    const parent = byShortId.get(item.session);
    chip.classList.toggle("chip-orphan", !parent);
    if (parent) chip.style.setProperty("--chip-color", idxColor(parent.idx));

    const parentTag = parent ? `S${parent.idx}` : (item.session || "?");
    chip.textContent = `${item.name} · ${parentTag}`;
    chip.title = `${item.name} (agent ${item.id}, n=${item.n}) — parent ${item.session || "unknown"}`;

    const prev = prevN.get(key);
    if (prev !== undefined && item.n > prev) flash(chip);
  }

  // Remove chips for agents that dropped out of the live list.
  for (const chip of [...container.children]) {
    if (!seen.has(chip.dataset.key)) {
      chip.classList.add("fading");
      setTimeout(() => chip.remove(), 300);
    }
  }

  $("agents-count").textContent = items.length;
  return nextN;
}

// ---- resizable hook-label column ----
function initLabelResize() {
  const saved = Number(localStorage.getItem(LABEL_W_KEY));
  if (saved) barsWrapEl.style.setProperty("--hook-label-w", `${clampLabelW(saved)}px`);

  const handle = $("bars-resize");
  if (!handle) return;

  let dragging = false;
  let startX = 0;
  let startW = 0;

  const currentW = () => {
    const px = getComputedStyle(barsWrapEl).getPropertyValue("--hook-label-w").trim();
    return parseFloat(px) || 120;
  };

  handle.addEventListener("pointerdown", (e) => {
    dragging = true;
    startX = e.clientX;
    startW = currentW();
    handle.setPointerCapture(e.pointerId);
  });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const w = clampLabelW(startW + (e.clientX - startX));
    barsWrapEl.style.setProperty("--hook-label-w", `${w}px`);
  });
  handle.addEventListener("pointerup", () => {
    if (!dragging) return;
    dragging = false;
    localStorage.setItem(LABEL_W_KEY, String(currentW()));
  });
}

function clampLabelW(w) {
  return Math.min(LABEL_W_MAX, Math.max(LABEL_W_MIN, Math.round(w)));
}

// ---- resizable log columns (stripe/time/session/label) ----
function initLogColResize() {
  for (const [col, [key, min, max, def]] of Object.entries(LOG_COLS)) {
    const varName = `--w-${col}`;
    const clamp = (w) => Math.min(max, Math.max(min, Math.round(w)));

    const saved = Number(localStorage.getItem(key));
    log.style.setProperty(varName, `${clamp(saved || def)}px`);

    const handle = logHeadEl.querySelector(`.col-resize[data-col="${col}"]`);
    if (!handle) continue;

    let dragging = false;
    let startX = 0;
    let startW = 0;
    const currentW = () => parseFloat(getComputedStyle(log).getPropertyValue(varName)) || def;

    handle.addEventListener("pointerdown", (e) => {
      dragging = true;
      startX = e.clientX;
      startW = currentW();
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      log.style.setProperty(varName, `${clamp(startW + (e.clientX - startX))}px`);
    });
    handle.addEventListener("pointerup", () => {
      if (!dragging) return;
      dragging = false;
      localStorage.setItem(key, String(currentW()));
    });
  }
}

async function poll() {
  try {
    const res = await fetch(`/events?since=${since}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    // The daemon's ids restart from 0 on every process restart (curl /restart
    // re-execs it, wiping the in-memory EventLog). If our `since` cursor is
    // now ahead of the server's seq, we're polling a dead cursor against a
    // fresher/smaller id space -- since > any future id, so we'd never see
    // another row again. Detect that and refetch from scratch.
    if (data.seq < since) {
      since = 0;
      return poll();
    }

    const events = data.events ?? [];
    if (events.length) since = events[events.length - 1].id;
    bufferEvents(events);
    appendNewRows();

    renderCounts(filteredCounts(data));
    prevSessionN = renderSessionChips(data.sessions ?? [], prevSessionN);
    prevAgentN = renderAgentChips(data.agents ?? [], prevAgentN, data.sessions ?? []);

    setStatus(true, "online");
  } catch {
    setStatus(false, "daemon down");
  }
}

initLabelResize();
initLogColResize();
poll();
setInterval(poll, POLL_MS);
