"""WAV load and basic level helpers -- split out of analyze_render.py."""

from __future__ import annotations

import math
import wave

import numpy as np


def load_wav(path):
    with wave.open(path, "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        sr = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if sw != 2:
        raise SystemExit(f"expected 16-bit wav, got sampwidth={sw}")
    data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    data = data.reshape(-1, nch)
    if nch == 1:
        data = np.repeat(data, 2, axis=1)
    return data, sr


def db(x):
    return 20.0 * math.log10(max(float(x), 1e-12))


def rms(x):
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(x))))


def rms_db(x):
    return db(rms(x))


def mono(x):
    return 0.5 * (x[:, 0] + x[:, 1])
