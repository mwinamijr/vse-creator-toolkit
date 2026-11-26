# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto Captions: settings, operators, panel and add-on preferences (Whisper installer)."""
import json
import os

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import audio_analysis, caption_strips, captions, compat, transcribe

TRANSCRIPT = "VSECT Transcript.json"
SRT_TEXT = "Captions.srt"


# --------------------------------------------------------------------------- settings
class VSECT_CaptionSettings(bpy.types.PropertyGroup):
    mode: EnumProperty(
        name="Style",
        items=[('HIGHLIGHT', "Word Highlight", "Whole line with the spoken word highlighted (TikTok style)"),
               ('POP', "Word Pop", "One big word at a time (Shorts style)"),
               ('LINES', "Subtitles", "Classic subtitle lines")],
        default='HIGHLIGHT')
    model: EnumProperty(name="Model", items=transcribe.MODELS, default='base')
    language: StringProperty(name="Language", default="",
                             description="Language code like 'en', 'sw', 'es'. Leave empty to auto-detect")
    size: FloatProperty(name="Size", default=0.055, min=0.02, max=0.2, subtype='FACTOR',
                        description="Font size as a fraction of the video height")
    position_y: FloatProperty(name="Height", default=0.18, min=0.0, max=1.0, subtype='FACTOR',
                              description="Vertical position (0 = bottom, 1 = top)")
    max_chars: IntProperty(name="Max Characters", default=0, min=0, max=80,
                           description="Characters per caption line (0 = auto: 32 for wide, 18 for vertical videos)")
    max_duration: FloatProperty(name="Max Duration", default=3.0, min=0.5, max=10.0, subtype='TIME_ABSOLUTE',
                                unit='TIME_ABSOLUTE')
    uppercase: BoolProperty(name="UPPERCASE", default=False)
    strip_punctuation: BoolProperty(name="Remove Punctuation", default=False)
    font: PointerProperty(name="Font", type=bpy.types.VectorFont)
    bold: BoolProperty(name="Bold", default=True, description="Bold (not used by Word Highlight, which needs exact metrics)")
    color: FloatVectorProperty(name="Text", subtype='COLOR', size=3, min=0, max=1, default=(1, 1, 1))
    highlight_color: FloatVectorProperty(name="Highlight", subtype='COLOR', size=3, min=0, max=1,
                                         default=(0.96, 0.77, 0.09))
    outline: BoolProperty(name="Outline", default=True)
    outline_color: FloatVectorProperty(name="Outline Color", subtype='COLOR', size=3, min=0, max=1, default=(0, 0, 0))
    outline_width: FloatProperty(name="Outline Width", default=0.08, min=0.0, max=1.0)
    shadow: BoolProperty(name="Shadow", default=False)
    box: BoolProperty(name="Background Box", default=False)
    box_color: FloatVectorProperty(name="Box Color", subtype='COLOR', size=3, min=0, max=1, default=(0, 0, 0))
    pop_animation: BoolProperty(name="Pop-In Animation", default=True)
    wrap_width: FloatProperty(name="Wrap Width", default=0.85, min=0.2, max=1.0, subtype='FACTOR')
    channel_min: IntProperty(name="Lowest Channel", default=1, min=1, max=128,
                             description="Captions go on the first free channel at or above this one")
    replace: BoolProperty(name="Replace Existing Captions", default=True)


def _st(context):
    return context.scene.vsect_captions


def effective_max_chars(scene, st):
    if st.max_chars:
        return st.max_chars
    r = scene.render
    return 18 if r.resolution_y > r.resolution_x else 32


# --------------------------------------------------------------------------- transcript store
def save_transcript(words):
    tb = bpy.data.texts.get(TRANSCRIPT) or bpy.data.texts.new(TRANSCRIPT)
    tb.clear()
    tb.write(json.dumps({"version": 1, "words": words}, ensure_ascii=False, indent=0))


def load_transcript():
    tb = bpy.data.texts.get(TRANSCRIPT)
    if tb is None:
        return None
    try:
        return json.loads(tb.as_string())["words"]
    except (ValueError, KeyError):
        return None


def build_from_words(scene, words, st):
    """words: timeline seconds (frame / fps). Replaces captions if requested."""
    fps = compat.scene_fps(scene)
    if st.replace:
        caption_strips.clear(scene)
    caps = captions.group_words(words, max_chars=effective_max_chars(scene, st),
                                max_duration=st.max_duration, one_word=(st.mode == 'POP'))
    to_frame = lambda t: int(round(t * fps))       # noqa: E731
    return caption_strips.build(scene, caps, st, to_frame), caps


# --------------------------------------------------------------------------- operators
def _sound_strips(context):
    sed = context.scene.sequence_editor
    if sed is None:
        return []
    return [s for s in compat.strips_top(sed) if s.select and s.type == 'SOUND']


class VSECT_OT_captions_transcribe(bpy.types.Operator):
    """Transcribe the selected sound strips offline with Whisper and create captions"""
    bl_idname = "vsect.captions_transcribe"
    bl_label = "Transcribe & Caption"

    _job = None
    _timer = None
    _offsets = None

    @classmethod
    def poll(cls, context):
        return bool(_sound_strips(context))

    def invoke(self, context, event):
        if not transcribe.available():
            self.report({'ERROR'}, "Whisper isn't installed yet: Edit → Preferences → Add-ons/Extensions → "
                                   "VSE Creator Toolkit → Install Whisper")
            return {'CANCELLED'}
        scene = context.scene
        fps = compat.scene_fps(scene)
        chunks, self._offsets = [], []
        try:
            for s in sorted(_sound_strips(context), key=lambda x: x.frame_final_start):
                path, tmp = audio_analysis.sound_strip_path(s)
                try:
                    samples, rate = audio_analysis.load_mono(path, 16000)
                finally:
                    if tmp:
                        os.remove(path)
                if rate != 16000:
                    raise audio_analysis.AudioLoadError("Couldn't resample audio to 16 kHz")
                a = int(max(0, (s.frame_final_start - s.frame_start) / fps) * 16000)
                b = int(max(0, (s.frame_final_end - s.frame_start) / fps) * 16000)
                chunks.append((samples[a:b].copy(), 0))
                self._offsets.append(s.frame_final_start / fps)
        except audio_analysis.AudioLoadError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        st = _st(context)
        lang = st.language.strip() or None
        if st.model.endswith(".en"):
            lang = "en"
        self._job = transcribe.Job(chunks, st.model, lang)
        self._job.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.4, window=context.window)
        wm.progress_begin(0, 100)
        wm.modal_handler_add(self)
        self.report({'INFO'}, "Transcribing… (Esc to cancel). The first run downloads the model.")
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        wm.progress_end()
        if context.area:
            context.area.header_text_set(None)

    def modal(self, context, event):
        job = self._job
        if event.type == 'ESC':
            job.cancel = True
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        context.window_manager.progress_update(int(job.progress * 100))
        if context.area:
            context.area.header_text_set(f"Captions: {job.status}  (Esc to cancel)")
        if not job.done:
            return {'PASS_THROUGH'}
        self._finish(context)
        if job.error:
            self.report({'ERROR'}, job.error)
            return {'CANCELLED'}
        if job.cancel:
            self.report({'WARNING'}, "Transcription cancelled")
            return {'CANCELLED'}
        words = []
        for offset, ws in zip(self._offsets, job.results):
            for w in ws:
                words.append({"start": offset + w["start"], "end": offset + w["end"], "text": w["text"].strip()})
        words.sort(key=lambda w: w["start"])
        save_transcript(words)
        strips, caps = build_from_words(context.scene, words, _st(context))
        self.report({'INFO'}, f"{len(words)} words → {len(caps)} captions")
        return {'FINISHED'}


class VSECT_OT_captions_rebuild(bpy.types.Operator):
    """Rebuild captions from the saved transcript with the current style (no re-transcribing)"""
    bl_idname = "vsect.captions_rebuild"
    bl_label = "Rebuild Captions"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return TRANSCRIPT in bpy.data.texts

    def execute(self, context):
        words = load_transcript()
        if not words:
            self.report({'ERROR'}, "No transcript saved")
            return {'CANCELLED'}
        strips, caps = build_from_words(context.scene, words, _st(context))
        self.report({'INFO'}, f"Rebuilt {len(caps)} captions")
        return {'FINISHED'}


class VSECT_OT_captions_restyle(bpy.types.Operator):
    """Apply the current colours, size and position to existing caption strips"""
    bl_idname = "vsect.captions_restyle"
    bl_label = "Apply Style"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = caption_strips.restyle(context.scene, _st(context))
        self.report({'INFO'}, f"Restyled {n} caption strips")
        return {'FINISHED'}


class VSECT_OT_captions_clear(bpy.types.Operator):
    """Delete all caption strips made by this add-on"""
    bl_idname = "vsect.captions_clear"
    bl_label = "Delete Captions"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        n = caption_strips.clear(context.scene)
        self.report({'INFO'}, f"Deleted {n} caption strips")
        return {'FINISHED'}


def _srt_words(scene, text):
    fps = compat.scene_fps(scene)
    base = scene.frame_start / fps
    segs = captions.parse_srt(text)
    for s in segs:
        s["start"] += base
        s["end"] += base
    return captions.segments_to_words(segs), len(segs)


class VSECT_OT_captions_import_srt(bpy.types.Operator, ImportHelper):
    """Create captions from an .srt subtitle file"""
    bl_idname = "vsect.captions_import_srt"
    bl_label = "Import SRT"
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = ".srt"
    filter_glob: StringProperty(default="*.srt", options={'HIDDEN'})

    def execute(self, context):
        with open(self.filepath, encoding="utf-8-sig", errors="replace") as f:
            words, n = _srt_words(context.scene, f.read())
        if not words:
            self.report({'ERROR'}, "No subtitles found in the file")
            return {'CANCELLED'}
        save_transcript(words)
        build_from_words(context.scene, words, _st(context))
        self.report({'INFO'}, f"Imported {n} subtitles")
        return {'FINISHED'}


class VSECT_OT_captions_export_srt(bpy.types.Operator, ExportHelper):
    """Save the captions as an .srt file (upload it to YouTube for searchable captions)"""
    bl_idname = "vsect.captions_export_srt"
    bl_label = "Export SRT"
    filename_ext = ".srt"
    filter_glob: StringProperty(default="*.srt", options={'HIDDEN'})

    def execute(self, context):
        caps = caption_strips.read_back(context.scene)
        if not caps:
            self.report({'ERROR'}, "No caption strips to export")
            return {'CANCELLED'}
        with open(self.filepath, "w", encoding="utf-8") as f:
            f.write(captions.write_srt(caps))
        self.report({'INFO'}, f"Exported {len(caps)} captions")
        return {'FINISHED'}


class VSECT_OT_captions_edit_text(bpy.types.Operator):
    """Put the captions in a text block as SRT so you can fix words, then press Apply Edited Text"""
    bl_idname = "vsect.captions_edit_text"
    bl_label = "Edit as Text"

    def execute(self, context):
        caps = caption_strips.read_back(context.scene)
        tb = bpy.data.texts.get(SRT_TEXT) or bpy.data.texts.new(SRT_TEXT)
        tb.clear()
        tb.write(captions.write_srt(caps))
        self.report({'INFO'}, f"Open the '{SRT_TEXT}' text in a Text Editor, edit, then Apply Edited Text")
        return {'FINISHED'}


class VSECT_OT_captions_apply_text(bpy.types.Operator):
    """Rebuild captions from the edited SRT text block"""
    bl_idname = "vsect.captions_apply_text"
    bl_label = "Apply Edited Text"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return SRT_TEXT in bpy.data.texts

    def execute(self, context):
        words, n = _srt_words(context.scene, bpy.data.texts[SRT_TEXT].as_string())
        if not words:
            self.report({'ERROR'}, "The text block has no valid SRT entries")
            return {'CANCELLED'}
        save_transcript(words)
        build_from_words(context.scene, words, _st(context))
        self.report({'INFO'}, f"Applied {n} edited captions")
        return {'FINISHED'}


class VSECT_OT_install_whisper(bpy.types.Operator):
    """Download and install faster-whisper (about 100 MB) into this add-on's user folder"""
    bl_idname = "vsect.install_whisper"
    bl_label = "Install Whisper"

    _inst = None
    _timer = None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        self._inst = transcribe.Installer()
        self._inst.start()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        self.report({'INFO'}, "Installing faster-whisper… this can take a few minutes")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER' or not self._inst.done:
            return {'PASS_THROUGH'}
        context.window_manager.event_timer_remove(self._timer)
        if self._inst.ok and transcribe.available():
            self.report({'INFO'}, "Whisper installed. Select a sound strip and press Transcribe & Caption")
            return {'FINISHED'}
        self.report({'ERROR'}, "Install failed: " + self._inst.log.strip().splitlines()[-1] if self._inst.log.strip()
                    else "Install failed")
        return {'CANCELLED'}


# --------------------------------------------------------------------------- UI
class VSECT_PT_captions(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Creator"
    bl_label = "Auto Captions"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        scene = context.scene
        st = _st(context)

        box = layout.box()
        if transcribe.available():
            col = box.column()
            col.prop(st, "model")
            col.prop(st, "language")
            sounds = _sound_strips(context)
            row = box.row()
            row.scale_y = 1.4
            row.operator("vsect.captions_transcribe", icon='REC')
            if not sounds:
                box.label(text="Select your voice strip(s)", icon='INFO')
        else:
            box.label(text="Offline transcription needs Whisper", icon='INFO')
            box.operator("vsect.install_whisper", icon='IMPORT')
            box.label(text="(or import an .srt file below)")

        if scene.view_settings.view_transform != 'Standard':
            warn = layout.box()
            warn.label(text=f"View transform is {scene.view_settings.view_transform}: white looks grey", icon='ERROR')
            warn.prop(scene.view_settings, "view_transform", text="Set to")

        col = layout.column()
        col.prop(st, "mode")
        col.prop(st, "size")
        col.prop(st, "position_y")
        sub = col.column(align=True)
        sub.prop(st, "color")
        if st.mode == 'HIGHLIGHT':
            sub.prop(st, "highlight_color")
        col.prop(st, "font")
        col.prop(st, "outline")
        if st.outline:
            sub = col.column(align=True)
            sub.prop(st, "outline_color")
            sub.prop(st, "outline_width")
        col.prop(st, "box")
        col.prop(st, "uppercase")
        col.prop(st, "strip_punctuation")
        if st.mode == 'POP':
            col.prop(st, "pop_animation")
        else:
            col.prop(st, "max_chars")
            col.prop(st, "max_duration")
        if st.mode == 'LINES':
            col.prop(st, "bold")
        col.prop(st, "replace")

        n = len(caption_strips.caption_strips(scene))
        row = layout.row(align=True)
        row.operator("vsect.captions_rebuild", icon='FILE_REFRESH')
        row.operator("vsect.captions_restyle", icon='BRUSH_DATA')
        row.operator("vsect.captions_clear", text="", icon='TRASH')
        layout.label(text=f"{n} caption strips", icon='FONT_DATA')

        row = layout.row(align=True)
        row.operator("vsect.captions_import_srt", icon='IMPORT')
        row.operator("vsect.captions_export_srt", icon='EXPORT')
        row = layout.row(align=True)
        row.operator("vsect.captions_edit_text", icon='TEXT')
        row.operator("vsect.captions_apply_text", icon='CHECKMARK')


class VSECT_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Auto Captions (offline speech-to-text)", icon='REC')
        if transcribe.available():
            box.label(text="faster-whisper is installed", icon='CHECKMARK')
        else:
            box.label(text="Downloads faster-whisper and its libraries (about 100 MB) from PyPI.")
            box.label(text="Speech models download on first use (75-480 MB, from Hugging Face).")
            box.operator("vsect.install_whisper", icon='IMPORT')
        box.label(text=f"Packages: {transcribe.deps_dir()}")
        box.label(text=f"Models: {transcribe.models_dir()}")


classes = (VSECT_CaptionSettings, VSECT_OT_captions_transcribe, VSECT_OT_captions_rebuild,
           VSECT_OT_captions_restyle, VSECT_OT_captions_clear, VSECT_OT_captions_import_srt,
           VSECT_OT_captions_export_srt, VSECT_OT_captions_edit_text, VSECT_OT_captions_apply_text,
           VSECT_OT_install_whisper, VSECT_PT_captions, VSECT_AddonPreferences)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.vsect_captions = PointerProperty(type=VSECT_CaptionSettings)
    transcribe.ensure_path()


def unregister():
    del bpy.types.Scene.vsect_captions
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
