# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto Reframe panel and operators."""
import bpy
from bpy.props import EnumProperty, FloatProperty, PointerProperty

from . import compat, reframe


class VSECT_ReframeSettings(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Mode",
        items=[('STEADY', "Steady (per shot)", "One fixed framing per shot, switching exactly at cuts (how editors do it)"),
               ('FOLLOW', "Follow", "Smoothly follow the action inside each shot")],
        default='STEADY')
    samples: FloatProperty(name="Samples / Second", default=2.0, min=0.5, max=8.0,
                           description="How often to look at the video. Higher = more accurate Follow, slower")
    cut_sensitivity: FloatProperty(name="Cut Sensitivity", default=0.5, min=0.1, max=1.5,
                                   description="Lower finds more cuts (a brightness-histogram difference threshold)")
    smoothness: FloatProperty(name="Smoothness", default=0.85, min=0.0, max=0.98, subtype='FACTOR',
                              description="Follow mode: higher = calmer camera moves")
    centre_bias: FloatProperty(name="Centre Bias", default=0.15, min=0.0, max=0.9, subtype='FACTOR',
                               description="Preference for the middle of the frame when interest is spread out")


def _needs_variant(scene):
    r = scene.render
    return r.resolution_x >= r.resolution_y


class VSECT_OT_reframe(bpy.types.Operator):
    """Analyse the selected video strips and keyframe a crop that keeps the action in frame"""
    bl_idname = "vsect.reframe"
    bl_label = "Auto Reframe"

    _job = None
    _timer = None

    @classmethod
    def poll(cls, context):
        return context.scene and context.scene.sequence_editor and bool(reframe.reframe_strips(context))

    def invoke(self, context, event):
        scene = context.scene
        st = scene.vsect_reframe
        strips = reframe.reframe_strips(context)
        narrow = [s for s in strips if reframe.displayed_width(s) > scene.render.resolution_x * 1.01]
        if not narrow:
            self.report({'ERROR'}, "Footage already fits the frame width. Make a vertical version first: "
                                   "Export Presets → YouTube Shorts → Apply (Fill)")
            return {'CANCELLED'}
        self._job = reframe.ReframeJob(scene, narrow, st.samples, st.mode, st.cut_sensitivity,
                                       st.smoothness, st.centre_bias)
        wm = context.window_manager
        wm.progress_begin(0, max(1, self._job.total))
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):          # synchronous (scripts and tests)
        scene = context.scene
        st = scene.vsect_reframe
        strips = [s for s in reframe.reframe_strips(context)
                  if reframe.displayed_width(s) > scene.render.resolution_x * 1.01]
        if not strips:
            self.report({'ERROR'}, "Nothing to reframe: footage already fits the frame width")
            return {'CANCELLED'}
        job = reframe.ReframeJob(scene, strips, st.samples, st.mode, st.cut_sensitivity, st.smoothness, st.centre_bias)
        while not job.done:
            job.step()
        shots = job.finish()
        self.report({'INFO'}, f"Reframed {len(strips)} strip(s), {shots} shot(s)")
        return {'FINISHED'}

    def modal(self, context, event):
        job = self._job
        wm = context.window_manager
        if event.type == 'ESC':
            wm.event_timer_remove(self._timer)
            wm.progress_end()
            job.cleanup()
            if context.area:
                context.area.header_text_set(None)
            self.report({'WARNING'}, "Auto Reframe cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        for _ in range(3):
            if job.done:
                break
            job.step()
        wm.progress_update(job.pos)
        if context.area:
            context.area.header_text_set(f"Auto Reframe: {job.pos}/{job.total} frames analysed  (Esc to cancel)")
        if not job.done:
            return {'RUNNING_MODAL'}
        wm.event_timer_remove(self._timer)
        wm.progress_end()
        if context.area:
            context.area.header_text_set(None)
        shots = job.finish()
        bpy.ops.ed.undo_push(message="Auto Reframe")
        self.report({'INFO'}, f"Reframed {len(job.strips)} strip(s), {shots} shot(s)")
        return {'FINISHED'}


class VSECT_OT_reframe_clear(bpy.types.Operator):
    """Remove reframe keyframes from the selected strips and centre them again"""
    bl_idname = "vsect.reframe_clear"
    bl_label = "Clear Reframe"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = 0
        for s in reframe.reframe_strips(context):
            reframe.clear_offset_keys(context.scene, s)
            n += 1
        self.report({'INFO'}, f"Cleared {n} strip(s)")
        return {'FINISHED'}


class VSECT_PT_reframe(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Creator"
    bl_label = "Auto Reframe (9:16)"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene
        st = scene.vsect_reframe
        box = layout.box()
        if _needs_variant(scene):
            box.label(text="This scene is wide. First make a vertical", icon='INFO')
            box.label(text="version: Export Presets → YouTube Shorts → Fill")
        strips = reframe.reframe_strips(context)
        if strips:
            box.label(text=f"{len(strips)} video strip(s) selected", icon='SEQUENCE')
        else:
            box.label(text="Select the video strips to reframe", icon='SEQUENCE')
        layout.prop(st, "mode")
        layout.prop(st, "samples")
        layout.prop(st, "cut_sensitivity")
        if st.mode == 'FOLLOW':
            layout.prop(st, "smoothness")
        layout.prop(st, "centre_bias")
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("vsect.reframe", icon='VIEW_CAMERA')
        row.operator("vsect.reframe_clear", text="", icon='X')
        layout.label(text="Fine-tune: Strip → Transform → Position X keys", icon='INFO')


classes = (VSECT_ReframeSettings, VSECT_OT_reframe, VSECT_OT_reframe_clear, VSECT_PT_reframe)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.vsect_reframe = PointerProperty(type=VSECT_ReframeSettings)


def unregister():
    del bpy.types.Scene.vsect_reframe
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
