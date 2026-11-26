# SPDX-License-Identifier: GPL-3.0-or-later
"""Audio tools: Auto Ducking and Loudness (measure, normalise mix, match clips)."""
import os
import tempfile

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty

from . import audio_analysis, compat, loudness

BASE_KEY = "vsect_base_volume"
PEAK_CEILING = -1.0

TARGETS = [
    ('-14', "YouTube / Spotify (−14 LUFS)", "Streaming platforms normalise to about −14 LUFS"),
    ('-16', "Podcast / Apple (−16 LUFS)", "Podcasts and Apple Music"),
    ('-23', "Broadcast EBU R128 (−23 LUFS)", "European TV broadcast"),
    ('-24', "Broadcast ATSC A/85 (−24 LKFS)", "US TV broadcast"),
    ('CUSTOM', "Custom", "Choose your own target"),
]


def _db_to_gain(db):
    return 10 ** (db / 20)


# --------------------------------------------------------------------------- settings
class VSECT_AudioSettings(bpy.types.PropertyGroup):
    # ducking
    duck_db: FloatProperty(name="Duck Amount", default=12.0, min=1.0, max=40.0,
                           description="How many dB the music drops while someone speaks")
    attack: FloatProperty(name="Fade Down", default=0.25, min=0.0, max=3.0, subtype='TIME_ABSOLUTE',
                          unit='TIME_ABSOLUTE', description="Fade-down time, starting before the speech")
    release: FloatProperty(name="Fade Up", default=0.6, min=0.0, max=5.0, subtype='TIME_ABSOLUTE',
                           unit='TIME_ABSOLUTE', description="Fade-up time after the speech ends")
    hold_gap: FloatProperty(name="Stay Down For Pauses Under", default=0.8, min=0.0, max=5.0,
                            subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                            description="Short pauses between sentences keep the music down (avoids pumping)")
    sensitivity: FloatProperty(name="Voice Sensitivity", default=0.35, min=0.05, max=0.9, subtype='FACTOR',
                               description="Higher treats quieter sounds as speech")
    music_source: EnumProperty(
        name="Music",
        items=[('UNSELECTED', "All Other Sound Strips", "Duck every sound strip that isn't selected"),
               ('CHANNEL', "Channel", "Duck sound strips in one channel")],
        default='UNSELECTED')
    music_channel: IntProperty(name="Music Channel", default=3, min=1, max=128)

    # loudness
    target: EnumProperty(name="Target", items=TARGETS, default='-14')
    custom_target: FloatProperty(name="Custom LUFS", default=-14.0, min=-40.0, max=-5.0)
    clip_target: FloatProperty(name="Clip Level", default=-16.0, min=-40.0, max=-5.0,
                               description="Loudness each selected clip is matched to (LUFS)")
    peak_safe: BoolProperty(name="Prevent Clipping", default=True,
                            description="Never raise the mix so far that peaks go above −1 dBFS")
    measured: FloatProperty(default=0.0)
    measured_peak: FloatProperty(default=0.0)
    has_measurement: BoolProperty(default=False)


def _target_lufs(st):
    return st.custom_target if st.target == 'CUSTOM' else float(st.target)


# --------------------------------------------------------------------------- ducking
def speech_ranges_frames(strip, fps, sensitivity, hold_gap):
    """Timeline frame ranges where the voice strip has sound."""
    path, tmp = audio_analysis.sound_strip_path(strip)
    try:
        samples, rate = audio_analysis.load_mono(path)
    finally:
        if tmp:
            try:
                os.remove(path)
            except OSError:
                pass
    db, step = audio_analysis.envelope_db(samples, rate)
    thr = audio_analysis.auto_threshold(db, sensitivity)
    loud = db >= thr
    ranges, i, n = [], 0, len(loud)
    while i < n:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < n and loud[j]:
            j += 1
        ranges.append([i * step, j * step])
        i = j
    merged = []
    for s, e in ranges:                         # bridge short pauses
        if merged and s - merged[-1][1] < hold_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    merged = [r for r in merged if r[1] - r[0] >= 0.1]   # drop clicks
    out = []
    lo, hi = strip.frame_final_start, strip.frame_final_end
    for s, e in merged:
        a = max(lo, strip.frame_start + s * fps)
        b = min(hi, strip.frame_start + e * fps)
        if b > a:
            out.append((a, b))
    return out


def union(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(b, out[-1][1]))
        else:
            out.append((a, b))
    return out


def clear_volume_animation(scene, strip):
    fc = compat.find_fcurve(scene, strip.path_from_id("volume"))
    if fc is not None:
        compat.remove_fcurve(scene, fc)


def duck_strip(scene, strip, speech, duck_db, attack_f, release_f):
    """Write volume keyframes on ``strip`` that dip during ``speech`` (timeline frames)."""
    base = strip.get(BASE_KEY, strip.volume)
    strip[BASE_KEY] = base
    clear_volume_animation(scene, strip)
    low = base * _db_to_gain(-duck_db)
    lo, hi = strip.frame_final_start, strip.frame_final_end

    # bridge regions whose fades would overlap
    regions = []
    for a, b in speech:
        if b <= lo or a >= hi:
            continue
        if regions and a - regions[-1][1] < attack_f + release_f:
            regions[-1] = (regions[-1][0], b)
        else:
            regions.append((a, b))

    keys = {}
    for a, b in regions:
        if a - attack_f > lo:
            keys[a - attack_f] = base
        keys[max(a, lo)] = low
        keys[min(b, hi)] = low
        if b + release_f < hi:
            keys[b + release_f] = base
    strip.volume = base
    if not keys:
        return 0
    if min(keys) > lo:
        keys[lo] = base
    for f in sorted(keys):
        strip.volume = keys[f]
        strip.keyframe_insert("volume", frame=f)
    strip.volume = base
    fc = compat.find_fcurve(scene, strip.path_from_id("volume"))
    if fc is not None:
        for kp in fc.keyframe_points:
            kp.interpolation = 'LINEAR'
        fc.update()
    return len(regions)


def _music_strips(context, st, voices):
    sed = context.scene.sequence_editor
    voice_ptrs = {v.as_pointer() for v in voices}
    out = []
    for s in compat.strips_top(sed):
        if s.type != 'SOUND' or s.as_pointer() in voice_ptrs:
            continue
        if st.music_source == 'CHANNEL':
            if s.channel == st.music_channel:
                out.append(s)
        elif not s.select:
            out.append(s)
    return out


def _voice_strips(context):
    sed = context.scene.sequence_editor
    if sed is None:
        return []
    return [s for s in compat.strips_top(sed) if s.select and s.type == 'SOUND']


class VSECT_OT_duck(bpy.types.Operator):
    """Lower the music automatically whenever the selected voice strips have speech"""
    bl_idname = "vsect.audio_duck"
    bl_label = "Duck Music Under Voice"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_voice_strips(context))

    def execute(self, context):
        scene = context.scene
        st = scene.vsect_audio
        fps = compat.scene_fps(scene)
        voices = _voice_strips(context)
        music = _music_strips(context, st, voices)
        if not music:
            self.report({'WARNING'}, "No music strips to duck (unselected sound strips, or the chosen channel)")
            return {'CANCELLED'}
        speech = []
        try:
            for v in voices:
                speech += speech_ranges_frames(v, fps, st.sensitivity, st.hold_gap)
        except audio_analysis.AudioLoadError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        speech = union(speech)
        att, rel = round(st.attack * fps), round(st.release * fps)
        regions = sum(duck_strip(scene, m, speech, st.duck_db, att, rel) for m in music)
        self.report({'INFO'}, f"Ducked {len(music)} music strip(s) under {len(speech)} speech section(s)"
                              f" ({regions} dips)")
        return {'FINISHED'}


class VSECT_OT_unduck(bpy.types.Operator):
    """Remove ducking from the music strips and restore their original volume"""
    bl_idname = "vsect.audio_unduck"
    bl_label = "Remove Ducking"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        sed = scene.sequence_editor
        n = 0
        for s in compat.strips_all(sed) if sed else ():
            if s.type == 'SOUND' and BASE_KEY in s:
                clear_volume_animation(scene, s)
                s.volume = s[BASE_KEY]
                del s[BASE_KEY]
                n += 1
        self.report({'INFO'}, f"Removed ducking from {n} strip(s)")
        return {'FINISHED'}


# --------------------------------------------------------------------------- loudness
def measure_mix(scene):
    """Render the scene's audio (frame range, volumes, animation) and measure it."""
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="vsect_mix_")
    os.close(fd)
    try:
        win = bpy.context.window
        override = {"scene": scene}
        if win is not None:
            override["window"] = win
        with bpy.context.temp_override(**override):
            bpy.ops.sound.mixdown(filepath=path, check_existing=False, relative_path=False,
                                  container='WAV', codec='PCM', format='F32')
        return loudness.measure_file(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def normalize_gain_db(measured, peak, target, peak_safe=True, ceiling=PEAK_CEILING):
    gain = target - measured
    limited = False
    if peak_safe and peak + gain > ceiling:
        gain = ceiling - peak
        limited = True
    return gain, limited


class VSECT_OT_loudness_measure(bpy.types.Operator):
    """Measure the integrated loudness (LUFS) of the whole edit"""
    bl_idname = "vsect.loudness_measure"
    bl_label = "Measure Mix"

    def execute(self, context):
        st = context.scene.vsect_audio
        lufs, peak = measure_mix(context.scene)
        st.measured, st.measured_peak, st.has_measurement = lufs, peak, lufs != float("-inf")
        self.report({'INFO'}, f"Mix: {loudness.fmt_lufs(lufs)} LUFS, peak {peak:.1f} dBFS")
        return {'FINISHED'}


class VSECT_OT_loudness_normalize(bpy.types.Operator):
    """Set the scene's master volume so the edit hits the target loudness"""
    bl_idname = "vsect.loudness_normalize"
    bl_label = "Normalise Mix"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        st = scene.vsect_audio
        lufs, peak = measure_mix(scene)
        if lufs == float("-inf"):
            self.report({'ERROR'}, "The edit is silent; nothing to normalise")
            return {'CANCELLED'}
        target = _target_lufs(st)
        gain, limited = normalize_gain_db(lufs, peak, target, st.peak_safe)
        scene.audio_volume = min(100.0, scene.audio_volume * _db_to_gain(gain))
        st.measured, st.measured_peak, st.has_measurement = lufs + gain, peak + gain, True
        msg = f"Master volume ×{_db_to_gain(gain):.2f} ({gain:+.1f} dB) → {lufs + gain:.1f} LUFS"
        if limited:
            msg += f" (held back to keep peaks under {PEAK_CEILING:.0f} dBFS)"
            self.report({'WARNING'}, msg)
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


def measure_strip(strip, fps):
    path, tmp = audio_analysis.sound_strip_path(strip)
    try:
        start = (strip.frame_final_start - strip.frame_start) / fps
        end = (strip.frame_final_end - strip.frame_start) / fps
        return loudness.measure_file(path, start, end)
    finally:
        if tmp:
            try:
                os.remove(path)
            except OSError:
                pass


class VSECT_OT_loudness_match(bpy.types.Operator):
    """Set each selected sound strip's volume so they all have the same loudness"""
    bl_idname = "vsect.loudness_match"
    bl_label = "Match Selected Clips"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(_voice_strips(context))

    def execute(self, context):
        scene = context.scene
        st = scene.vsect_audio
        fps = compat.scene_fps(scene)
        done, skipped = 0, []
        for s in _voice_strips(context):
            if compat.find_fcurve(scene, s.path_from_id("volume")) is not None:
                skipped.append(s.name)
                continue
            try:
                lufs, peak = measure_strip(s, fps)
            except audio_analysis.AudioLoadError as e:
                skipped.append(f"{s.name} ({e})")
                continue
            if lufs == float("-inf"):
                skipped.append(f"{s.name} (silent)")
                continue
            gain, _ = normalize_gain_db(lufs, peak, st.clip_target, st.peak_safe)
            s.volume = min(100.0, _db_to_gain(gain))
            done += 1
        msg = f"Matched {done} clip(s) to {st.clip_target:.0f} LUFS"
        if skipped:
            msg += f"; skipped {len(skipped)} (animated volume or silent): {', '.join(skipped[:3])}"
            self.report({'WARNING'}, msg)
        else:
            self.report({'INFO'}, msg)
        return {'FINISHED'}


# --------------------------------------------------------------------------- UI
class VSECT_PT_audio_ducking(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Creator"
    bl_label = "Auto Ducking"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        st = context.scene.vsect_audio
        voices = _voice_strips(context)
        box = layout.box()
        if voices:
            box.label(text=f"Voice: {', '.join(v.name for v in voices[:2])}"
                           + (f" +{len(voices) - 2}" if len(voices) > 2 else ""), icon='SOUND')
        else:
            box.label(text="Select your voice strip(s)", icon='INFO')
        layout.prop(st, "music_source")
        if st.music_source == 'CHANNEL':
            layout.prop(st, "music_channel")
        col = layout.column(align=True)
        col.prop(st, "duck_db")
        col.prop(st, "attack")
        col.prop(st, "release")
        col = layout.column(align=True)
        col.prop(st, "hold_gap")
        col.prop(st, "sensitivity")
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("vsect.audio_duck", icon='MOD_SOUND')
        row.operator("vsect.audio_unduck", text="", icon='X')


class VSECT_PT_audio_loudness(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Creator"
    bl_label = "Loudness"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene
        st = scene.vsect_audio

        layout.prop(st, "target")
        if st.target == 'CUSTOM':
            layout.prop(st, "custom_target")
        layout.prop(st, "peak_safe")
        box = layout.box().column(align=True)
        if st.has_measurement:
            box.label(text=f"Mix: {st.measured:.1f} LUFS · peak {st.measured_peak:.1f} dBFS", icon='SPEAKER')
            diff = st.measured - _target_lufs(st)
            if abs(diff) <= 1.0:
                box.label(text="On target", icon='CHECKMARK')
            else:
                box.label(text=f"{abs(diff):.1f} LU {'too loud' if diff > 0 else 'too quiet'}", icon='ERROR')
        else:
            box.label(text="Not measured yet", icon='SPEAKER')
        box.label(text=f"Master volume ×{scene.audio_volume:.2f}")
        row = layout.row(align=True)
        row.operator("vsect.loudness_measure", icon='VIEWZOOM')
        row.operator("vsect.loudness_normalize", icon='MOD_SOUND')

        layout.separator()
        layout.label(text="Clip Matching (selected strips)")
        layout.prop(st, "clip_target")
        layout.operator("vsect.loudness_match", icon='SEQ_STRIP_DUPLICATE')


classes = (VSECT_AudioSettings, VSECT_OT_duck, VSECT_OT_unduck, VSECT_OT_loudness_measure,
           VSECT_OT_loudness_normalize, VSECT_OT_loudness_match, VSECT_PT_audio_ducking,
           VSECT_PT_audio_loudness)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.vsect_audio = PointerProperty(type=VSECT_AudioSettings)


def unregister():
    del bpy.types.Scene.vsect_audio
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
