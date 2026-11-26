# SPDX-License-Identifier: GPL-3.0-or-later
"""VSE Creator Toolkit: creator features for Blender's Video Sequence Editor.

Works as a legacy add-on on Blender 3.6+ (bl_info below) and as an extension on
Blender 4.2+ (blender_manifest.toml).
"""

bl_info = {
    "name": "VSE Creator Toolkit",
    "author": "TechDome",
    "version": (0, 3, 0),
    "blender": (3, 6, 0),
    "location": "Video Sequencer > Sidebar (N) > Creator",
    "description": "Silence remover, auto ducking, loudness, export presets and YouTube chapters for the Video Sequence Editor",
    "doc_url": "",
    "tracker_url": "",
    "category": "Sequencer",
}

if "bpy" in locals():
    import importlib
    for _m in ("compat", "audio_analysis", "loudness", "cutting", "silence", "audio_tools", "export", "chapters"):
        if _m in locals():
            importlib.reload(locals()[_m])

import bpy  # noqa: E402

from . import compat, audio_analysis, loudness, cutting, silence, audio_tools, export, chapters  # noqa: E402,F401

_modules = (silence, audio_tools, export, chapters)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
