/* Claudio activity log — polls /events and renders a scrolling row-per-event log,
   plus a raw-hook bar graph and live session/subagent presence chips. */

"use strict";

const $ = (id) => document.getElementById(id);

const ICONS = { spawn: "✸", subagent: "✸", fail: "⚠", done: "✓", attention: "●", session: "▸" };

const MAX_ROWS = 500;
const POLL_MS = 700;
const NEAR_BOTTOM_PX = 48;

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
const barsEl = $("bars");

let prevCounts = {};
let prevSessionN = new Map();
let prevAgentN = new Map();
const hookOrder = [...HOOK_ORDER];
const barEls = new Map(); // hook -> {row, fill, num}

function setStatus(ok, text) {
  const el = $("status");
  el.classList.toggle("ok", ok);
  el.classList.toggle("down", !ok);
  el.querySelector(".txt").textContent = text;
}

function isNearBottom() {
  return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM_PX;
}

function appendRow(ev) {
  const row = document.createElement("div");
  row.className = `row kind-${ev.kind}`;

  const time = document.createElement("span");
  time.className = "col-time";
  time.textContent = new Date(ev.t * 1000).toLocaleTimeString();

  const icon = document.createElement("span");
  icon.className = "col-icon";
  icon.textContent = ICONS[ev.kind] ?? "";

  const label = document.createElement("span");
  label.className = "col-label";
  label.textContent = ev.label ?? "";

  const detail = document.createElement("span");
  detail.className = "col-detail";
  detail.textContent = ev.detail ?? "";

  row.append(time, icon, label, detail);
  log.appendChild(row);
}

function render(events) {
  if (!events.length) return;
  const stick = isNearBottom();
  for (const ev of events) appendRow(ev);
  while (log.childElementCount > MAX_ROWS) log.removeChild(log.firstChild);
  if (stick) log.scrollTop = log.scrollHeight;
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

function renderPresence(containerId, countId, items, prevN, small) {
  const container = $(containerId);
  const seen = new Set();
  const nextN = new Map();

  for (const item of items) {
    const key = chipKey(item.idx, item.id);
    seen.add(key);
    nextN.set(key, item.n);

    let chip = container.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!chip) {
      chip = document.createElement("span");
      chip.className = small ? "chip chip-sm" : "chip";
      chip.dataset.key = key;
      chip.style.setProperty("--chip-color", idxColor(item.idx));
      container.appendChild(chip);
    }
    chip.classList.remove("fading");
    chip.textContent = small ? "" : `S${item.idx} ${item.id}`;
    chip.title = `${item.id} (n=${item.n})`;

    const prev = prevN.get(key);
    if (prev !== undefined && item.n > prev) flash(chip);
  }

  // Remove chips for sessions/agents that dropped out of the live list.
  for (const chip of [...container.children]) {
    if (!seen.has(chip.dataset.key)) {
      chip.classList.add("fading");
      setTimeout(() => chip.remove(), 300);
    }
  }

  $(countId).textContent = items.length;
  return nextN;
}

async function poll() {
  try {
    const res = await fetch(`/events?since=${since}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const events = data.events ?? [];
    if (events.length) since = events[events.length - 1].id;
    render(events);

    renderCounts(data.counts ?? {});
    prevSessionN = renderPresence("sessions", "sessions-count", data.sessions ?? [], prevSessionN, false);
    prevAgentN = renderPresence("agents", "agents-count", data.agents ?? [], prevAgentN, true);

    setStatus(true, "online");
  } catch {
    setStatus(false, "daemon down");
  }
}

poll();
setInterval(poll, POLL_MS);
