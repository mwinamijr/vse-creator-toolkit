# SPDX-License-Identifier: GPL-3.0-or-later
"""YouTube chapters from timeline markers.

YouTube only shows chapters when the list starts at 0:00, has at least three
entries, and every chapter is at least 10 seconds long. We build the text,
check those rules, and copy it to the clipboard and a Text datablock.
"""
import bpy
from bpy.props import BoolProperty, PointerProperty, StringProperty

from . import compat

TEXT_NAME = "YouTube Chapters"
MIN_CHAPTERS = 3
MIN_LENGTH = 10.0


def _is_silence_marker(name):
    return (name.startswith("S") and name.endswith("▸")) or name.startswith("◂ S")


def _is_default_name(name):
    return name == "" or (name.startswith("F_") and name[2:].isdigit())


def format_time(seconds, with_hours=False):
    s = int(seconds)               # YouTube timestamps are whole seconds (floor)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if with_hours:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def collect(scene, selected_only=False, intro_title="Intro"):
    """Return (chapters, problems). chapters = [(seconds, title)] sorted, starting at 0."""
    fps = compat.scene_fps(scene)
    start, end = scene.frame_start, scene.frame_end
    marks = [m for m in scene.timeline_markers
             if not _is_silence_marker(m.name)
             and (m.select or not selected_only)
             and start <= m.frame <= end]
    marks.sort(key=lambda m: m.frame)

    chapters = []
    for i, m in enumerate(marks, 1):
        title = m.name.strip()
        if _is_default_name(title):
            title = f"Chapter {i}"
        chapters.append(((m.frame - start) / fps, title))

    # merge markers that round to the same second
    dedup = []
    for t, title in chapters:
        if dedup and int(t) == int(dedup[-1][0]):
            dedup[-1] = (dedup[-1][0], title)
        else:
            dedup.append((t, title))
    chapters = dedup

    if not chapters or int(chapters[0][0]) != 0:
        chapters.insert(0, (0.0, intro_title))

    problems = []
    total = (end - start + 1) / fps
    if len(chapters) < MIN_CHAPTERS:
        problems.append(f"YouTube needs at least {MIN_CHAPTERS} chapters (you have {len(chapters)})")
    bounds = [t for t, _ in chapters] + [total]
    for (t, title), nxt in zip(chapters, bounds[1:]):
        if nxt - t < MIN_LENGTH:
            problems.append(f"'{title}' is {nxt - t:.0f}s; chapters must be at least {MIN_LENGTH:.0f}s")
    return chapters, problems


def build_text(chapters, total_seconds):
    hours = total_seconds >= 3600
    return "\n".join(f"{format_time(t, hours)} {title}" for t, title in chapters)


class VSECT_ChapterSettings(bpy.types.PropertyGroup):
    selected_only: BoolProperty(name="Selected Markers Only", default=False)
    intro_title: StringProperty(name="First Chapter", default="Intro",
                                description="Title used when no marker sits at the very start")


class VSECT_OT_chapters_copy(bpy.types.Operator):
    """Build YouTube chapters from timeline markers and copy them to the clipboard"""
    bl_idname = "vsect.chapters_copy"
    bl_label = "Copy YouTube Chapters"

    def execute(self, context):
        scene = context.scene
        st = scene.vsect_chapters
        chapters, problems = collect(scene, st.selected_only, st.intro_title)
        total = (scene.frame_end - scene.frame_start + 1) / compat.scene_fps(scene)
        text = build_text(chapters, total)
        context.window_manager.clipboard = text
        tb = bpy.data.texts.get(TEXT_NAME) or bpy.data.texts.new(TEXT_NAME)
        tb.clear()
        tb.write(text + "\n")
        if problems:
            self.report({'WARNING'}, f"Copied {len(chapters)} chapters, but YouTube may ignore them: {problems[0]}")
        else:
            self.report({'INFO'}, f"Copied {len(chapters)} chapters to the clipboard")
        return {'FINISHED'}


class VSECT_OT_chapter_add(bpy.types.Operator):
    """Add a named chapter marker at the current frame"""
    bl_idname = "vsect.chapter_add"
    bl_label = "Add Chapter Here"
    bl_options = {'REGISTER', 'UNDO'}

    title: StringProperty(name="Title", default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        scene = context.scene
        title = self.title.strip() or f"Chapter {len(scene.timeline_markers) + 1}"
        for m in scene.timeline_markers:
            if m.frame == scene.frame_current:
                m.name = title
                return {'FINISHED'}
        scene.timeline_markers.new(title, frame=scene.frame_current)
        return {'FINISHED'}


class VSECT_PT_chapters(bpy.types.Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Creator"
    bl_label = "YouTube Chapters"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        st = scene.vsect_chapters
        layout.operator("vsect.chapter_add", icon='MARKER_HLT')
        col = layout.column()
        col.use_property_split = True
        col.prop(st, "intro_title")
        col.prop(st, "selected_only")

        chapters, problems = collect(scene, st.selected_only, st.intro_title)
        total = (scene.frame_end - scene.frame_start + 1) / compat.scene_fps(scene)
        box = layout.box().column(align=True)
        hours = total >= 3600
        for t, title in chapters[:12]:
            box.label(text=f"{format_time(t, hours)}  {title}")
        if len(chapters) > 12:
            box.label(text=f"… {len(chapters) - 12} more")
        for p in problems[:3]:
            layout.label(text=p, icon='ERROR')
        row = layout.row()
        row.scale_y = 1.3
        row.operator("vsect.chapters_copy", icon='COPYDOWN')


classes = (VSECT_ChapterSettings, VSECT_OT_chapters_copy, VSECT_OT_chapter_add, VSECT_PT_chapters)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.vsect_chapters = PointerProperty(type=VSECT_ChapterSettings)


def unregister():
    del bpy.types.Scene.vsect_chapters
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
