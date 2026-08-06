/* Clawdio control panel.
   One declarative table maps config keys to controls; adding a knob later
   means one backend key + one entry here (+ markup). */

"use strict";

const $ = (id) => document.getElementById(id);

// key -> {read: () => value, write: (value) => void}
const CONTROLS = {
  theme: {
    read: () => $("theme").querySelector("button.on")?.dataset.value,
    write: (v) => {
      for (const b of $("theme").querySelectorAll("button")) {
        b.classList.toggle("on", b.dataset.value === v);
        b.setAttribute("aria-checked", String(b.dataset.value === v));
      }
      relabelVoices(v);
    },
  },
  volume: {
    read: () => Number($("volume").value),
    write: (v) => { $("volume").value = v; paintVolume(v); },
  },
  mute: {
    read: () => $("mute").getAttribute("aria-pressed") === "true",
    write: (v) => $("mute").setAttribute("aria-pressed", String(v)),
  },
  clicks: { read: () => $("clicks").checked, write: (v) => { $("clicks").checked = v; } },
  chimes: { read: () => $("chimes").checked, write: (v) => { $("chimes").checked = v; } },
  drone: { read: () => $("drone").checked, write: (v) => { $("drone").checked = v; } },
  quiet: { read: () => $("quiet").checked, write: (v) => { $("quiet").checked = v; } },
  idle_exit_min: {
    read: () => Number($("idle_exit_min").value),
    write: (v) => { $("idle_exit_min").value = v; },
  },
  port: { read: () => Number($("port").value), write: (v) => { $("port").value = v; } },
};

function paintVolume(v) {
  $("volume").style.setProperty("--fill", `${Math.round(v * 100)}%`);
  $("volume-val").textContent = Math.round(v * 100);
}

function relabelVoices(theme) {
  for (const span of document.querySelectorAll(".switches span[data-ambient]")) {
    span.textContent = theme === "geiger" ? span.dataset.geiger : span.dataset.ambient;
  }
  // Drone is a GeigerTheme-only voice (AmbientTheme.render_block never reads
  // drone_enabled); hide the control rather than let it toggle a no-op.
  $("drone").closest(".switch").hidden = theme !== "geiger";
}

function showBanner(pending) {
  $("banner").hidden = !pending;
}

async function push(updates) {
  try {
    const res = await fetch("/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    showBanner(data.pending_restart);
  } catch (err) {
    setStatus(false, "save failed");
    console.error("config save failed:", err);
  }
}

function setStatus(ok, text) {
  const el = $("status");
  el.classList.toggle("ok", ok);
  el.classList.toggle("down", !ok);
  el.querySelector(".txt").textContent = text;
}

function paintMeter(activity) {
  // Leaky-integrator activity, roughly events/sec; ~5 lights the full bar.
  const lit = Math.min(10, Math.round((1 - Math.exp(-activity / 2)) * 10));
  document.querySelectorAll("#meter i").forEach((seg, idx) => {
    seg.classList.toggle("on", idx < lit);
  });
}

async function poll() {
  try {
    const res = await fetch("/health");
    const data = await res.json();
    setStatus(true, "online");
    paintMeter(data.activity ?? 0);
  } catch {
    setStatus(false, "daemon down");
    paintMeter(0);
  }
}

async function restart() {
  showBanner(false);
  setStatus(false, "restarting");
  try { await fetch("/restart", { method: "POST" }); } catch { /* expected: it dies */ }
  // The daemon re-execs in place; wait for /health to come back, then re-sync.
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      await fetch("/health");
      await load();
      return;
    } catch { /* not back yet */ }
  }
  setStatus(false, "did not come back");
}

async function load() {
  const res = await fetch("/config");
  const data = await res.json();
  for (const [key, value] of Object.entries(data.config)) {
    CONTROLS[key]?.write(value);
  }
  $("cfgpath").textContent = `cfg ${data.config_path}`;
  $("version").textContent = data.version ? `v${data.version}` : "";
  showBanner(data.pending_restart);
  setStatus(true, "online");
}

function wire() {
  $("theme").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-value]");
    if (!btn) return;
    CONTROLS.theme.write(btn.dataset.value);
    push({ theme: btn.dataset.value });
  });

  let volTimer = null;
  $("volume").addEventListener("input", () => {
    paintVolume(CONTROLS.volume.read());
    clearTimeout(volTimer);
    volTimer = setTimeout(() => push({ volume: CONTROLS.volume.read() }), 150);
  });

  $("mute").addEventListener("click", () => {
    const next = !CONTROLS.mute.read();
    CONTROLS.mute.write(next);
    push({ mute: next });
  });

  for (const key of ["clicks", "chimes", "drone", "quiet"]) {
    $(key).addEventListener("change", () => push({ [key]: CONTROLS[key].read() }));
  }
  for (const key of ["idle_exit_min", "port"]) {
    $(key).addEventListener("change", () => push({ [key]: CONTROLS[key].read() }));
  }

  $("restart").addEventListener("click", restart);
}

wire();
load().catch(() => setStatus(false, "daemon down"));
poll();
setInterval(poll, 1000);
