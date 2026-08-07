# claude-geiger v2 — "Ambient" Sound Design Brief

Coordinator synthesis of three research passes (product-sound psychoacoustics, generative ambient music, numpy DSP recipes). This brief is the single source of truth for the v2 sound layer. The tracing layer (hooks/ingress/event mapping plumbing) and installation layer are UNCHANGED. Only the sound is replaced.

## 0. Design philosophy (from research)

The vacuum-cleaner/plane-engine principle: a well-engineered machine makes continuous noise that is (a) pleasant, (b) informative, (c) whose *silence* means "off/broken". v1's Geiger clicks carried information but scored badly on pleasantness. v2 keeps the *information architecture* of v1 (rate channel + event cues + state layers) and re-skins it with three research-backed pillars:

1. **Psychoacoustic pleasantness targets** (Zwicker/Fastl, product-sound-quality industry): pink-ish falling spectrum (−3 to −6 dB/oct), sharpness low (little energy >5 kHz), NO amplitude modulation in the 0.5–10 Hz band (fluctuation strength peaks at 4 Hz = maximally annoying) nor 20–150 Hz (roughness), no prominent discrete tones sticking out of the bed, narrow dynamics, states signaled by ≥1% pitch / ≥1.5 dB / ≥10% centroid changes glided over seconds.
2. **Eno-style generativity** (Music for Airports / Bloom): a fixed consonant note pool (major pentatonic — no semitones, no tritone → ANY coincidence of notes is consonant by construction); randomness lives only in timing/selection; incommensurate periods/Poisson timing so texture never audibly cycles; system self-plays quietly when idle.
3. **Rain as the pleasant Geiger counter**: individually random broadband grains whose density is an instantly readable rate variable, universally tolerated for hours, pink-ish spectrum by nature.

## 1. Musical framework

- Root: **C** (drone at C2 ≈ 65.4 Hz and C3). Note pool: **C major pentatonic over 3 octaves** — degrees [0,2,4,7,9] from C3 (MIDI 48): pool = [48+12*o+d for o in 0..2 for d in [0,2,4,7,9]]. Weight lower octaves more (p ∝ 1/(1+octave)).
- Harmony changes ONLY as slow recolorings at cycle boundaries (~24 s cycle): bass root may move C↔A (I↔vi) for dark shading; sus2/sus4 colorings allowed; NEVER introduce semitone clashes or tritones in sustained material.
- One shared reverb (Freeverb, §4) = one coherent "room". Everything except the sub-bass pressure drone goes through it.

## 2. Layer architecture (max 5–6 audible elements)

### L1 — Bed pad (always on while session alive)
- Stereo supersaw: 7 saws, ±7 cents linear spread, center saw ×1.0 / sides ×0.7, independent random phases per channel, L/R offset ±1 cent → 2nd-order lowpass, base cutoff ~700 Hz, Q≤1. Fundamental C2 (65.41 Hz) + a quiet C3 layer. Naive saws are fine at these fundamentals under a ≤1 kHz LP (measured alias ≤ −40 dB).
- Add a sparse additive layer: 6 sines at harmonics [1,2,3,4,6,8] of C2, amps ~1/k², each with an independent Ornstein–Uhlenbeck amplitude walk (tau 2–5 s, ±30%) — slow overtone shimmer, tambura-style.
- Cutoff and gain drift on OU walks (tau 4–8 s, cutoff ±0.3 oct, gain ±2 dB). NO periodic LFO ≥0.5 Hz. NO tremolo.
- Levels: active session bed ≈ −26 dBFS RMS; idle (no events > 90 s) fades to ≈ −35 dBFS over 10 s; after >10 min idle fade further to barely-there (−45 dBFS) but NEVER digital zero while session alive. SessionEnd → graceful 4 s fade to true silence (silence = off, meaningful).

### L2 — Rain grain stream (instant activity channel — the Geiger replacement)
- Pre-render 12–16 drop variants at init: 5–20 ms, exp-decaying downward sine chirp (start log-uniform 1.2–4 kHz, sweep to 0.6×, tau 3–8 ms) OR 5 ms noise burst → RBJ bandpass 2–4 kHz Q 3–8; whole drop bus lowpassed ~4.5 kHz. Random equal-power pan per drop, log-uniform amplitude over 12 dB.
- Each tool event immediately spawns 1–3 drops (instant twitch feedback). Additionally the v1 activity leaky-integrator (KEEP IT, tau 3 s) drives a Poisson drop rate: rate = 2 + 38·min(a,1)^1.3 drops/s (idle → gentle sparse drips only from events; hot ≈ 40/s — well below the ~500/s fusion point). A light pink-noise rain bed (Paul Kellet 3-pole pink filter) fades in with activity (gain −∞ → −30 dBFS as a→1).
- Tool-class voicing of drops: read → smallest/brightest drops (f0 ×1.3, −4 dB); write → ALSO triggers an L3 note (see below) 30% of the time; exec → drops with f0 ×0.6 and a subtle 150–400 Hz filtered-noise "whoosh" swell while a Bash tool is in flight (start on PreToolUse Bash, end on PostToolUse/Failure, gain −34 dBFS, attack/release ≥ 1.5 s).

### L3 — Melodic bloom (trend channel, Eno layer)
- Voice: 2-op FM electric-piano/kalimba. r = fm/fc = 1.0, I_peak = 0.5 + velocity·2.0, mod-index env tau 0.15–0.3 s, amp env: raised-cosine attack 5–8 ms, decay tau 0.8–1.5 s. Optional soft bell accent voice r = 3.5, I ≤ 2 for occasional sparkle (p ≈ 0.1). Enforce Carson bound fc + fm·(I+2) < 21.6 kHz.
- Scheduler: Poisson with refractory — global min interval 1.5 s; per-pitch refractory 10 s; velocity 0.3 + 0.5·rand²; occasional 2–3-note cluster (p=0.15, spacing 80–250 ms). Note-emission rate follows a SECOND slow-smoothed activity envelope (tau 15 s): idle ≈ 1 note/45 s (self-playing Bloom idle behavior — the machine audibly idles); busy ≈ 1 note/2.5 s max. Notes from pool §1. Write-tool events may directly trigger a mid-register note (that's the "making things" voice).
- Register plan (anti-masking): read-family accents live 1–4 kHz (small bright), write notes 250 Hz–1 kHz, exec 80–250 Hz.

### L4 — Subagent stems (Journey trick: presence = added voice)
- Subagent refcount ≥1: fade in (5 s equal-power) a shimmer pad — supersaw an octave above bed (C4), darker mix level (−32 dBFS), panned ±0.3 alternating per agent. Refcount 2+: add a low perfect-5th drone (G2) stem. Cap 2 stems. Fade out 8 s on SubagentStop. No spawn swishes — the stem itself is the signal.

### L5 — Context-pressure weather (slow tension without dissonance)
- fill < 0.5: silent. fill 0.5→1.0: sub-bass drone C1 (32.7 Hz, sine + 2nd harmonic, NOT through reverb) fades in to −30 dBFS; global master lowpass slides 6 kHz → 2.5 kHz; above 0.85 bed recolors sus4 (F replaces E in additive layer) and drops get duller (drop LP 4.5 → 2 kHz). Strictly monotonic in fill, slew tau ≥ 5 s. PreCompact event: a soft downward "settling" gesture (2 low pentatonic notes descending, C3→A2) then pressure layers release over 8 s (fill resets via ContextPressure events anyway).

## 3. Discrete event gestures (all consonant-by-construction except the knock)

| Event | Gesture |
|---|---|
| PostToolUseFailure | **Low wooden knock**: modal woodblock (modes ratio 1:1.47:2.09:2.56 on ~190 Hz, tau 30–80 ms, velocity-scaled noise contact transient), dry-ish (low reverb send). PLUS bed shading: bass to vi (A), stays unresolved until next Stop/success; repeated failures each pull master LP down another 300 Hz (floor 1.8 kHz). NO stingers, NO volume spikes — "the room got darker." |
| Stop (turn complete) | 2–4 note descending pentatonic cadence landing on C or G, slightly longer reverb tail, quiet. If darkened by failures: filter lifts back over ~5 s (audible "resolution"). Then bed eases toward idle level. |
| Notification / PermissionRequest | "Held breath": bed dips to idle level over 2 s + sus2 recoloring + ONE soft two-strike wind-chime figure (high pentatonic dyad, e.g. A5 then E5, soft mallet FM r=3.5 I=1.2). Distinct, inviting, not alarming. |
| UserPromptSubmit | Single soft mid acknowledgment note (G3/A3), bed swells back to active level. |
| SubagentStart/Stop | Stem fades only (L4). |
| PreCompact | Settling gesture (L5). |
| SessionStart | Bed fades in from silence over 3 s + one low root note. |
| SessionEnd | Everything fades to true silence over 4 s. |

## 4. Shared reverb — Freeverb (exact)
8 parallel lowpass-feedback combs L: 1116,1188,1277,1356,1422,1491,1557,1617; R = L+23. 4 series allpasses L: 556,441,341,225 (R=L+23), g=0.5. Comb feedback 0.953 (roomsize ~0.9), damp one-pole d=0.4·0.5=0.2–0.4 (use 0.35). Input gain 0.015 on mono sum. Wet generous (ambient), width 1.0. Per-block ring-buffer pattern: all comb delays ≥ blocksize 256 → no intra-block feedback → fully vectorizable; damping one-pole via scipy.signal.lfilter with zi state; allpasses with D<256 processed in ≤D sub-chunks. Denormal guard: add −1e-20 to comb inputs. If scipy unavailable, hand-roll the one-pole per-block (still vectorizable via lfilter-equivalent recursion in numpy with np.frompyfunc? NO — just require scipy OR implement the one-pole with a short Python loop over 256 samples ONLY for the 16 comb filters if measured fast enough; prefer adding scipy as a dependency: it's fine, add `scipy` to PEP 723 deps).

## 5. Glue (mandatory hygiene)
One-pole smoothing on every gain/cutoff (tau 10–50 ms minimum; state layers per §2 slews); raised-cosine attacks ≥5 ms; equal-power crossfades; DC blocker after tanh/noise (R=0.9975); master chain: sum at −12 dBFS headroom → tanh(x·0.6)/0.6 → clip ±0.99; stereo width from decorrelated generation + Freeverb spread (no Haas needed); voice allocator max 10 voices steal-oldest with 3 ms fade; all pre-rendered assets built once at init.

## 6. Engine integration requirements
- New env: `CLAUDIO_THEME=ambient` (DEFAULT) | `geiger` (legacy v1 sound, keep working). Implement themes as two engine classes behind one interface: handle_event(evt), render_block() → (256,2) f32. The ingress/mapping/CLI/ports/env contract from v1 is otherwise unchanged.
- scipy joins numpy in PEP 723 deps (used for lfilter; keep sounddevice optional).
- Blocksize stays 256 @ 48 kHz. Measured budget for full v2 scene ≈ 1.0–1.3 ms/block (~20–25% core) — acceptable; keep callback allocation-light (preallocate; small lfilter temporaries OK).
- --render, --check, --seed, tests must all still work. Existing geiger tests keep passing; add ambient tests.

## 7. Numeric acceptance criteria (verifier runs these on renders)
On a ≥60 s active-state render (and where noted on specific windows):
1. Spectral slope: octave-band fit −3 to −6 dB/oct (63 Hz–8 kHz), bands within ±5 dB of the fit line.
2. HF fraction: energy above 5 kHz ≤ 10% of total; mean spectral centroid ≤ 1.5 kHz.
3. Slow AM: Hilbert-envelope modulation depth ≤ 10% for any single component in 0.5–10 Hz (steady-state windows, excluding scripted event moments).
4. Roughness proxy: envelope modulation depth in 20–150 Hz band ≤ 10%.
5. Tonal prominence: no narrowband peak > ~12 dB above the local (critical-band-ish, ±10%) spectral median except the intended drone fundamental region, which must stay ≤ 15 dB.
6. Crest factor 8–14 dB in 1 s windows on steady bed; no window exceeding long-term CF by >6 dB except scripted knock moments.
7. Stereo: interchannel correlation 0.3–0.9; mono sum has no >3 dB comb notches vs stereo average.
8. Loudness stability: short-term (3 s) RMS-dB max−min ≤ 3 dB within a constant machine state.
9. Information checks: low-activity vs high-activity renders differ by ≥1.5 dB RMS or ≥10% centroid, and rain-grain density visibly monotonic in activity (onset counting); failure knock detectable as a localized 80–400 Hz transient at scripted time; idle ≠ digital silence while session alive; post-SessionEnd tail = true silence.
10. No NaN/inf; renders reproducible per seed.

## 8. Demo (the deliverable MP3)
`demo-session-v2.jsonl` ≈ 170–190 s telling a story: 0–10 s session start + idle bloom → 10–40 s gentle read/browse phase → 40–80 s active build (writes+execs, rain thickens, notes bloom) → ~85 s a failure (knock, room darkens) → 95 s retry succeeds (light returns) → 105–140 s subagent phase (choir thickens) + context pressure rising (weather gathers) → ~145 s PreCompact settle + pressure release → 150–165 s wind-down, Stop cadence → fade to silence. Render with seed fixed, CLAUDIO_DRONE semantics replaced by theme (pressure layer is part of ambient theme, on by default there), encode 192k MP3.
