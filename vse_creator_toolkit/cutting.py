# SPDX-License-Identifier: GPL-3.0-or-later
"""Timeline editing: remove frame ranges from a set of channels, with optional ripple."""
from . import compat

EFFECT_TYPES = {
    'CROSS', 'ADD', 'SUBTRACT', 'ALPHA_OVER', 'ALPHA_UNDER', 'GAMMA_CROSS', 'MULTIPLY',
    'OVER_DROP', 'WIPE', 'GLOW', 'TRANSFORM', 'SPEED', 'MULTICAM', 'GAUSSIAN_BLUR',
    'COLORMIX',
}


def _has_inputs(strip):
    return getattr(strip, "input_1", None) is not None


def strips_in_channels(sed, channels):
    return [s for s in compat.strips_top(sed) if s.channel in channels]


def check_channels(sed, channels):
    """Return a list of human-readable problems that block cutting these channels."""
    problems = []
    for ch in sorted(channels):
        if compat.channel_locked(sed, ch):
            problems.append(f"Channel {ch} is locked")
    for s in strips_in_channels(sed, channels):
        if s.type in EFFECT_TYPES and _has_inputs(s):
            problems.append(f"'{s.name}' is an effect/transition strip (remove it or move it to another channel)")
        elif getattr(s, "lock", False):
            problems.append(f"'{s.name}' is locked")
    return problems


def remove_ranges(context, sed, ranges, channels, ripple=True):
    """Cut every (start, end) frame range out of all strips in ``channels``.

    Ranges are processed from last to first so earlier frame numbers stay valid
    while later material shifts left. Returns (removed_frames, cuts_made).
    """
    removed_total = 0
    cuts = 0
    for a, b in sorted(ranges, reverse=True):
        if b <= a:
            continue
        # 1) isolate [a, b) in every strip of the target channels
        for s in list(strips_in_channels(sed, channels)):
            if s.frame_final_end <= a or s.frame_final_start >= b:
                continue
            piece = s
            right = compat.split_strip(context, sed, piece, a)
            if right is not None:
                piece = right
            compat.split_strip(context, sed, piece, b)
        # 2) delete the pieces that lie entirely inside [a, b)
        for s in list(strips_in_channels(sed, channels)):
            if s.frame_final_start >= a and s.frame_final_end <= b:
                compat.remove_strip(sed, s)
        cuts += 1
        removed_total += b - a
        # 3) ripple: pull later material left to close the gap
        if ripple:
            d = b - a
            later = [s for s in strips_in_channels(sed, channels) if s.frame_final_start >= b]
            for s in sorted(later, key=lambda x: x.frame_final_start):
                s.frame_start -= d
    return removed_total, cuts


def channels_end(sed, channels):
    ends = [s.frame_final_end for s in strips_in_channels(sed, channels)]
    return max(ends) if ends else None
