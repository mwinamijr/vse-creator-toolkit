# SPDX-License-Identifier: GPL-3.0-or-later
"""Small compatibility layer so one code base runs on Blender 3.6 LTS through 5.x.

API differences handled here:
  * 3.6 - 4.x: ``sequence_editor.sequences`` / ``sequences_all``
    5.0+:      ``sequence_editor.strips`` / ``strips_all``
  * ``Strip.split()`` exists in recent versions; older builds fall back to the
    ``sequencer.split`` operator, which needs a Sequencer area in the context.
  * Channel lock state lives on ``sequence_editor.channels`` (3.5+); strips also
    have their own ``lock`` flag.
"""
import bpy

BLENDER = bpy.app.version


def scene_fps(scene):
    return scene.render.fps / scene.render.fps_base


def strips_all(sed):
    if hasattr(sed, "strips_all"):
        return sed.strips_all
    return sed.sequences_all


def strips_top(sed):
    """Top-level strip collection (the one with new_*/remove)."""
    if hasattr(sed, "strips"):
        return sed.strips
    return sed.sequences


def remove_strip(sed, strip):
    strips_top(sed).remove(strip)


def channel_locked(sed, channel):
    chans = getattr(sed, "channels", None)
    if chans is not None and 0 <= channel < len(chans):
        return bool(chans[channel].lock)
    return False


def strip_locked(sed, strip):
    return bool(getattr(strip, "lock", False)) or channel_locked(sed, strip.channel)


def _find_sequencer_area(context):
    screen = context.window.screen if context.window else None
    if screen is None:
        return None, None
    for area in screen.areas:
        if area.type == 'SEQUENCE_EDITOR':
            for region in area.regions:
                if region.type == 'WINDOW':
                    return area, region
    return None, None


def split_strip(context, sed, strip, frame):
    """Split ``strip`` at ``frame`` and return the right-hand piece (or None).

    ``frame`` must lie strictly inside the strip.
    """
    if not (strip.frame_final_start < frame < strip.frame_final_end):
        return None

    if hasattr(strip, "split"):
        return strip.split(frame=frame, split_method='SOFT')

    # Fallback for builds without Strip.split(): use the operator.
    before = {s.as_pointer() for s in strips_top(sed)}
    for s in strips_all(sed):
        s.select = False
    strip.select = True
    area, region = _find_sequencer_area(context)
    if area is None:
        raise RuntimeError("Open a Video Sequencer area to cut strips in this Blender version.")
    with context.temp_override(area=area, region=region):
        bpy.ops.sequencer.split(frame=frame, channel=strip.channel, type='SOFT',
                                use_cursor_position=False, side='RIGHT')
    for s in strips_top(sed):
        if s.as_pointer() not in before:
            return s
    return None
