/* Claudio activity log — polls /events and renders a scrolling row-per-event log. */

"use strict";

const $ = (id) => document.getElementById(id);

const ICONS = { spawn: "✸", subagent: "✸", fail: "⚠", done: "✓", attention: "●", session: "▸" };

const MAX_ROWS = 500;
const POLL_MS = 700;
const NEAR_BOTTOM_PX = 48;

let since = 0;
const log = $("log");

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

async function poll() {
  try {
    const res = await fetch(`/events?since=${since}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const events = data.events ?? [];
    if (events.length) since = events[events.length - 1].id;
    render(events);
    setStatus(true, "online");
  } catch {
    setStatus(false, "daemon down");
  }
}

poll();
setInterval(poll, POLL_MS);
