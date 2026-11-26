# SPDX-License-Identifier: GPL-3.0-or-later
"""Create, restyle and read caption text strips in the sequencer.

Three styles:
  LINES      classic subtitles (one strip per caption, may wrap to 2 lines)
  POP        one big word at a time (Shorts/TikTok), optional pop-in scale
  HIGHLIGHT  the whole line, plus an overlay strip per word in the highlight
             colour, positioned with font metrics so it sits exactly on the word
"""
import os

import bpy

from . import compat

ROLE = "vsect_caption"      # custom property: 'line', 'word', 'pop'
CAP_ID = "vsect_cap"        # caption index
PREFIX = "vsect_prefix"     # highlight: text before the word on its line
LINE = "vsect_line"         # highlight: full line text


DEFAULT_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",                                   # Windows: Arial Bold
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",                # macOS
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",             # Linux
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def default_font():
    """A real font file for captions, so highlight positions can be measured exactly.

    Uses a bold system sans font, falling back to Blender's bundled Inter.
    """
    paths = list(DEFAULT_FONT_CANDIDATES)
    try:
        paths.append(os.path.join(bpy.utils.system_resource('DATAFILES'), "fonts", "Inter.woff2"))
    except Exception:                                                  # noqa: BLE001
        pass
    for p in paths:
        if os.path.isfile(p):
            try:
                return bpy.data.fonts.load(p, check_existing=True)
            except RuntimeError:
                continue
    return None


def caption_font(st):
    return st.font if st.font is not None else default_font()


def _font_path(font):
    if font is None:
        return None
    p = bpy.path.abspath(font.filepath)
    return p if os.path.isfile(p) else None


def text_width(text, size_px, font=None):
    """Width in pixels of ``text`` drawn at ``size_px`` with the strip font."""
    import blf
    fid = 0
    path = _font_path(font)
    if path:
        fid = blf.load(path)
        if fid == -1:
            fid = 0
    try:
        blf.size(fid, size_px)
    except TypeError:                       # Blender < 4.0 had a dpi argument
        blf.size(fid, size_px, 72)
    return blf.dimensions(fid, text)[0]


def _set(obj, attr, value):
    if hasattr(obj, attr):
        try:
            setattr(obj, attr, value)
        except (TypeError, AttributeError):
            pass


def apply_style(strip, st, scene, color=None):
    H = scene.render.resolution_y
    strip.font_size = max(8, int(round(H * st.size)))
    strip.color = (*(color or st.color), 1.0)
    font = caption_font(st)
    if font is not None:
        strip.font = font
    _set(strip, "use_bold", st.bold and st.mode != 'HIGHLIGHT')
    _set(strip, "use_outline", st.outline)
    _set(strip, "outline_color", (*st.outline_color, 1.0))
    _set(strip, "outline_width", st.outline_width)
    _set(strip, "use_shadow", st.shadow)
    _set(strip, "shadow_color", (0, 0, 0, 0.8))
    _set(strip, "use_box", st.box and strip.get(ROLE) != 'word')
    _set(strip, "box_color", (*st.box_color, 0.6))
    _set(strip, "box_margin", 0.01)
    strip.align_y = 'CENTER'


def _position(strip, st, scene):
    role = strip.get(ROLE)
    if role == 'word':
        W = scene.render.resolution_x
        size = strip.font_size
        font = strip.font                     # measure with exactly the font the strip draws with
        line_w = text_width(strip[LINE], size, font)
        pre_w = text_width(strip[PREFIX], size, font) if strip[PREFIX] else 0.0
        strip.align_x = 'LEFT'
        strip.location = ((W / 2 - line_w / 2 + pre_w) / W, st.position_y)
        strip.wrap_width = 1.0
    else:
        strip.align_x = 'CENTER'
        strip.location = (0.5, st.position_y)
        strip.wrap_width = st.wrap_width if role == 'line' and st.mode == 'LINES' else 1.0


def free_channel(sed, frame_start, frame_end, min_channel=1):
    """Lowest channel ≥ min_channel above everything in the range."""
    used = [s.channel for s in compat.strips_top(sed)
            if s.frame_final_end > frame_start and s.frame_final_start < frame_end]
    return max([min_channel - 1] + used) + 1


def _new_text(sed, name, channel, f0, f1):
    s = compat.strips_top(sed).new_effect(name=name, type='TEXT', channel=channel,
                                          frame_start=f0, frame_end=max(f1, f0 + 1))
    return s


def build(scene, captions, st, to_frame, channel=None):
    """Create strips for ``captions`` (times in seconds, mapped with ``to_frame``).

    Returns the list of created strips. Highlight mode uses two channels
    (line on ``channel``, word overlays on ``channel + 1``).
    """
    sed = scene.sequence_editor or scene.sequence_editor_create()
    if not captions:
        return []
    f_first = to_frame(captions[0]["start"])
    f_last = to_frame(captions[-1]["end"])
    ch = channel or free_channel(sed, f_first, f_last, st.channel_min)
    created = []
    upper, strip_p = st.uppercase, st.strip_punctuation
    from .captions import caption_text

    for ci, cap in enumerate(captions):
        f0, f1 = to_frame(cap["start"]), to_frame(cap["end"])
        if f1 <= f0:
            f1 = f0 + 1
        if st.mode in ('LINES', 'HIGHLIGHT'):
            s = _new_text(sed, f"CAP_{ci + 1:04d}", ch, f0, f1)
            s.text = caption_text(cap["words"], upper, strip_p)
            s[ROLE], s[CAP_ID] = 'line', ci
            created.append(s)
        if st.mode == 'HIGHLIGHT':
            pairs = [(w, caption_text([w], upper, strip_p)) for w in cap["words"]]
            pairs = [(w, t) for w, t in pairs if t]
            toks = [t for _, t in pairs]
            line = " ".join(toks)
            for wi, (w, tok) in enumerate(pairs):
                a = max(f0, to_frame(w["start"]))
                b = to_frame(pairs[wi + 1][0]["start"]) if wi + 1 < len(pairs) else f1
                b = min(max(b, a + 1), f1)
                if b <= a:
                    continue
                s = _new_text(sed, f"CAP_{ci + 1:04d}_w{wi + 1:02d}", ch + 1, a, b)
                s.text = tok
                s[ROLE], s[CAP_ID] = 'word', ci
                s[LINE] = line
                s[PREFIX] = (" ".join(toks[:wi]) + " ") if wi else ""
                created.append(s)
        if st.mode == 'POP':
            for wi, w in enumerate(cap["words"]):
                a = to_frame(w["start"])
                b = to_frame(cap["words"][wi + 1]["start"]) if wi + 1 < len(cap["words"]) else f1
                b = max(b, a + 1)
                s = _new_text(sed, f"CAP_{ci + 1:04d}_w{wi + 1:02d}", ch, a, b)
                s.text = caption_text([w], upper, strip_p)
                s[ROLE], s[CAP_ID] = 'pop', ci
                created.append(s)
    restyle(scene, st, created)
    return created


def restyle(scene, st, strips=None):
    sed = scene.sequence_editor
    if sed is None:
        return 0
    if strips is None:
        strips = caption_strips(scene)
    for s in strips:
        word = s.get(ROLE) == 'word'
        apply_style(s, st, scene, color=st.highlight_color if word else None)
        _position(s, st, scene)
        if s.get(ROLE) == 'pop' and st.pop_animation:
            _pop_keys(scene, s)
    return len(strips)


def _pop_keys(scene, s):
    t = getattr(s, "transform", None)
    if t is None:
        return
    f = s.frame_final_start
    for frame, k in ((f, 1.25), (f + 3, 1.0)):
        t.scale_x = t.scale_y = k
        t.keyframe_insert("scale_x", frame=frame)
        t.keyframe_insert("scale_y", frame=frame)
    t.scale_x = t.scale_y = 1.0


def caption_strips(scene):
    sed = scene.sequence_editor
    if sed is None:
        return []
    return [s for s in compat.strips_all(sed) if s.type == 'TEXT' and ROLE in s]


def clear(scene):
    sed = scene.sequence_editor
    n = 0
    for s in caption_strips(scene):
        compat.remove_strip(sed, s)
        n += 1
    return n


def read_back(scene):
    """Line-level captions from the strips on the timeline (in scene seconds)."""
    fps = compat.scene_fps(scene)
    groups = {}
    for s in caption_strips(scene):
        role = s.get(ROLE)
        if role == 'word':
            continue
        cid = s.get(CAP_ID, 0)
        g = groups.setdefault(cid, {"start": s.frame_final_start, "end": s.frame_final_end, "texts": []})
        g["start"] = min(g["start"], s.frame_final_start)
        g["end"] = max(g["end"], s.frame_final_end)
        g["texts"].append((s.frame_final_start, s.text))
    out = []
    for cid in sorted(groups, key=lambda c: groups[c]["start"]):
        g = groups[cid]
        text = " ".join(t for _, t in sorted(g["texts"]))
        out.append({"start": (g["start"] - scene.frame_start) / fps,
                    "end": (g["end"] - scene.frame_start) / fps, "text": text})
    return out
