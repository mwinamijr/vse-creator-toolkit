# SPDX-License-Identifier: GPL-3.0-or-later
"""Export presets for YouTube, Shorts, Reels, TikTok and more.

A preset sets resolution and H.264/AAC MP4 settings. It can be applied to the
current scene or to a new *variant* scene (a full copy of the edit), so a 16:9
edit and its 9:16 version live side by side and the original is never changed.
"""
import os

import bpy
from bpy.props import EnumProperty, PointerProperty, StringProperty

from . import compat

# id: (label, width, height, crf, audio kbps, recommended max seconds or 0, description)
PRESETS = {
    'YT_1080':  ("YouTube 1080p",          1920, 1080, 'HIGH',   320, 0,   "16:9 Full HD for regular YouTube videos"),
    'YT_1440':  ("YouTube 1440p",          2560, 1440, 'HIGH',   320, 0,   "16:9 2K; YouTube gives higher bitrate to 1440p+ uploads"),
    'YT_4K':    ("YouTube 4K",             3840, 2160, 'HIGH',   320, 0,   "16:9 Ultra HD (slow to render on modest PCs)"),
    'SHORTS':   ("YouTube Shorts",         1080, 1920, 'HIGH',   192, 180, "9:16 vertical"),
    'REELS':    ("Instagram Reels",        1080, 1920, 'HIGH',   192, 180, "9:16 vertical"),
    'TIKTOK':   ("TikTok",                 1080, 1920, 'HIGH',   192, 600, "9:16 vertical"),
    'IG_FEED':  ("Instagram Feed 4:5",     1080, 1350, 'HIGH',   192, 0,   "4:5 portrait feed post"),
    'SQUARE':   ("Square 1:1",             1080, 1080, 'HIGH',   192, 0,   "1:1 for feeds and ads"),
    'PREVIEW':  ("Fast Preview",           1280, 720,  'MEDIUM', 128, 0,   "Quick 720p draft for checking the edit"),
}

PRESET_ITEMS = [(k, v[0], v[6]) for k, v in PRESETS.items()]

VISUAL_TYPES = {'MOVIE', 'IMAGE', 'CLIP', 'MASK'}


def aspect_label(w, h):
    from math import gcd
    g = gcd(w, h)
    return f"{w // g}:{h // g}" if (w // g) < 30 else f"{w / h:.2f}:1"


def edit_duration(scene):
    return (scene.frame_end - scene.frame_start + 1) / compat.scene_fps(scene)


def apply_render_settings(scene, preset_id, output_dir="//renders/"):
    label, w, h, crf, akbps, _max, _d = PRESETS[preset_id]
    r = scene.render
    r.resolution_x, r.resolution_y = w, h
    r.resolution_percentage = 100
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    # Video editing needs the 'Standard' view transform: AgX/Filmic (meant for 3D
    # renders) would grey out whites and shift the colours of your footage.
    try:
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
    except TypeError:
        pass

    ims = r.image_settings
    if hasattr(ims, "media_type"):          # Blender 5.0+ separates image/video output
        try:
            ims.media_type = 'VIDEO'
        except (TypeError, AttributeError):
            pass
    ims.file_format = 'FFMPEG'

    f = r.ffmpeg
    f.format = 'MPEG4'
    f.codec = 'H264'
    f.constant_rate_factor = crf
    f.ffmpeg_preset = 'GOOD'
    fps = compat.scene_fps(scene)
    f.gopsize = max(1, int(round(fps / 2)))  # keyframe every half second (YouTube recommendation)
    f.use_max_b_frames = True
    f.max_b_frames = 2
    f.audio_codec = 'AAC'
    f.audio_bitrate = akbps
    f.audio_channels = 'STEREO'
    f.audio_mixrate = 48000

    blend = bpy.path.basename(bpy.data.filepath)
    stem = os.path.splitext(blend)[0] if blend else "edit"
    slug = label.lower().replace(" ", "_").replace(":", "x")
    r.filepath = f"{output_dir}{stem}_{slug}_"
    r.use_file_extension = True


def _orig_size(strip):
    elems = getattr(strip, "elements", None)
    if elems and len(elems):
        e = elems[0]
        if e.orig_width and e.orig_height:
            return e.orig_width, e.orig_height
    if strip.type == 'CLIP' and strip.clip:
        return tuple(strip.clip.size)
    return None


def refit_strips(scene, mode):
    """Scale visual strips to the scene resolution. mode: 'FIT' (show all) or 'FILL' (crop to fill)."""
    sed = scene.sequence_editor
    if sed is None:
        return 0, 0
    W, H = scene.render.resolution_x, scene.render.resolution_y
    done = skipped = 0
    for s in compat.strips_all(sed):
        if s.type not in VISUAL_TYPES:
            continue
        size = _orig_size(s)
        if not size:
            skipped += 1
            continue
        ow, oh = size
        k = max(W / ow, H / oh) if mode == 'FILL' else min(W / ow, H / oh)
        t = s.transform
        t.scale_x = t.scale_y = k
        t.offset_x = t.offset_y = 0
        done += 1
    return done, skipped


def make_variant(scene, preset_id):
    """Full copy of the scene (strips included) named after the preset."""
    new = scene.copy()
    new.name = f"{scene.name} - {PRESETS[preset_id][0]}"
    return new


# --------------------------------------------------------------------------- settings / ops
class VSECT_ExportSettings(bpy.types.PropertyGroup):
    preset: EnumProperty(name="Preset", items=PRESET_ITEMS, default='YT_1080')
    target: EnumProperty(
        name="Apply To",
        items=[('VARIANT', "New Variant Scene", "Copy the edit into a new scene and apply the preset there; the original is untouched"),
               ('CURRENT', "This Scene", "Change this scene's output settings")],
        default='VARIANT')
    footage: EnumProperty(
        name="Footage",
        items=[('KEEP', "Keep", "Don't change strip sizes"),
               ('FILL', "Fill (Crop)", "Scale footage to fill the new frame, cropping the edges"),
               ('FIT', "Fit (Bars)", "Scale footage to fit inside the new frame, with bars")],
        default='FILL',
        description="How existing footage adapts when the aspect ratio changes")
    output_dir: StringProperty(name="Folder", default="//renders/", subtype='DIR_PATH')


class VSECT_OT_export_apply(bpy.types.Operator):
    """Apply the export preset (resolution, MP4/H.264, AAC audio)"""
    bl_idname = "vsect.export_apply"
    bl_label = "Apply Preset"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        st = context.scene.vsect_export
        scene = context.scene
        old_aspect = scene.render.resolution_x / scene.render.resolution_y
        if st.target == 'VARIANT':
            scene = make_variant(scene, st.preset)
            if context.window is not None:
                context.window.scene = scene
        apply_render_settings(scene, st.preset, st.output_dir)
        new_aspect = scene.render.resolution_x / scene.render.resolution_y
        msg = f"{PRESETS[st.preset][0]} applied to '{scene.name}'"
        if st.footage != 'KEEP' and abs(old_aspect - new_aspect) > 1e-3:
            done, skipped = refit_strips(scene, st.footage)
            msg += f"; refitted {done} strips" + (f" ({skipped} skipped)" if skipped else "")
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class VSECT_OT_export_render(bpy.types.Operator):
    """Render the edit to a video file with the current output settings"""
    bl_idname = "vsect.export_render"
    bl_label = "Render Video"

    def execute(self, context):
        if context.scene.render.image_settings.file_format != 'FFMPEG':
            self.report({'WARNING'}, "Output isn't a video format yet: apply a preset first")
            return {'CANCELLED'}
        bpy.ops.render.render('INVOKE_DEFAULT', animation=True)
        return {'FINISHED'}


class VSECT_PT_export(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Creator"
    bl_label = "Export Presets"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene
        st = scene.vsect_export
        label, w, h, crf, akbps, max_s, _d = PRESETS[st.preset]

        layout.prop(st, "preset")
        info = layout.box().column(align=True)
        info.label(text=f"{w}x{h} ({aspect_label(w, h)}) · MP4 H.264 · AAC {akbps}k", icon='FILE_MOVIE')
        dur = edit_duration(scene)
        if max_s and dur > max_s:
            info.label(text=f"Edit is {dur:.0f}s, longer than the recommended {max_s}s for {label}", icon='ERROR')
        else:
            info.label(text=f"Edit length {int(dur // 60)}:{dur % 60:04.1f}", icon='TIME')

        layout.prop(st, "target")
        cur = scene.render.resolution_x / scene.render.resolution_y
        sub = layout.column()
        sub.active = abs(cur - w / h) > 1e-3
        sub.prop(st, "footage")
        layout.prop(st, "output_dir")
        layout.operator("vsect.export_apply", icon='CHECKMARK')

        r = scene.render
        col = layout.column(align=True)
        col.label(text=f"Scene: {r.resolution_x}x{r.resolution_y} → {bpy.path.abspath(r.filepath) or '(no path)'}", icon='SCENE_DATA')
        row = col.row()
        row.scale_y = 1.3
        row.operator("vsect.export_render", icon='RENDER_ANIMATION')


classes = (VSECT_ExportSettings, VSECT_OT_export_apply, VSECT_OT_export_render, VSECT_PT_export)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.vsect_export = PointerProperty(type=VSECT_ExportSettings)


def unregister():
    del bpy.types.Scene.vsect_export
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
