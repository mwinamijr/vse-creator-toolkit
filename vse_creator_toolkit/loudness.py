# SPDX-License-Identifier: GPL-3.0-or-later
"""Integrated loudness (LUFS) per ITU-R BS.1770-4 / EBU R128.

K-weighting uses the two standard biquads (high-shelf + RLB high-pass), with
coefficients computed for any sample rate and applied by Audaspace's IIR
``filter`` (fast, native). Gating: 400 ms blocks with 75 % overlap, an absolute
gate at -70 LUFS and a relative gate 10 LU below the ungated mean.
"""
import math

import numpy as np

ABS_GATE = -70.0
REL_GATE = -10.0
BLOCK = 0.4
OVERLAP = 0.75


def _biquad_high_shelf(fs, fc=1500.0, gain_db=4.0, q=1 / math.sqrt(2)):
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * fc / fs
    alpha = math.sin(w0) / (2 * q)
    c = math.cos(w0)
    b0 = A * ((A + 1) + (A - 1) * c + 2 * math.sqrt(A) * alpha)
    b1 = -2 * A * ((A - 1) + (A + 1) * c)
    b2 = A * ((A + 1) + (A - 1) * c - 2 * math.sqrt(A) * alpha)
    a0 = (A + 1) - (A - 1) * c + 2 * math.sqrt(A) * alpha
    a1 = 2 * ((A - 1) - (A + 1) * c)
    a2 = (A + 1) - (A - 1) * c - 2 * math.sqrt(A) * alpha
    return (b0 / a0, b1 / a0, b2 / a0), (1.0, a1 / a0, a2 / a0)


def _biquad_high_pass(fs, fc=38.0, q=0.5):
    w0 = 2 * math.pi * fc / fs
    alpha = math.sin(w0) / (2 * q)
    c = math.cos(w0)
    b0 = (1 + c) / 2
    b1 = -(1 + c)
    b2 = (1 + c) / 2
    a0 = 1 + alpha
    return (b0 / a0, b1 / a0, b2 / a0), (1.0, -2 * c / a0, (1 - alpha) / a0)


def k_weighting(fs):
    return [_biquad_high_shelf(fs), _biquad_high_pass(fs)]


def channel_weights(n):
    if n <= 2:
        return np.ones(n)
    if n == 6:                      # L R C LFE Ls Rs
        return np.array([1, 1, 1, 0, 1.41, 1.41])
    return np.ones(n)


def gated_loudness(filtered, fs):
    """Integrated loudness from K-weighted samples, shape (n, channels)."""
    n, ch = filtered.shape
    blk = int(round(BLOCK * fs))
    hop = int(round(BLOCK * (1 - OVERLAP) * fs))
    if n < blk:
        blk = n
        hop = max(1, n)
    if blk == 0:
        return float("-inf")
    sq = filtered.astype(np.float64) ** 2
    cs = np.vstack([np.zeros((1, ch)), np.cumsum(sq, axis=0)])
    starts = np.arange(0, n - blk + 1, hop)
    z = (cs[starts + blk] - cs[starts]) / blk                      # mean square per block/channel
    G = channel_weights(ch)
    power = z @ G
    with np.errstate(divide="ignore"):
        lk = -0.691 + 10 * np.log10(power)
    keep = lk > ABS_GATE
    if not np.any(keep):
        return float("-inf")
    rel = -0.691 + 10 * math.log10(float(np.mean(power[keep]))) + REL_GATE
    keep &= lk > rel
    if not np.any(keep):
        return float("-inf")
    return -0.691 + 10 * math.log10(float(np.mean(power[keep])))


def sample_peak_db(data):
    p = float(np.max(np.abs(data))) if data.size else 0.0
    return 20 * math.log10(p) if p > 0 else float("-inf")


def _as2d(a):
    a = np.asarray(a, dtype=np.float32)
    return a[:, None] if a.ndim == 1 else a


def measure_sound(sound, fs):
    """Measure an ``aud.Sound``. Returns (integrated LUFS, sample peak dBFS).

    Decodes twice (raw for the peak, filtered for loudness) so only one full
    array is in memory at a time.
    """
    raw = _as2d(sound.data())
    peak = sample_peak_db(raw)
    del raw
    s = sound
    for b, a in k_weighting(fs):
        s = s.filter(b, a)
    filtered = _as2d(s.data())
    return gated_loudness(filtered, fs), peak


def measure_array(data, fs):
    """Measure a NumPy array (n, channels) or (n,)."""
    import aud
    data = _as2d(data)
    snd = aud.Sound.buffer(np.ascontiguousarray(data), fs)
    return measure_sound(snd, fs)


def measure_file(path, start_sec=None, end_sec=None):
    import aud
    snd = aud.Sound(path)
    fs = int(snd.specs[0])
    if start_sec is not None or end_sec is not None:
        start = max(0.0, start_sec or 0.0)
        snd = snd.limit(start, end_sec if end_sec is not None else -1)
    return measure_sound(snd, fs)


def fmt_lufs(v):
    return "-inf" if v == float("-inf") else f"{v:.1f}"
