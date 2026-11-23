# SPDX-License-Identifier: GPL-3.0-or-later
"""Audio loading and silence detection.

Uses Blender's bundled ``aud`` (Audaspace) to decode any format Blender can play,
including the audio track of video files, and NumPy for the analysis. Audio is
down-mixed to mono and resampled to 16 kHz first, which keeps memory low: a
10-minute recording is about 38 MB.
"""
import os
import tempfile

import bpy
import numpy as np

ANALYSIS_RATE = 16000
WINDOW_SEC = 0.01          # 10 ms analysis windows
DB_FLOOR = -100.0


class AudioLoadError(Exception):
    pass


def sound_strip_path(strip):
    """Return a readable file path for a sound strip, unpacking packed audio to a temp file."""
    sound = strip.sound
    if sound is None:
        raise AudioLoadError("Strip has no sound data.")
    packed = getattr(sound, "packed_file", None)
    if packed is not None:
        ext = os.path.splitext(sound.filepath)[1] or ".wav"
        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="vsect_")
        with os.fdopen(fd, "wb") as f:
            f.write(packed.data)
        return tmp, True
    path = bpy.path.abspath(sound.filepath, library=sound.library)
    if not os.path.isfile(path):
        raise AudioLoadError(f"Audio file not found: {path}")
    return path, False


def load_mono(path, rate=ANALYSIS_RATE):
    """Decode ``path`` to a mono float32 array. Returns (samples, sample_rate)."""
    import aud
    snd = aud.Sound(path)
    try:
        snd = snd.rechannel(1)
    except Exception:
        pass
    out_rate = None
    try:
        try:
            snd = snd.resample(rate, 1)      # 4.x: (rate, quality) / 3.6: (rate, high_quality)
        except TypeError:
            snd = snd.resample(rate)
        out_rate = rate
    except Exception:
        out_rate = None
    data = snd.data()
    if data is None or len(data) == 0:
        raise AudioLoadError("Could not decode any audio samples.")
    data = np.asarray(data, dtype=np.float32)
    if data.ndim == 2:
        data = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
    if out_rate is None:
        out_rate = int(snd.specs[0])
    return data, out_rate


def envelope_db(samples, rate, window_sec=WINDOW_SEC):
    """RMS loudness per window in dBFS."""
    win = max(1, int(rate * window_sec))
    n = len(samples) // win
    if n == 0:
        return np.array([DB_FLOOR], dtype=np.float32), win / rate
    frames = samples[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-10))
    return np.maximum(db, DB_FLOOR).astype(np.float32), win / rate


def auto_threshold(db, sensitivity=0.35):
    """Pick a threshold between the noise floor and the speech level.

    sensitivity 0 = right at the noise floor (cuts only dead silence),
    sensitivity 1 = at speech level (cuts aggressively).
    """
    floor = float(np.percentile(db, 10))
    speech = float(np.percentile(db, 95))
    if speech - floor < 6.0:          # almost no dynamic range: probably no real pauses
        return floor - 1.0
    return floor + (speech - floor) * sensitivity


def find_silences(db, step_sec, threshold_db, min_silence_sec, padding_sec,
                  keep_edges=False):
    """Return a list of (start_sec, end_sec) silent ranges, padding already applied.

    ``padding_sec`` of audio is kept next to speech on both sides of every cut so
    words aren't clipped. With ``keep_edges`` the silence before the first and
    after the last sound is left alone.
    """
    silent = db < threshold_db
    total = len(db) * step_sec
    ranges = []
    i, n = 0, len(silent)
    while i < n:
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < n and silent[j]:
            j += 1
        start, end = i * step_sec, j * step_sec
        at_start, at_end = i == 0, j == n
        if keep_edges and (at_start or at_end):
            i = j
            continue
        s = start if at_start else start + padding_sec
        e = end if at_end else end - padding_sec
        if (end - start) >= min_silence_sec and e - s > 0:
            ranges.append((s, min(e, total)))
        i = j
    return ranges


def analyze_strip(strip, threshold_mode, threshold_db, sensitivity,
                  min_silence_sec, padding_sec, keep_edges):
    """Analyze a sound strip. Returns dict with ranges in *source seconds* and stats."""
    path, is_temp = sound_strip_path(strip)
    try:
        samples, rate = load_mono(path)
    finally:
        if is_temp:
            try:
                os.remove(path)
            except OSError:
                pass
    db, step = envelope_db(samples, rate)
    thr = auto_threshold(db, sensitivity) if threshold_mode == 'AUTO' else threshold_db
    ranges = find_silences(db, step, thr, min_silence_sec, padding_sec, keep_edges)
    return {
        "ranges": ranges,
        "threshold": thr,
        "noise_floor": float(np.percentile(db, 10)),
        "speech_level": float(np.percentile(db, 95)),
        "duration": len(samples) / rate,
    }


def source_ranges_to_frames(strip, ranges, fps, min_frames=2):
    """Map source-time ranges to whole timeline frames, clipped to the visible strip."""
    out = []
    lo, hi = strip.frame_final_start, strip.frame_final_end
    for s, e in ranges:
        a = int(round(strip.frame_start + s * fps))
        b = int(round(strip.frame_start + e * fps))
        a, b = max(a, lo), min(b, hi)
        if b - a >= min_frames:
            out.append((a, b))
    # merge overlaps created by rounding
    merged = []
    for a, b in sorted(out):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged
