# SPDX-License-Identifier: GPL-3.0-or-later
"""Headless test suite. Run with:

    blender -b --factory-startup --python tests/run_tests.py

Exits with code 0 when all tests pass, 1 otherwise.
"""
import math
import os
import sys
import tempfile
import traceback
import wave

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import vse_creator_toolkit as addon  # noqa: E402
from vse_creator_toolkit import audio_analysis, compat  # noqa: E402

FPS = 25
TMP = tempfile.mkdtemp(prefix="vsect_test_")

# Voice track layout (seconds): tone/silence pattern with a short pause that must survive.
PATTERN = [("tone", 1.0), ("sil", 1.0), ("tone", 1.5), ("sil", 0.3),
           ("tone", 1.0), ("sil", 2.0), ("tone", 1.0)]


def write_wav(path, pattern, rate=44100, noise_db=-65.0, channels=2):
    rng = np.random.default_rng(7)
    parts = []
    t0 = 0.0
    for kind, dur in pattern:
        n = int(round(dur * rate))
        t = (np.arange(n) / rate) + t0
        if kind == "tone":
            # speech-like: 220 Hz tone with a slow amplitude wobble
            sig = 0.4 * np.sin(2 * math.pi * 220 * t) * (0.75 + 0.25 * np.sin(2 * math.pi * 3 * t))
        else:
            sig = np.zeros(n)
        sig = sig + rng.normal(0, 10 ** (noise_db / 20), n)   # room noise floor
        parts.append(sig)
        t0 += dur
    mono = np.clip(np.concatenate(parts), -1, 1)
    data = np.repeat(mono[:, None], channels, axis=1)
    pcm = (data * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return sum(d for _, d in pattern)


def fresh_scene(wav, music=None):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    sed = scene.sequence_editor_create()
    top = compat.strips_top(sed)
    voice = top.new_sound("Voice", wav, channel=1, frame_start=1)
    video = top.new_effect("Video", 'COLOR', channel=2, frame_start=1, frame_end=voice.frame_final_end)
    if music:
        mus = top.new_sound("Music", music, channel=3, frame_start=1)
        mus.select = False
    for s in compat.strips_all(sed):
        s.select = s.name in ("Voice", "Video")
    sed.active_strip = voice
    scene.frame_start, scene.frame_end = 1, voice.frame_final_end - 1
    return scene, sed


def pieces(sed, channel):
    return sorted((s for s in compat.strips_top(sed) if s.channel == channel),
                  key=lambda s: s.frame_final_start)


def assert_contiguous(ps, label):
    for a, b in zip(ps, ps[1:]):
        assert a.frame_final_end == b.frame_final_start, f"{label}: gap/overlap between {a.name} and {b.name}"


# ------------------------------------------------------------------- tests
def test_find_silences_unit():
    step = 0.01
    db = np.array([-20] * 100 + [-70] * 100 + [-20] * 50 + [-70] * 20 + [-20] * 50, dtype=np.float32)
    r = audio_analysis.find_silences(db, step, -40, 0.5, 0.1)
    assert len(r) == 1, r
    s, e = r[0]
    assert abs(s - 1.1) < 1e-6 and abs(e - 1.9) < 1e-6, r


def test_edges():
    step = 0.01
    db = np.array([-70] * 80 + [-20] * 100 + [-70] * 80, dtype=np.float32)
    assert len(audio_analysis.find_silences(db, step, -40, 0.5, 0.1)) == 2
    assert len(audio_analysis.find_silences(db, step, -40, 0.5, 0.1, keep_edges=True)) == 0


def test_detect_and_cut_ripple():
    wav = os.path.join(TMP, "voice.wav")
    write_wav(wav, PATTERN)
    music = os.path.join(TMP, "music.wav")
    write_wav(music, [("tone", 7.8)])
    scene, sed = fresh_scene(wav, music)
    voice_len = sed.active_strip.frame_final_duration
    music_before = pieces(sed, 3)[0]
    m_start, m_end = music_before.frame_final_start, music_before.frame_final_end

    st = scene.vsect_silence
    st.min_silence, st.padding = 0.5, 0.1
    assert bpy.ops.vsect.silence_detect() == {'FINISHED'}
    ranges = [(r.start, r.end) for r in st.ranges]
    assert len(ranges) == 2, f"expected 2 pauses, got {ranges}"
    # expected: (1.1s..1.9s) and (3.9+0.1 .. 6.0-0.1)s -> frames relative to start 1
    exp = [(1 + 1.1 * FPS, 1 + 1.9 * FPS), (1 + 4.9 * FPS, 1 + 6.7 * FPS)]
    for (a, b), (ea, eb) in zip(ranges, exp):
        assert abs(a - ea) <= 2 and abs(b - eb) <= 2, f"range {a,b} vs expected {ea,eb}"
    assert st.channels == "1,2", st.channels

    removed = sum(b - a for a, b in ranges)
    assert bpy.ops.vsect.silence_cut('EXEC_DEFAULT') == {'FINISHED'}

    v, c = pieces(sed, 1), pieces(sed, 2)
    assert len(v) == 3 and len(c) == 3, f"voice {len(v)} pieces, video {len(c)} pieces"
    assert_contiguous(v, "voice")
    assert_contiguous(c, "video")
    assert sum(p.frame_final_duration for p in v) == voice_len - removed
    assert v[-1].frame_final_end == c[-1].frame_final_end
    # each voice piece must stay in sync with its video piece
    for pv, pc in zip(v, c):
        assert (pv.frame_final_start, pv.frame_final_end) == (pc.frame_final_start, pc.frame_final_end)
    # untouched music channel
    mus = pieces(sed, 3)
    assert len(mus) == 1 and (mus[0].frame_final_start, mus[0].frame_final_end) == (m_start, m_end)
    assert len(st.ranges) == 0


def test_audio_in_sync_after_cut():
    """The audio heard at each piece start must be speech, not the removed silence."""
    wav = os.path.join(TMP, "voice2.wav")
    write_wav(wav, PATTERN)
    scene, sed = fresh_scene(wav)
    bpy.ops.vsect.silence_detect()
    bpy.ops.vsect.silence_cut('EXEC_DEFAULT')
    v = pieces(sed, 1)
    for p in v[1:]:
        # source second played at the piece start
        src = (p.frame_final_start - p.frame_start) / FPS
        # it must lie within padding of a tone section start
        t, ok = 0.0, False
        for kind, dur in PATTERN:
            if kind == "tone" and abs(src - (t - 0.12)) < 0.1:
                ok = True
            t += dur
        assert ok, f"piece {p.name} starts at source {src:.2f}s, not right before speech"


def test_no_ripple_keeps_gaps():
    wav = os.path.join(TMP, "voice3.wav")
    write_wav(wav, PATTERN)
    scene, sed = fresh_scene(wav)
    end_before = sed.active_strip.frame_final_end
    st = scene.vsect_silence
    st.ripple = False
    bpy.ops.vsect.silence_detect()
    gaps = [(r.start, r.end) for r in st.ranges]
    bpy.ops.vsect.silence_cut('EXEC_DEFAULT')
    v = pieces(sed, 1)
    assert v[-1].frame_final_end == end_before
    for (a, b), (p, q) in zip(gaps, zip(v, v[1:])):
        assert p.frame_final_end == a and q.frame_final_start == b


def test_packed_sound():
    wav = os.path.join(TMP, "voice4.wav")
    write_wav(wav, PATTERN, channels=1, rate=48000)
    scene, sed = fresh_scene(wav)
    sed.active_strip.sound.pack()
    os.remove(wav)                     # analysis must work from the packed data alone
    assert bpy.ops.vsect.silence_detect() == {'FINISHED'}
    assert len(scene.vsect_silence.ranges) == 2


def test_markers():
    wav = os.path.join(TMP, "voice5.wav")
    write_wav(wav, PATTERN)
    scene, sed = fresh_scene(wav)
    bpy.ops.vsect.silence_detect()
    bpy.ops.vsect.silence_markers()
    assert len(scene.timeline_markers) == 4
    bpy.ops.vsect.silence_clear_markers()
    assert len(scene.timeline_markers) == 0


def test_locked_channel_blocks_cut():
    wav = os.path.join(TMP, "voice6.wav")
    write_wav(wav, PATTERN)
    scene, sed = fresh_scene(wav)
    bpy.ops.vsect.silence_detect()
    if hasattr(sed, "channels"):
        sed.channels[2].lock = True
    else:
        for s in pieces(sed, 2):
            s.lock = True
    count = len(compat.strips_top(sed))
    try:
        bpy.ops.vsect.silence_cut('EXEC_DEFAULT')
    except RuntimeError:
        pass                            # operator reports ERROR -> RuntimeError in background
    assert len(compat.strips_top(sed)) == count, "locked channel was modified"


def main():
    addon.register()
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    print(f"\nVSE Creator Toolkit tests on Blender {bpy.app.version_string}")
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.stdout.flush()
    os._exit(1 if failed else 0)


main()
