"""Theme packs: JSON preset overrides over AmbientConfig. A pack is a small
JSON file whitelisted against PACK_SCHEMA (per-field clamp bounds / allowed
sets); never raises -- a bad or missing pack degrades to {} (built-in
defaults). Search path: <repo>/themes/*.json, then ~/.config/claudio/themes/
*.json (user dir wins on a name clash). See CLAUDE.md / docs/THEMES.md."""

from __future__ import annotations

import json
import math
import os

from ambient_layers import DROP_TIMBRES, DONE_CADENCE_MODES
from logging_setup import get_logger

log = get_logger("themes")

# Numeric fields: name -> (lo, hi) clamp bounds. String fields: name ->
# allowed value tuple. Deliberately excludes derived-at-import constants
# (DROP_RATE_CAP, OU taus, duck envelope shape), array/structural fields, and
# the dead NOTE_REVERB_SEND_DB -- see docs/THEMES.md / the theme-packs plan.
PACK_SCHEMA = {
    "drop_timbre": DROP_TIMBRES,
    "done_cadence": DONE_CADENCE_MODES,
    "DROP_RATE_SCALE": (0.1, 1.5),
    "DROP_MIN_GAP_S": (0.05, 1.0),
    "BURST_COALESCE_WINDOW_S": (0.1, 2.0),
    "DROP_CAL_DB": (-10, 8),
    "DROP_AMP_SPREAD_DB": (0, 8),
    "BED_LP_BASE_HZ": (300, 2400),
    "BED_LP_MAX_HZ": (600, 6000),
    "BED_CAL_DB": (13, 25),
    "MIDLAYER_CAL_DB": (15, 27),
    "MIDLAYER_LP_HZ": (500, 6000),
    "AIR_CAL_DB": (13, 25),
    "AIR_TILT_HI_IDLE_HZ": (800, 6000),
    "AIR_TILT_HI_ACTIVE_HZ": (1000, 8000),
    "AIR_V23_HARD_CEILING_HZ": (1000, 8000),
    "AIR_V23_LEVEL_CUT_DB": (-12, 0),
    "AIR_FLOOR_OFFSET_DB": (-8, 3),
    "AIR_ACTIVITY_RANGE_DB": (0, 10),
    "AIR_GLOOM_DEPTH": (0, 1),
    "NOTE_EMBED_CAP_DB": (4, 16),
    "NOTE_EMBED_CAP_IDLE_DB": (2, 12),
    "KNOCK_EMBED_CAP_DB": (10, 22),
    "DUCK_DEPTH_DB": (0, 6),
    "NOTE_DIRECT_FRAC": (0, 1.2),
    "NOTE_REVERB_FRAC": (0, 1.0),
    "AMBIENT_WET_GAIN": (0, 1.0),
    "AMBIENT_DRY_GAIN": (0.5, 2.0),
    "STEM_CAL_DB": (12, 24),
    "STEM_LP_HZ": (200, 4000),
    "SUBBASS_CAL_DB": (12, 24),
    "WHOOSH_CAL_DB": (24, 36),
    "SETTLED_BED_DB": (-50, -28),
    "SETTLED_BED_TAU_S": (2, 20),
    "SETTLED_BLOOM_RATE_SCALE": (0, 1),
}

# Reserved metadata keys: shared packs carry these for humans, never for the
# engine. Silently skipped (no warning) -- warn-per-key here would make every
# community pack noisy.
_RESERVED_KEYS = {"name", "author", "description", "version"}

_MAX_PACK_BYTES = 64 * 1024  # size-bomb guard

# Module-level names (not constants folded at import) so tests can monkeypatch
# them to point at a tmp_path.
_REPO_THEMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "themes")
_USER_THEMES_DIR = os.path.expanduser("~/.config/claudio/themes")


def _dir_pack_files(dirpath: str) -> dict:
    out = {}
    try:
        names = os.listdir(dirpath)
    except OSError:
        return out
    for fname in names:
        if fname.endswith(".json"):
            out[fname[:-5]] = os.path.join(dirpath, fname)
    return out


def _pack_files() -> dict:
    """name -> path. Repo dir scanned first, then the user dir; a name
    present in both resolves to the user dir's file (user override wins)."""
    files = _dir_pack_files(_REPO_THEMES_DIR)
    files.update(_dir_pack_files(_USER_THEMES_DIR))
    return files


def list_packs() -> list:
    return sorted(_pack_files())


def _norm_value(key, value):
    """Validate/clamp a single field's value. Returns (ok, value)."""
    allowed = PACK_SCHEMA[key]
    if isinstance(allowed, tuple) and allowed and isinstance(allowed[0], str):
        if not isinstance(value, str) or value not in allowed:
            log.warning("theme pack: %r has invalid value %r, dropping", key, value)
            return False, None
        return True, value
    # numeric field: (lo, hi)
    try:
        f = float(value)
    except (TypeError, ValueError):
        log.warning("theme pack: %r has non-numeric value %r, dropping", key, value)
        return False, None
    if not math.isfinite(f):
        # json.load accepts the literal tokens Infinity/-Infinity/NaN -- a
        # known poisoning class (same one fixed for the /config API).
        log.warning("theme pack: %r has non-finite value %r, dropping", key, value)
        return False, None
    lo, hi = allowed
    if f < lo or f > hi:
        clamped = max(lo, min(hi, f))
        log.warning("theme pack: %r=%r out of range [%s, %s], clamping to %s",
                    key, value, lo, hi, clamped)
        f = clamped
    return True, f


def load_pack(name: str) -> dict:
    """Load and validate a theme pack by name. Never raises: an unknown
    name, corrupt JSON, or a non-dict body all log a warning and return {}
    (built-in defaults)."""
    path = _pack_files().get(name)
    if path is None:
        log.warning("theme pack %r not found", name)
        return {}
    try:
        if os.path.getsize(path) > _MAX_PACK_BYTES:
            log.warning("theme pack %r too large (>%d bytes), ignoring", name, _MAX_PACK_BYTES)
            return {}
        with open(path, "r") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        log.warning("theme pack %r: cannot read/parse (%s), ignoring", name, exc)
        return {}
    if not isinstance(raw, dict):
        log.warning("theme pack %r: body is not a JSON object, ignoring", name)
        return {}

    overrides = {}
    for key, value in raw.items():
        if key in _RESERVED_KEYS or key.startswith("_"):
            continue
        if key not in PACK_SCHEMA:
            log.warning("theme pack %r: unknown key %r, dropping", name, key)
            continue
        ok, v = _norm_value(key, value)
        if ok:
            overrides[key] = v
    return overrides
