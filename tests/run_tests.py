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


# ------------------------------------------------------------------- v0.2: export presets
def _image(path, w, h):
    img = bpy.data.images.new(os.path.basename(path), w, h)
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    return path


def _video_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.name = "Edit"
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
    sed = scene.sequence_editor_create()
    path = _image(os.path.join(TMP, "frame169.png"), 1920, 1080)
    compat.strips_top(sed).new_image("Shot", path, channel=1, frame_start=1)
    scene.frame_start, scene.frame_end = 1, 250
    return scene, sed


def test_export_preset_current_scene():
    from vse_creator_toolkit import export
    scene, sed = _video_scene()
    st = scene.vsect_export
    st.preset, st.target, st.footage = 'YT_1080', 'CURRENT', 'KEEP'
    assert bpy.ops.vsect.export_apply() == {'FINISHED'}
    r = scene.render
    assert (r.resolution_x, r.resolution_y, r.resolution_percentage) == (1920, 1080, 100)
    assert r.image_settings.file_format == 'FFMPEG'
    f = r.ffmpeg
    assert (f.format, f.codec, f.audio_codec, f.audio_mixrate) == ('MPEG4', 'H264', 'AAC', 48000)
    assert f.gopsize == round(FPS / 2) and f.audio_bitrate == 320
    assert r.filepath.startswith("//renders/") and "youtube_1080p" in r.filepath
    assert export.aspect_label(1080, 1920) == "9:16"
    assert scene.view_settings.view_transform == 'Standard'


def test_export_variant_fill_keeps_original():
    scene, sed = _video_scene()
    st = scene.vsect_export
    st.preset, st.target, st.footage = 'SHORTS', 'VARIANT', 'FILL'
    assert bpy.ops.vsect.export_apply() == {'FINISHED'}
    assert len(bpy.data.scenes) == 2
    orig = bpy.data.scenes["Edit"]
    var = [s for s in bpy.data.scenes if s != orig][0]
    assert "YouTube Shorts" in var.name
    # original untouched
    assert (orig.render.resolution_x, orig.render.resolution_y) == (1920, 1080)
    assert abs(compat.strips_top(orig.sequence_editor)["Shot"].transform.scale_x - 1.0) < 1e-6
    # variant is vertical and the 16:9 shot fills the height (1920/1080)
    assert (var.render.resolution_x, var.render.resolution_y) == (1080, 1920)
    shot = compat.strips_top(var.sequence_editor)["Shot"]
    assert abs(shot.transform.scale_x - 1920 / 1080) < 1e-4, shot.transform.scale_x


def test_export_fit_letterbox():
    from vse_creator_toolkit import export
    scene, sed = _video_scene()
    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920
    done, skipped = export.refit_strips(scene, 'FIT')
    assert done == 1 and skipped == 0
    assert abs(compat.strips_top(sed)["Shot"].transform.scale_x - 1080 / 1920) < 1e-4


# ------------------------------------------------------------------- v0.2: chapters
def _chapter_scene(marks, frames=25 * 120):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    scene.frame_start, scene.frame_end = 1, frames
    for name, sec in marks:
        scene.timeline_markers.new(name, frame=1 + int(sec * FPS))
    return scene


def test_chapters_valid():
    from vse_creator_toolkit import chapters
    scene = _chapter_scene([("Hook", 0), ("Modelling", 15), ("Materials", 63.5), ("Render", 95)])
    chs, problems = chapters.collect(scene)
    assert problems == [], problems
    text = chapters.build_text(chs, 120)
    assert text == "0:00 Hook\n0:15 Modelling\n1:03 Materials\n1:35 Render", text
    assert bpy.ops.vsect.chapters_copy() == {'FINISHED'}
    assert bpy.context.window_manager.clipboard == text or bpy.app.background
    assert bpy.data.texts[chapters.TEXT_NAME].as_string().strip() == text


def test_chapters_auto_intro_and_rules():
    from vse_creator_toolkit import chapters
    # no marker at 0, a too-short chapter, silence markers and default names ignored/renamed
    scene = _chapter_scene([("Build", 20), ("F_550", 22), ("S1 ▸", 40), ("◂ S1", 41)])
    chs, problems = chapters.collect(scene)
    titles = [t for _, t in chs]
    assert titles == ["Intro", "Build", "Chapter 2"], titles
    assert any("at least 10s" in p for p in problems), problems
    scene2 = _chapter_scene([("Only", 30)])
    _, p2 = chapters.collect(scene2)
    assert any("at least 3 chapters" in p for p in p2), p2


def test_chapters_hours_format():
    from vse_creator_toolkit import chapters
    assert chapters.format_time(3725, with_hours=True) == "1:02:05"
    assert chapters.format_time(59.9) == "0:59"


# ------------------------------------------------------------------- v0.3: loudness
def _sine_wav(path, dbfs, seconds, rate=48000, freq=1000.0, burst_dbfs=None):
    t = np.arange(int(seconds * rate)) / rate
    x = 10 ** (dbfs / 20) * np.sin(2 * math.pi * freq * t)
    if burst_dbfs is not None:                       # short loud click near the middle
        mid = len(x) // 2
        x[mid:mid + rate // 100] = 10 ** (burst_dbfs / 20)
    pcm = (np.stack([x, x], 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


def _sound_scene(specs):
    """specs: list of (name, path, channel, select)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    scene.render.ffmpeg.audio_mixrate = 48000
    sed = scene.sequence_editor_create()
    end = 1
    for name, path, ch, sel in specs:
        s = compat.strips_top(sed).new_sound(name, path, channel=ch, frame_start=1)
        s.select = sel
        end = max(end, s.frame_final_end)
    scene.frame_start, scene.frame_end = 1, end - 1
    return scene, sed


def test_loudness_ebu_reference():
    from vse_creator_toolkit import loudness
    for fs, db in ((48000, -23.0), (44100, -33.0)):
        t = np.arange(fs * 20) / fs
        x = np.stack([10 ** (db / 20) * np.sin(2 * math.pi * 1000 * t)] * 2, 1).astype(np.float32)
        lufs, peak = loudness.measure_array(x, fs)
        assert abs(lufs - db) <= 0.1, (fs, lufs)          # EBU Tech 3341 tolerance
        assert abs(peak - db) <= 0.05, peak


def test_loudness_gating_ignores_silence():
    from vse_creator_toolkit import loudness
    fs = 48000
    tone = 10 ** (-23 / 20) * np.sin(2 * math.pi * 1000 * np.arange(fs * 10) / fs)
    x = np.concatenate([tone, np.zeros(fs * 10)])
    lufs, _ = loudness.measure_array(np.stack([x, x], 1).astype(np.float32), fs)
    assert abs(lufs + 23) <= 0.2, lufs


def test_normalize_mix():
    p = _sine_wav(os.path.join(TMP, "quiet.wav"), -30, 8)
    scene, sed = _sound_scene([("Quiet", p, 1, True)])
    scene.vsect_audio.target = '-14'
    assert bpy.ops.vsect.loudness_normalize() == {'FINISHED'}
    assert abs(scene.audio_volume - 10 ** (16 / 20)) < 0.1, scene.audio_volume
    from vse_creator_toolkit import audio_tools
    lufs, peak = audio_tools.measure_mix(scene)
    assert abs(lufs + 14) <= 0.3, lufs


def test_normalize_peak_safe():
    from vse_creator_toolkit import audio_tools
    gain, limited = audio_tools.normalize_gain_db(-30.0, -3.0, -14.0, peak_safe=True)
    assert limited and abs(gain - 2.0) < 1e-6, gain
    gain, limited = audio_tools.normalize_gain_db(-30.0, -3.0, -14.0, peak_safe=False)
    assert not limited and abs(gain - 16.0) < 1e-6
    p = _sine_wav(os.path.join(TMP, "spiky.wav"), -30, 8, burst_dbfs=-3)
    scene, sed = _sound_scene([("Spiky", p, 1, True)])
    bpy.ops.vsect.loudness_normalize()
    lufs, peak = audio_tools.measure_mix(scene)
    assert peak <= -0.9, peak


def test_match_clips():
    a = _sine_wav(os.path.join(TMP, "a.wav"), -30, 5)
    b = _sine_wav(os.path.join(TMP, "b.wav"), -20, 5)
    scene, sed = _sound_scene([("A", a, 1, True), ("B", b, 2, True)])
    scene.vsect_audio.clip_target = -16.0
    assert bpy.ops.vsect.loudness_match() == {'FINISHED'}
    top = compat.strips_top(sed)
    assert abs(top["A"].volume - 10 ** (14 / 20)) < 0.05, top["A"].volume
    assert abs(top["B"].volume - 10 ** (4 / 20)) < 0.02, top["B"].volume


# ------------------------------------------------------------------- v0.3: ducking
def _vol_at(scene, strip, frame):
    fc = compat.find_fcurve(scene, strip.path_from_id("volume"))
    return fc.evaluate(frame) if fc else strip.volume


def test_ducking():
    voice = os.path.join(TMP, "duck_voice.wav")
    write_wav(voice, PATTERN)
    music = _sine_wav(os.path.join(TMP, "duck_music.wav"), -20, 7.8, freq=440)
    scene, sed = _sound_scene([("Voice", voice, 1, True), ("Music", music, 2, False)])
    mus = compat.strips_top(sed)["Music"]
    mus.volume = 0.8
    st = scene.vsect_audio
    st.duck_db, st.attack, st.release, st.hold_gap = 12.0, 0.25, 0.6, 0.8
    assert bpy.ops.vsect.audio_duck() == {'FINISHED'}
    low = 0.8 * 10 ** (-12 / 20)
    f = lambda sec: 1 + sec * FPS
    assert abs(_vol_at(scene, mus, f(0.5)) - low) < 0.01         # speaking
    assert abs(_vol_at(scene, mus, f(3.65)) - low) < 0.01        # 0.3 s pause stays ducked
    assert abs(_vol_at(scene, mus, f(5.5)) - 0.8) < 0.01         # 2 s pause: back up
    assert abs(_vol_at(scene, mus, f(7.3)) - low) < 0.01         # last sentence
    assert abs(compat.strips_top(sed)["Voice"].volume - 1.0) < 1e-6   # voice untouched

    # running again must not compound the dip
    bpy.ops.vsect.audio_duck()
    assert abs(_vol_at(scene, mus, f(0.5)) - low) < 0.01
    assert abs(_vol_at(scene, mus, f(5.5)) - 0.8) < 0.01

    assert bpy.ops.vsect.audio_unduck() == {'FINISHED'}
    assert compat.find_fcurve(scene, mus.path_from_id("volume")) is None
    assert abs(mus.volume - 0.8) < 1e-6


def test_ducking_channel_mode():
    voice = os.path.join(TMP, "duck_voice2.wav")
    write_wav(voice, PATTERN)
    music = _sine_wav(os.path.join(TMP, "duck_music2.wav"), -20, 7.8, freq=440)
    sfx = _sine_wav(os.path.join(TMP, "sfx.wav"), -20, 7.8, freq=880)
    scene, sed = _sound_scene([("Voice", voice, 1, True), ("Music", music, 3, False), ("SFX", sfx, 4, False)])
    st = scene.vsect_audio
    st.music_source, st.music_channel = 'CHANNEL', 3
    bpy.ops.vsect.audio_duck()
    top = compat.strips_top(sed)
    assert compat.find_fcurve(scene, top["Music"].path_from_id("volume")) is not None
    assert compat.find_fcurve(scene, top["SFX"].path_from_id("volume")) is None


# ------------------------------------------------------------------- v0.4: captions
WORDS = [  # a fake transcript (seconds)
    ("Welcome", 0.0, 0.4), ("back", 0.45, 0.7), ("to", 0.72, 0.8), ("the", 0.82, 0.9), ("channel.", 0.92, 1.4),
    ("Today", 2.2, 2.5), ("we", 2.55, 2.65), ("build", 2.7, 3.0), ("a", 3.02, 3.08), ("low", 3.1, 3.3),
    ("poly", 3.32, 3.6), ("island", 3.62, 4.0), ("in", 4.05, 4.15), ("Blender!", 4.2, 4.8),
]


def _words():
    return [{"text": t, "start": a, "end": b} for t, a, b in WORDS]


def _caption_scene(w=1920, h=1080):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = FPS, 1.0
    scene.render.resolution_x, scene.render.resolution_y = w, h
    scene.sequence_editor_create()
    scene.frame_start, scene.frame_end = 1, 200
    return scene


def test_caption_grouping():
    from vse_creator_toolkit import captions
    caps = captions.group_words(_words(), max_chars=32, max_duration=3.0)
    texts = [c["text"] for c in caps]
    assert texts[0] == "Welcome back to the channel.", texts       # sentence end breaks
    assert all(len(t) <= 32 for t in texts), texts
    assert texts[-1].endswith("Blender!")
    for a, b in zip(caps, caps[1:]):
        assert a["end"] <= b["start"] + 1e-9, "captions overlap"
    narrow = captions.group_words(_words(), max_chars=12)
    assert all(len(c["text"]) <= 12 or len(c["words"]) == 1 for c in narrow)
    pops = captions.group_words(_words(), one_word=True)
    assert len(pops) == len(WORDS)


def test_srt_roundtrip_and_parse():
    from vse_creator_toolkit import captions
    caps = captions.group_words(_words())
    text = captions.write_srt(caps)
    assert text.startswith("1\n00:00:00,000 --> 00:00:01,")
    back = captions.parse_srt(text)
    assert [c["text"] for c in back] == [c["text"] for c in caps]
    assert all(abs(a["start"] - b["start"]) < 0.002 for a, b in zip(back, caps))
    messy = "﻿1\r\n00:01:02.5 --> 00:01:04,250\r\n<i>Hello</i>\r\nthere\r\n\r\n\r\n2\r\n00:01:05,000 --> 00:01:06,000\r\nBye\r\n"
    segs = captions.parse_srt(messy)
    assert segs[0]["text"] == "Hello there" and abs(segs[0]["start"] - 62.5) < 1e-6 and abs(segs[0]["end"] - 64.25) < 1e-6
    assert len(segs) == 2
    words = captions.segments_to_words(segs)
    assert words[0]["start"] == 62.5 and abs(words[1]["end"] - 64.25) < 1e-6


def test_caption_strips_lines():
    from vse_creator_toolkit import caption_strips, captions_ui
    scene = _caption_scene()
    st = scene.vsect_captions
    st.mode = 'LINES'
    strips, caps = captions_ui.build_from_words(scene, _words(), st)
    assert len(strips) == len(caps)
    s0 = strips[0]
    assert s0.type == 'TEXT' and s0.text == "Welcome back to the channel."
    assert (s0.frame_final_start, s0.frame_final_end) == (0, round(1.4 * FPS)), \
        (s0.frame_final_start, s0.frame_final_end)
    assert s0.font_size == round(1080 * st.size)
    assert abs(s0.location[1] - st.position_y) < 1e-6 and s0.align_x == 'CENTER'
    if hasattr(s0, "use_outline"):
        assert s0.use_outline


def test_caption_strips_highlight_positions():
    from vse_creator_toolkit import caption_strips, captions_ui
    scene = _caption_scene()
    st = scene.vsect_captions
    st.mode = 'HIGHLIGHT'
    strips, caps = captions_ui.build_from_words(scene, _words(), st)
    lines = [s for s in strips if s["vsect_caption"] == 'line']
    words = [s for s in strips if s["vsect_caption"] == 'word']
    assert len(lines) == len(caps) and len(words) == len(WORDS)
    assert len({s.channel for s in words}) == 1 and words[0].channel == lines[0].channel + 1
    assert all(s.font is not None and s.font == lines[0].font for s in strips), "captions need one real font"
    first_line_words = [w for w in words if w["vsect_cap"] == 0]
    xs = [w.location[0] for w in first_line_words]
    assert xs == sorted(xs) and xs[0] < 0.5 < xs[-1] + 0.1, xs
    # word timing: each word strip ends where the next word starts, inside its line
    for w in first_line_words:
        assert lines[0].frame_final_start <= w.frame_final_start < w.frame_final_end <= lines[0].frame_final_end
    # restyle keeps positions consistent when the size changes
    st.size = 0.08
    caption_strips.restyle(scene, st)
    xs2 = [w.location[0] for w in first_line_words]
    assert xs2[0] < xs[0], "bigger text should start further left"


def test_caption_strips_pop_and_clear():
    from vse_creator_toolkit import caption_strips, captions_ui
    scene = _caption_scene(1080, 1920)
    st = scene.vsect_captions
    st.mode, st.uppercase = 'POP', True
    strips, caps = captions_ui.build_from_words(scene, _words(), st)
    assert len(strips) == len(WORDS) and strips[0].text == "WELCOME"
    assert captions_ui.effective_max_chars(scene, st) == 18
    # rebuild replaces instead of duplicating
    captions_ui.save_transcript(_words())
    assert bpy.ops.vsect.captions_rebuild() == {'FINISHED'}
    assert len(caption_strips.caption_strips(scene)) == len(WORDS)
    assert bpy.ops.vsect.captions_clear() == {'FINISHED'}
    assert caption_strips.caption_strips(scene) == []


def test_srt_import_export_edit_ops():
    from vse_creator_toolkit import caption_strips, captions
    scene = _caption_scene()
    st = scene.vsect_captions
    st.mode = 'LINES'
    src = os.path.join(TMP, "in.srt")
    with open(src, "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:03,000\nFirst caption here\n\n2\n00:00:04,000 --> 00:00:05,500\nSecond one\n")
    assert bpy.ops.vsect.captions_import_srt(filepath=src) == {'FINISHED'}
    caps = caption_strips.read_back(scene)
    assert [c["text"] for c in caps] == ["First caption here", "Second one"], caps
    assert abs(caps[0]["start"] - 1.0) < 1 / FPS + 1e-6

    out = os.path.join(TMP, "out.srt")
    assert bpy.ops.vsect.captions_export_srt(filepath=out) == {'FINISHED'}
    segs = captions.parse_srt(open(out, encoding="utf-8").read())
    assert [s["text"] for s in segs] == ["First caption here", "Second one"]

    # edit as text, change a word, apply
    bpy.ops.vsect.captions_edit_text()
    tb = bpy.data.texts["Captions.srt"]
    txt = tb.as_string().replace("Second one", "Second line fixed")
    tb.clear()
    tb.write(txt)
    assert bpy.ops.vsect.captions_apply_text() == {'FINISHED'}
    assert [c["text"] for c in caption_strips.read_back(scene)][1] == "Second line fixed"


def test_whisper_missing_is_graceful():
    from vse_creator_toolkit import transcribe
    assert isinstance(transcribe.available(), bool)
    assert os.path.isdir(transcribe.deps_dir()) and os.path.isdir(transcribe.models_dir())


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
