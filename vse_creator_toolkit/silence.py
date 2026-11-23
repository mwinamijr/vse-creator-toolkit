# SPDX-License-Identifier: GPL-3.0-or-later
"""Silence Remover: settings, operators and sidebar panel."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       IntProperty, PointerProperty, StringProperty)

from . import audio_analysis, compat, cutting


# --------------------------------------------------------------------------- data
class VSECT_SilenceRange(bpy.types.PropertyGroup):
    start: IntProperty()
    end: IntProperty()


class VSECT_SilenceSettings(bpy.types.PropertyGroup):
    threshold_mode: EnumProperty(
        name="Threshold",
        items=[('AUTO', "Auto", "Measure the noise floor and speech level and pick a threshold between them"),
               ('MANUAL', "Manual", "Use a fixed loudness threshold in dB")],
        default='AUTO')
    sensitivity: FloatProperty(
        name="Sensitivity", default=0.35, min=0.05, max=0.9, subtype='FACTOR',
        description="Higher cuts more (quiet breaths and soft words may be treated as silence)")
    threshold_db: FloatProperty(
        name="Threshold (dB)", default=-40.0, min=-90.0, max=-5.0,
        description="Audio quieter than this counts as silence")
    min_silence: FloatProperty(
        name="Min Silence", default=0.5, min=0.1, max=10.0, unit='TIME_ABSOLUTE', subtype='TIME_ABSOLUTE',
        description="Only pauses at least this long are removed")
    padding: FloatProperty(
        name="Padding", default=0.12, min=0.0, max=1.0, unit='TIME_ABSOLUTE', subtype='TIME_ABSOLUTE',
        description="Audio kept before and after speech so words aren't clipped")
    keep_edges: BoolProperty(
        name="Keep Start/End Silence", default=False,
        description="Don't remove silence before the first sound and after the last one")
    ripple: BoolProperty(
        name="Ripple (Close Gaps)", default=True,
        description="Move later strips left so no gaps remain")
    update_scene_end: BoolProperty(
        name="Shorten Scene End", default=True,
        description="Reduce the scene end frame by the removed time")

    # results of the last analysis
    source_strip: StringProperty()
    channels: StringProperty(description="Comma-separated channels that will be cut")
    found_threshold: FloatProperty()
    ranges: CollectionProperty(type=VSECT_SilenceRange)


# --------------------------------------------------------------------------- helpers
def _settings(context):
    return context.scene.vsect_silence


def _source_strip(context):
    sed = context.scene.sequence_editor
    if sed is None:
        return None
    act = sed.active_strip
    if act is not None and act.type == 'SOUND':
        return act
    for s in compat.strips_all(sed):
        if s.select and s.type == 'SOUND':
            return s
    return None


def _target_channels(context, source):
    sed = context.scene.sequence_editor
    chans = {s.channel for s in compat.strips_top(sed) if s.select}
    chans.add(source.channel)
    return chans


def _fmt_time(seconds):
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:04.1f}"


# --------------------------------------------------------------------------- operators
class VSECT_OT_silence_detect(bpy.types.Operator):
    """Analyze the active sound strip and find pauses to remove"""
    bl_idname = "vsect.silence_detect"
    bl_label = "Detect Silence"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene and context.scene.sequence_editor and _source_strip(context) is not None

    def execute(self, context):
        st = _settings(context)
        strip = _source_strip(context)
        fps = compat.scene_fps(context.scene)
        try:
            res = audio_analysis.analyze_strip(
                strip, st.threshold_mode, st.threshold_db, st.sensitivity,
                st.min_silence, st.padding, st.keep_edges)
        except audio_analysis.AudioLoadError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        frames = audio_analysis.source_ranges_to_frames(strip, res["ranges"], fps)

        st.ranges.clear()
        for a, b in frames:
            r = st.ranges.add()
            r.start, r.end = a, b
        st.source_strip = strip.name
        st.found_threshold = res["threshold"]
        st.channels = ",".join(str(c) for c in sorted(_target_channels(context, strip)))

        removed = sum(b - a for a, b in frames) / fps
        self.report({'INFO'}, f"Found {len(frames)} pauses, {removed:.1f}s to remove "
                              f"(threshold {res['threshold']:.1f} dB)")
        return {'FINISHED'}


class VSECT_OT_silence_markers(bpy.types.Operator):
    """Add timeline markers at the start and end of every detected pause, to review before cutting"""
    bl_idname = "vsect.silence_markers"
    bl_label = "Add Markers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene and len(context.scene.vsect_silence.ranges) > 0

    def execute(self, context):
        markers = context.scene.timeline_markers
        for i, r in enumerate(_settings(context).ranges, 1):
            markers.new(f"S{i} ▸", frame=r.start)
            markers.new(f"◂ S{i}", frame=r.end)
        return {'FINISHED'}


class VSECT_OT_silence_clear_markers(bpy.types.Operator):
    """Remove markers created by the Silence Remover"""
    bl_idname = "vsect.silence_clear_markers"
    bl_label = "Clear Markers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        markers = context.scene.timeline_markers
        ours = [m for m in markers
                if (m.name.startswith("S") and m.name.endswith("▸")) or m.name.startswith("◂ S")]
        for m in ours:
            markers.remove(m)
        return {'FINISHED'}


class VSECT_OT_silence_cut(bpy.types.Operator):
    """Cut the detected pauses out of every strip in the target channels"""
    bl_idname = "vsect.silence_cut"
    bl_label = "Remove Silence"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        sed = context.scene.sequence_editor if context.scene else None
        return sed is not None and len(context.scene.vsect_silence.ranges) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene = context.scene
        sed = scene.sequence_editor
        st = _settings(context)
        channels = {int(c) for c in st.channels.split(",") if c.strip()}
        problems = cutting.check_channels(sed, channels)
        if problems:
            self.report({'ERROR'}, "Can't cut: " + "; ".join(problems[:3]))
            return {'CANCELLED'}

        old_end = cutting.channels_end(sed, channels)
        ranges = [(r.start, r.end) for r in st.ranges]
        try:
            removed, cuts = cutting.remove_ranges(context, sed, ranges, channels, ripple=st.ripple)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        if st.ripple and st.update_scene_end and old_end is not None and scene.frame_end >= old_end - 1:
            scene.frame_end = max(scene.frame_start, scene.frame_end - removed)

        st.ranges.clear()
        fps = compat.scene_fps(scene)
        self.report({'INFO'}, f"Removed {cuts} pauses ({removed / fps:.1f}s)")
        return {'FINISHED'}


class VSECT_OT_silence_clear(bpy.types.Operator):
    """Forget the detected pauses"""
    bl_idname = "vsect.silence_clear"
    bl_label = "Clear Results"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _settings(context).ranges.clear()
        return {'FINISHED'}


# --------------------------------------------------------------------------- UI
class VSECT_PT_silence(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Creator"
    bl_label = "Silence Remover"

    @classmethod
    def poll(cls, context):
        sd = context.space_data
        return sd is not None and getattr(sd, "view_type", 'SEQUENCER') in {'SEQUENCER', 'SEQUENCER_PREVIEW'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        st = _settings(context)
        src = _source_strip(context)

        box = layout.box()
        if src is None:
            box.label(text="Select your voice/sound strip", icon='INFO')
            box.label(text="(also select the video to cut with it)")
        else:
            box.label(text=src.name, icon='SOUND')

        col = layout.column(align=True)
        col.prop(st, "threshold_mode")
        if st.threshold_mode == 'AUTO':
            col.prop(st, "sensitivity")
        else:
            col.prop(st, "threshold_db")
        col = layout.column(align=True)
        col.prop(st, "min_silence")
        col.prop(st, "padding")
        layout.prop(st, "keep_edges")
        layout.operator("vsect.silence_detect", icon='VIEWZOOM')

        if len(st.ranges):
            fps = compat.scene_fps(context.scene)
            removed = sum(r.end - r.start for r in st.ranges) / fps
            res = layout.box()
            res.use_property_split = False
            res.label(text=f"{len(st.ranges)} pauses · saves {_fmt_time(removed)}", icon='CHECKMARK')
            res.label(text=f"Threshold {st.found_threshold:.1f} dB · channels {st.channels}")
            row = res.row(align=True)
            row.operator("vsect.silence_markers", icon='MARKER_HLT')
            row.operator("vsect.silence_clear_markers", text="", icon='X')
            res.prop(st, "ripple")
            sub = res.column()
            sub.active = st.ripple
            sub.prop(st, "update_scene_end")
            row = res.row(align=True)
            row.scale_y = 1.4
            row.operator("vsect.silence_cut", icon='SEQ_SEQUENCER')
            row.operator("vsect.silence_clear", text="", icon='TRASH')


classes = (
    VSECT_SilenceRange,
    VSECT_SilenceSettings,
    VSECT_OT_silence_detect,
    VSECT_OT_silence_markers,
    VSECT_OT_silence_clear_markers,
    VSECT_OT_silence_cut,
    VSECT_OT_silence_clear,
    VSECT_PT_silence,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.vsect_silence = PointerProperty(type=VSECT_SilenceSettings)


def unregister():
    del bpy.types.Scene.vsect_silence
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
