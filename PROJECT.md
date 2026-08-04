# claude-geiger — Functional Ambient Audio for AI Coding Agents

**Project dossier: purpose, research record, design history, and current state** _Last updated: August 2026 · Status: v2.2 "Warm Room" shipped, awaiting second blind-listener round_

---

## 1. Purpose

### 1.1 The core idea

AI coding agents like Claude Code do long stretches of autonomous work while the user's attention is elsewhere. Today the only way to know what the agent is doing is to _look_ — which breaks flow, forces context switches, and makes multi-agent supervision exhausting. This project gives the agent a **functional sound**: continuous, real-time audio feedback that lets a user know what the agent is doing _by ear alone_, peripherally, without watching a screen.

The founding metaphor was the **Geiger counter** — the canonical zero-attention auditory display: event rate maps to click rate, the sound _is_ the data, silence is meaningful, and no training is needed. The project's first insight journey was realizing that we wanted the Geiger counter's _information principle_ (continuous rate→sound mapping) but explicitly **not** its literal sound.

The refined framing came from product design: a **vacuum cleaner or a plane engine must make noise** — if it went silent, people would assume it was broken. Well-engineered machines communicate state through sound that is simultaneously informative and pleasant. The goal of this project is to give an AI agent that same quality of engineered operating noise: you always know it's alive, you can hear how hard it's working, you notice when something breaks — and after four hours you are not annoyed.

### 1.2 The five information axes

Defined at project start; they survived every redesign as the _information architecture_, even as the sounds changed completely:

1. **Velocity / workload** — how hard is the agent working right now (token rate, tool-call cadence)?
2. **Tool identity** — _what kind_ of work: reading, writing, executing?
3. **State & outcome** — success, failure, needs-your-permission?
4. **Topology / agent depth** — has the main agent delegated to subagents?
5. **Context pressure** — how full is the context window; when does compaction happen?

### 1.3 The three-module architecture

Fixed early and preserved through every version:

| Module           | What it does                                                                   | Status                                    |
| ---------------- | ------------------------------------------------------------------------------ | ----------------------------------------- |
| **Tracing**      | Observes what the agent is doing (hooks → events → activity model)             | Stable since v1                           |
| **Installation** | Gets the system onto a machine (installer, hooks config, daemon lifecycle)     | Stable since v1                           |
| **Sound**        | Turns events into audio (swappable themes: `geiger` legacy, `ambient` current) | Iterating — this is the research frontier |

---

## 2. Research record

Eight research passes were run across three phases, each feeding a build cycle. This section preserves the essential findings of each.

### Phase A — Feasibility spike (research passes 1–4)

#### 2.1 Claude Code signal surface (what can be sonified)

Claude Code exposes four observable surfaces, each with different latency/granularity:

- **Hooks** (the backbone): ~20+ events — PreToolUse, PostToolUse, PostToolUseFailure, SubagentStart/Stop, Stop, Notification, PermissionRequest, PreCompact, SessionStart/End, UserPromptSubmit, and more. Payloads include tool name and input. Hooks can run **async** (non-blocking) and — decisively — Claude Code supports **HTTP hooks** that POST event JSON straight to a local daemon with zero subprocess cost. Fires at event time; effectively instant.
- **Streaming JSON** (`--output-format stream-json --include-partial-messages`): true token-level deltas (<50 ms), tool names at block start, `parent_tool_use_id` for subagent nesting. Only available in headless/SDK wrapper mode — the path to _true_ token-rate velocity later.
- **Statusline**: frequently-refreshed JSON including context-window usage — the practical source for the context-pressure axis in interactive mode.
- **Transcript JSONL**: tailable session log, ~0.5–2 s latency, internal/unstable format — fallback only.
- **OpenTelemetry** exists but batches on 1–60 s intervals — wrong tool for audio; ignored.

Every one of the five axes has a viable signal source. Velocity in interactive mode is a _proxy_ (leaky integrator over hook-event rate); exact token velocity requires wrapper mode.

#### 2.2 Prior art survey (~40 projects) — the gap

The Claude Code community has built a large ecosystem of **discrete notification sound** projects: chime-on-Stop hooks, themed sample packs (peon-ping's Warcraft voice lines being the breakout hit), TTS announcers (cc-hooks, echook, talkito), and one binary continuous-audio project (Claude-Muzak: elevator music while working, silence when idle). Matt Webb's widely-shared essay (interconnected.org, Sept 2025) explicitly called for adaptive, layered, game-audio-style agent soundscapes and noted nobody had built one.

**What did not exist anywhere:** state-proportional continuous sonification; real-time synthesis (everything shipped plays samples or TTS); polyphonic multi-agent soundscapes; stream-level signal tapping; habituation-aware "pleasant ambience, anomaly = deviation" design. That gap is this project.

**Reusable lessons taken from the community:** (1) non-blocking audio dispatch is table stakes — background daemon, never block a hook; (2) novelty decays fast — variation, few-sounds-by-default, and a mute knob are proven mitigations; (3) the highest-value signal is "needs attention," which must stay acoustically distinct from ambience; (4) sustainable projects map magnitude to pleasant parameters, not alarm parameters; (5) event plumbing is commoditized — all differentiation is in the signal→sound mapping.

#### 2.3 Sonification design science

From the auditory-display literature (Sonification Handbook, ICAD, Gaver's auditory icons, Bregman's auditory scene analysis, Walker's mapping psychophysics):

- **Why the Geiger principle works**: rate→density mapping is analogic (no learned code), pre-attentive, and polarity-unambiguous (more events = more sound; pitch, by contrast, has concept-dependent polarity). Silence is meaningful and costs zero attention.
- **The 3-stream budget**: humans can peripherally monitor only ~3–4 concurrent auditory streams. This forced the central architectural move: **fuse velocity + tool identity + agent depth into ONE stream** (rate = velocity, timbre = tool class, register/pan = subagent), keep success/failure as intermittent event cues, make context pressure an optional third layer.
- **Calm-technology framing** (Weiser & Brown): information should live in the periphery and promote itself to the center only when anomalous. Peep (USENIX 2000) demonstrated this for network monitoring with a nature soundscape — operators heard problems as "the forest sounds wrong."
- **Alarm-fatigue honesty rule** (Edworthy): perceived urgency must match actual urgency. Only failure may be salient. No per-tool success chimes — success is signaled by the stream simply continuing.
- **Timbre is the categorical channel** (max 3–5 unattended classes); register+pan for hierarchy; contour+roughness (not major/minor alone) for valence, for cross-cultural robustness.

#### 2.4 Audio engineering feasibility

- Per-event playback (`afplay` etc. — what all prior art uses) is structurally incapable of continuous audio: 100–500 ms latency, no shared state, no drone, no rate-modulated textures.
- The winning architecture is a **persistent local audio daemon**: HTTP/UDP ingress (localhost UDP one-way ≈ 15 µs), block-based synthesis callback (256 samples @ 48 kHz ≈ 5.3 ms), total event→sound latency **15–30 ms**.
- Python (`sounddevice` + numpy) is the right v0/v1 stack (pip-installable, PortAudio wheels); Rust single binary is the right eventual distribution; SuperCollider is the pro option with too heavy an install; browser/Web Audio is a good optional companion UI.

### Phase B — Pleasantness engineering (research passes 5–7)

Triggered by the pivot: keep the information architecture, replace the Geiger clicks with engineered-pleasant sound ("the vacuum-cleaner principle").

#### 2.5 Psychoacoustics of pleasant product sound

The appliance/automotive industry has quantitative machinery for exactly our problem — designing operating noise that is pleasant _and_ informative:

- **The Zwicker/Fastl metric set**: loudness (sone), sharpness (acum), roughness (asper, worst at ~70 Hz modulation), fluctuation strength (vacil, worst at ~4 Hz modulation), tonality. Zwicker's psychoacoustic annoyance formula combines them; loudness dominates, and sharpness only contributes above 1.75 acum.
- **Key numeric design rules** distilled into our acceptance battery: pink-to-brown spectral slope (−3 to −6 dB/oct); minimal energy above 5 kHz (sharpness); **no amplitude modulation in 0.5–10 Hz** (the 4 Hz fluctuation peak is the syllabic rate of speech — a 4 Hz-modulated sound behaves like someone talking at you forever) nor 20–150 Hz (roughness); no discrete tones protruding from the broadband bed (tonal prominence penalties are the largest per-unit annoyance factor in appliance sound-quality indices); narrow dynamics; sound-masking-industry levels (bed ≤ ambient +5 dB).
- **The information grammar of machines** (validated by EV sound regulation UNECE R138, which _mandates_ informative sound with ≥0.8% pitch change per km/h): pitch tracks rate, loudness/brightness track load, timbral change signals fault, silence means off.
- **The strongest direct evidence for the whole project**: Hildebrandt et al. 2016 — continuous soundscape sonification of a process, monitored peripherally during a demanding primary task, produced _better-timed interventions_ (0.787 vs 0.578 adequacy score) and _fewer late responses_ (1% vs 17%) than threshold alerts, with no primary-task cost. Their system was informative but aesthetically under-engineered — the authors explicitly called for pleasant sound design for long-term use. That is precisely this project's niche.
- **Perceived control** (Stallen's annoyance framework): predictability and a visible mute/volume affordance measurably reduce annoyance — even if never used.
- **Natural-sound bias**: rain/water/wind textures are rated more pleasant and mask better at equal loudness, and are acoustically the PSQ-optimal spectrum. This legitimized the rain design.

#### 2.6 Generative ambient music systems

- **Eno's method** (Music for Airports, Discreet Music, Bloom): a small, fully consonant note pool + incommensurate loop periods = infinite non-repeating, never-wrong texture. The extractable rules: fix the _what_ (consonant pool), randomize only the _when/which_; the system self-plays quietly when idle; "as ignorable as it is interesting."
- **Pentatonic pools** are the enabling trick: no semitones, no tritone → _any_ coincidence of randomly-timed notes is consonant by construction (the wind-chime principle). This is what lets uncorrelated agent events safely trigger notes.
- **Adaptive game audio**: vertical layering (stems fade with state — used for subagents and pressure) vs horizontal re-sequencing; transition rules (fade 4–10 s, harmonic changes only at slow cycle boundaries); Journey's "another player joins = another instrument joins" became our subagent mapping; Minecraft/C418's lesson that sparseness and permission-to-be-silent are the strongest anti-fatigue tools.
- **Music-for-focus research**: lyrics/speech are the main distractor (verbal working-memory competition); slow/low-information/predictable is neutral-to-positive; deliberate periodic amplitude modulation (brain.fm-style) conflicts with the annoyance literature and was rejected.
- **Tension without dissonance** (for failure/pressure): sus/add9 colorings, bass moves to the relative minor, filter darkening, register drops — never semitone clashes in sustained material.

#### 2.7 DSP recipes (all pure numpy/scipy, benchmarked in-container)

Complete parameterized recipes were collected and measured: supersaw pads (±7 cents detune), 2-op FM electric piano (ratio 1:1, the DX7 heritage; velocity → modulation index for played-feel), modal synthesis for wooden percussion (marimba ratios 1:3.9:9.2; woodblock 1:1.47:2.09:2.56), rain as pre-rendered Poisson-timed grains + pink-noise bed (Paul Kellet filter), wind/water via Ornstein–Uhlenbeck-walked bandpass noise, and **Freeverb** with its exact classic tuning constants as the shared room. The full v2 scene renders in ~1 ms per 256-sample block (~20% of one core) — real-time viable with headroom. (Full constants live in research/BRIEF-v2.md / the engine source.)

### Phase C — Pacing & evaluation (research passes 8–9)

Triggered by the first blind-listener feedback ("too fast — losing control").

#### 2.8 Pacing, urgency, and perceived control

- **The smoking gun**: perceived urgency scales with pulse rate as a Stevens power law with exponent ≈ **1.35** (Hellier & Edworthy) — and our v2 activity→rain-rate mapping used exponent **1.3**. We had inadvertently implemented the urgency-maximizing curve. More density _always_ reads as more urgent; the mapping must be compressive (log / exponent 0.3–0.5), never expansive.
- **Auditory numerosity**: humans reliably count only ~4–6 events/s; above ~8/s a stream reads as an urgent sequence; at ~20/s grains **fuse into continuous texture** (Truax). Design consequence: cap discrete drops at ~6/s and convert additional intensity into _wash thickness_ — which is exactly how natural rain encodes "heavier" (drizzle = countable taps; downpour = hiss).
- **Grouping window**: onsets within ~150–300 ms fuse perceptually → coalesce event bursts into one weighted drop (also supported by notification-batching wellbeing research).
- **Biological anchors**: calm periodicities live at breathing (0.1–0.33 Hz) and resting-heart (~1 Hz) rates; medical alarm standards (IEC 60601-1-8) show what urgent cadence deliberately sounds like — stay far below it.
- **Perceived control** (Stallen + progress-bar psychology): a steady, predictable, smoothly-varying bed is the anchor that makes the whole system feel governed; erratic feedback over silence reads as "out of control" regardless of loudness.

#### 2.9 Evaluation methodology (how to measure "usefulness of feedback")

- **The methods menu** (Bonebright & Flowers, Sonification Handbook ch. 6): identification tasks, semantic-differential ratings, discrimination trials, plus procedural rules (naive listeners only, order rotation, "we're testing the sounds, not you," ≤30 min sessions, medians and effect sizes at small n).
- **The gold-standard paradigm** (Hildebrandt et al.): dual task + timestamped peripheral events + _anticipation-optimal intervention scoring_ — measuring whether listeners act at the right times, not just whether they notice.
- **The instrument that formalizes the wife-feedback**: ISO 12913 soundscape circumplex — 8 agreement items (pleasant, vibrant, eventful, chaotic, annoying, monotonous, uneventful, calm) project to a 2-D point (ISOPleasant × ISOEventful). "Too fast / losing control" = the **chaotic** quadrant; our target for routine work is **calm**, with legitimate busy phases reaching **vibrant** (eventful _and_ pleasant) — the fix is rotation on this plane, not just fewer events. ICBEN 0–10 annoyance item, perceived-control Likert items, and detection hit/false-alarm/latency complete the kit.
- **Ambient-display heuristics** (Mankoff 2003) as a free expert checklist, and A–B–A′ field deployment (baseline → sonification → removal; the removal week reveals dependence — "felt blind" = value signal). Mute events are the hardest behavioral annoyance metric.
- **No universal events/min threshold exists** in the literature — the flip point from calm to chaotic must be found empirically per design, which is exactly what the evaluation kit's Block C (same session at three densities) does.

---

## 3. Design history

### v0/v1 — "Geiger" (proof of signal)

Single-file Python daemon (~1000 lines): Poisson click train driven by a leaky-integrator activity model (τ = 3 s), three click timbres by tool class, chimes for failure/done/attention, subagent register shift, optional context drone. HTTP+UDP ingress on port 9753, hooks pack, installer with safe jq merge, session simulator, offline `--render` mode for testing without an audio device. Adversarial verification found and fixed 10 bugs (thread races, unbounded buffers, NaN wedges, an installer path bug). **Verdict: information architecture works; sound is harsh by design metrics — kept as the `geiger` legacy theme.**

### v2 — "Ambient" (pleasantness engineering)

Sound layer rebuilt from Phase B research: warm supersaw/additive bed in C over a C-major-pentatonic pool, rain-grain stream as the velocity channel, FM e-piano bloom notes for writes, wooden knock + room-darkening for failure (no alarm stingers), subagent choir stems, context-pressure "weather," everything through one shared Freeverb. A 14-item numeric acceptance battery derived from the psychoacoustic research was implemented (`tools/analyze_render.py`); verification found 11 more bugs (including the bed sitting 23 dB under its design level) and tuned until all items passed. 83 tests.

### First blind-listener round (n=1, earphones, no priming)

- On the dense showcase demo: _"too fast — like losing control."_
- On the realistic-cadence render: _"confusing; a little anxiety; like a dark cave; can't tell if birds or drops; a far-away bing puts me under pressure; left/right difference; not regular; maybe under the sea; feels isolated, confused, lost."_

Every phrase mapped to an identifiable engineering cause — this feedback was the most valuable single input of the project:

| Listener phrase            | Diagnosed cause                                                                           |
| -------------------------- | ----------------------------------------------------------------------------------------- |
| too fast / losing control  | expansive rate map (exp 1.3 ≈ published urgency exponent 1.35); up to 40 discrete drops/s |
| dark cave / sea / isolated | centroid 157 Hz (far too dark), long wet reverb, near-silent bed = big empty space        |
| birds or drops?            | drops synthesized as downward sine _chirps_ — the bird-call signature                     |
| far-away bing = pressure   | salient pitched notes over a too-quiet bed = "distant notification" grammar               |
| left/right difference      | full-width random panning + decorrelation                                                 |
| not regular / lost         | no audible steady anchor under irregular events (Stallen: predictability = control)       |

### v2.2 — "Warm Room" (current)

All six causes fixed, each converted into a permanent numeric regression check (`tools/complaint_checks.py`):

- **Pacing**: logarithmic rate map, hard cap 6 discrete drops/s, activity beyond that thickens the continuous wash instead (the natural-rain encoding); 250 ms burst coalescing (1 event = 1 weighted drop); pacing floors; strict 1:1 at low activity.
- **Space**: RT60 1.86 s warm room (was cavernous); centroid raised to 400–530 Hz; bed always clearly audible (the "engine idle" anchor, −24 dBFS at its quietest).
- **Identity**: drops are now broadband noise ticks (physically cannot read as birds — verified by a chirp-signature metric that the old sound fails and the new sound passes); bell timbre removed from routine flow.
- **Embedding**: pitched one-shots ≤ bed + 10 dB; failure knock made detectable by a 3 dB "room pause" duck instead of loudness.
- **Stereo**: |L−R| ≤ 1 dB per 5 s window; constrained panning.
- Final state: 97 tests, 106 battery checks passing across 5 render types, 13/13 complaint-suite checks passing.

### The evaluation kit (`eval/`)

Built from Phase C research so future feedback is scored, not anecdotal: 15 pre-rendered clips (8 vocabulary, 4 scenarios with ground-truth timestamps, 3 pacing variants of one session for the personal density-threshold probe); a printable scoring sheet (comprehension probes, 8 semantic-differential pairs, the ISO 12913 8-item circumplex block, ICBEN annoyance item, perceived-control items); a scorer computing information-transfer score, affect score, and ISOPleasant/ISOEventful coordinates; verbatim anti-priming instructions; separate answer key.

---

## 4. Current state & roadmap

**Shipped and verified (by measurement):** the full pipeline — hooks → daemon → themed synthesis → renders — with deterministic offline rendering, a psychoacoustic acceptance battery, a listener-complaint regression suite, an evaluation kit, and two themes.

**Honest limitations:** no human has yet heard v2.2 (all quality claims are calibrated numeric proxies); live audio has never run against real hardware (the dev container has no sound device — DSP is validated through the identical offline path); the mix is headphone-tuned (bass-forward; laptop speakers will thin it); context-pressure has no real producer yet (needs the statusline feed); true token-velocity needs wrapper mode; realistic sessions are now _very_ sparse by design — if listeners report "nothing there," the lever is drop salience, not the rate map.

**Next steps, in order:** (1) second blind-listener round using the scoring sheet — place v2.2 on the pleasant×eventful plane and compare against the v2 point; (2) find the personal density threshold with the Block C clips; (3) first live deployment on real hardware during real Claude Code work, A–B–A′ protocol with mute-event logging; (4) wire the statusline → ContextPressure producer; (5) multi-session polyphony and the wrapper-mode true-velocity feed; (6) distribution hardening (Rust single binary was researched and selected as the v-next stack).

**The long-term claim being tested:** that an agent with well-engineered operating sound is _supervisable by ear_ — users intervene at the right moments more often, check screens less, and tolerate the sound for full working days. The Hildebrandt result says the first part is achievable; the product-sound literature says the second is an engineering discipline, not luck; this project is the attempt to do both at once.

---

## 5. Key sources (curated)

**Signal surface:** Claude Code hooks/statusline/streaming docs (code.claude.com/docs). **Prior art:** peon-ping (+HN thread), claudio, cc-hooks, Claude-Muzak, Matt Webb "Get your Claude Code muzak here" (interconnected.org), Carmelyne "Giving Your AI Agents a Voice," Peep the Network Auralizer (USENIX LISA 2000), Listen to Wikipedia (hatnote). **Sonification & calm tech:** Sonification Handbook chs. 6, 14, 18 (Bonebright & Flowers; earcons; Vickers on process monitoring); Gaver, The SonicFinder (1989); Weiser & Brown, Designing Calm Technology (1995); Mynatt et al., Audio Aura (CHI 1998); Bregman, Auditory Scene Analysis (1990); Walker, mapping-polarity psychophysics (JEP:Applied 2002); Hildebrandt, Herrmann & Rinderle-Ma, continuous sonification for peripheral process monitoring (IJHCS 2016). **Psychoacoustics & product sound:** Zwicker & Fastl metrics (ISO 532-1, DIN 45692, ECMA-418); Edworthy/Hellier perceived-urgency power laws (Human Factors 1991/1993); vacuum-cleaner sound-quality studies (Appl. Sci. 13:6136; Acoustics 3:35); Altinsoy, European Sound Label; UNECE R138 EV sound regulation; Stallen, noise-annoyance control framework (1999); sound-masking industry spectra (NC/RC curves). **Generative music:** Reverb Machine's Music for Airports deconstruction; teropa.info "How Generative Music Works"; Bloom (Eno/Chilvers); Wintory on adaptive game scoring; C418/Minecraft analyses; 65daysofstatic / No Man's Sky. **DSP:** JOS, Physical Audio Signal Processing (Freeverb, Schroeder); Karplus & Strong (1983); Chowning FM (1973); Farnell, Designing Sound; musicdsp.org (Kellet pink filter). **Pacing & evaluation:** Truax on grain fusion (~20 Hz); auditory numerosity studies; IEC 60601-1-8 alarm cadences; Fitz & Kushlev, notification batching (CHB 2019); ISO/TS 12913-2/3 soundscape circumplex; ISO/TS 15666 / ICBEN annoyance items; Mankoff et al., ambient-display heuristics (CHI 2003); Matthews et al., Evaluating Peripheral Displays.

_Full citation lists with URLs are preserved in the research transcripts and in research/BRIEF-v2.md / research/BRIEF-v2.2.md / research/VERIFICATION.md alongside this file._
