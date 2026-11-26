# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto Reframe: keep the action in frame when a wide edit becomes a vertical one.

For each selected movie/image strip in a narrow (e.g. 9:16) scene, small frames of the
*uncropped* source are rendered from a temporary scene, scored for interest (motion,
skin tones, detail, slight centre bias), split into shots at cuts, and turned into
transform offset keyframes: one steady framing per shot, or a smooth follow.
"""
import os
import tempfile

import bpy
import numpy as np

from . import compat

ANALYSIS_W, ANALYSIS_H = 192, 108


# ------------------------------------------------------------------ pure analysis helpers
def luminance(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def skin_mask(rgb):
    """Rough skin-tone mask in YCbCr (works across many skin tones in normal light)."""
    r, g, b = rgb[..., 0] * 255, rgb[..., 1] * 255, rgb[..., 2] * 255
    cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return ((cr > 135) & (cr < 180) & (cb > 85) & (cb < 135) & (y > 40)).astype(np.float32)


def _norm(a):
    m = float(a.max())
    return a / m if m > 1e-6 else a


def interest_map(rgb, prev_gray=None, w_motion=1.0, w_skin=0.8, w_detail=0.3):
    gray = luminance(rgb)
    gy, gx = np.gradient(gray)
    detail = _norm(np.hypot(gx, gy))
    score = w_detail * detail + w_skin * skin_mask(rgb)
    if prev_gray is not None:
        score = score + w_motion * _norm(np.abs(gray - prev_gray))
    return score, gray


def best_window(profile, win_frac, centre_bias=0.15):
    """Centre (0-1) of the window of width ``win_frac`` with the most interest."""
    n = len(profile)
    win = max(1, min(n, int(round(win_frac * n))))
    if win >= n:
        return 0.5
    x = np.linspace(0, 1, n)
    weighted = profile * (1 - centre_bias + centre_bias * np.exp(-((x - 0.5) ** 2) / 0.08))
    c = np.concatenate([[0.0], np.cumsum(weighted)])
    sums = c[win:] - c[:-win]
    if float(sums.max() - sums.min()) < 1e-6:
        return 0.5                                   # nothing interesting: keep centred
    i = int(np.argmax(sums))
    # many windows can contain the subject equally well: centre on the interest itself
    seg = weighted[i:i + win]
    tot = float(seg.sum())
    centre = i + (float((seg * np.arange(win)).sum()) / tot + 0.5 if tot > 0 else win / 2)
    centre = min(max(centre, win / 2), n - win / 2)          # keep the frame inside the footage
    return centre / n


def histogram(rgb, bins=12):
    """Colour histogram (R, G, B concatenated, sums to 1). Catches cuts between shots of
    similar brightness but different colour, which a brightness-only histogram misses."""
    parts = []
    for c in range(3):
        h, _ = np.histogram(rgb[..., c], bins=bins, range=(0, 1))
        parts.append(h / max(1, h.sum()))
    return np.concatenate(parts) / 3.0


def split_shots(hists, threshold=0.5):
    """Indices where a new shot starts (always includes 0)."""
    cuts = [0]
    for i in range(1, len(hists)):
        if float(np.abs(hists[i] - hists[i - 1]).sum()) > threshold:
            cuts.append(i)
    return cuts


def smooth(values, strength=0.85):
    out, acc = [], None
    for v in values:
        acc = v if acc is None else acc * strength + v * (1 - strength)
        out.append(acc)
    back, acc = [], None                              # forward-backward: no lag
    for v in reversed(out):
        acc = v if acc is None else acc * strength + v * (1 - strength)
        back.append(acc)
    return list(reversed(back))


def offset_for(u, displayed_w, frame_w):
    """Transform offset (px) that centres source position u (0-1), clamped to keep the frame filled."""
    limit = max(0.0, (displayed_w - frame_w) / 2)
    return float(max(-limit, min(limit, -(u - 0.5) * displayed_w)))


# ------------------------------------------------------------------ strip helpers
def _orig_size(strip):
    elems = getattr(strip, "elements", None)
    if elems and len(elems) and elems[0].orig_width:
        return elems[0].orig_width, elems[0].orig_height
    return None


def reframe_strips(context):
    sed = context.scene.sequence_editor
    if sed is None:
        return []
    return [s for s in compat.strips_top(sed) if s.select and s.type in ('MOVIE', 'IMAGE') and _orig_size(s)]


def displayed_width(strip):
    w, _h = _orig_size(strip)
    return w * strip.transform.scale_x


def clear_offset_keys(scene, strip):
    for prop in ("offset_x",):
        fc = compat.find_fcurve(scene, strip.transform.path_from_id(prop))
        if fc is not None:
            compat.remove_fcurve(scene, fc)
    strip.transform.offset_x = 0.0


# ------------------------------------------------------------------ job
class ReframeJob:
    """Analyses strips step by step (so a modal operator can show progress).

    ``step()`` renders and scores one sample; ``finish()`` writes keyframes.
    """

    def __init__(self, scene, strips, samples_per_sec=2.0, mode='STEADY', cut_threshold=0.5,
                 follow_smooth=0.85, centre_bias=0.15):
        self.scene = scene
        self.strips = strips
        self.mode = mode
        self.cut_threshold = cut_threshold
        self.follow_smooth = follow_smooth
        self.centre_bias = centre_bias
        fps = compat.scene_fps(scene)
        step = max(1, int(round(fps / samples_per_sec)))
        self.tasks = []                                   # (strip index, frame)
        for si, s in enumerate(strips):
            frames = list(range(s.frame_final_start, s.frame_final_end, step))
            if frames and frames[-1] != s.frame_final_end - 1:
                frames.append(s.frame_final_end - 1)
            self.tasks += [(si, f) for f in frames]
        self.results = {i: [] for i in range(len(strips))}   # (frame, u, hist)
        self.pos = 0
        self.tmpdir = tempfile.mkdtemp(prefix="vsect_reframe_")
        self._tmp_scene = None
        self._tmp_strip_for = None
        self._prev = {}

    # -- temporary scene showing one source strip uncropped, small
    def _prepare(self, si):
        if self._tmp_strip_for == si:
            return
        src = self.strips[si]
        if self._tmp_scene is None:
            sc = bpy.data.scenes.new("VSECT_Reframe_Analysis")
            sc.render.resolution_x, sc.render.resolution_y = ANALYSIS_W, ANALYSIS_H
            sc.render.resolution_percentage = 100
            sc.render.image_settings.file_format = 'PNG'
            sc.render.fps, sc.render.fps_base = self.scene.render.fps, self.scene.render.fps_base
            try:
                sc.view_settings.view_transform = 'Standard'
            except TypeError:
                pass
            sc.sequence_editor_create()
            self._tmp_scene = sc
        sc = self._tmp_scene
        sed = sc.sequence_editor
        for s in list(compat.strips_top(sed)):
            compat.remove_strip(sed, s)
        top = compat.strips_top(sed)
        if src.type == 'MOVIE':
            s = top.new_movie("src", bpy.path.abspath(src.filepath), channel=1, frame_start=int(round(src.frame_start)))
        else:
            path = bpy.path.abspath(os.path.join(src.directory, src.elements[0].filename))
            s = top.new_image("src", path, channel=1, frame_start=int(round(src.frame_start)))
            s.frame_final_duration = max(1, int(round(src.frame_final_end - src.frame_start)))
        ow, oh = _orig_size(s) or (ANALYSIS_W, ANALYSIS_H)
        k = min(ANALYSIS_W / ow, ANALYSIS_H / oh)
        s.transform.scale_x = s.transform.scale_y = k
        sc.frame_start = s.frame_final_start
        sc.frame_end = s.frame_final_end
        self._tmp_strip_for = si
        self._prev.pop(si, None)

    def _grab(self, frame):
        sc = self._tmp_scene
        path = os.path.join(self.tmpdir, "f.png")
        sc.render.filepath = path
        # Render the analysis scene as the *context* scene: rendering another scene by name
        # (render(scene=...)) can evaluate strips at the active scene's frame instead.
        with bpy.context.temp_override(scene=sc):
            sc.frame_set(frame)
            bpy.ops.render.render(write_still=True)
        img = bpy.data.images.load(path, check_existing=False)
        try:
            px = np.empty(len(img.pixels), dtype=np.float32)
            img.pixels.foreach_get(px)
            w, h = img.size
            rgb = px.reshape(h, w, 4)[..., :3]
        finally:
            bpy.data.images.remove(img)
        return rgb

    @property
    def total(self):
        return len(self.tasks)

    @property
    def done(self):
        return self.pos >= len(self.tasks)

    def _hist_at(self, frame):
        return histogram(self._grab(frame))

    def _refine_cut(self, f_prev, h_prev, f_cur, h_cur):
        """Binary-search the exact first frame of the new shot between two samples."""
        lo, hi = f_prev, f_cur                    # lo: old shot, hi: new shot
        while hi - lo > 1:
            mid = (lo + hi) // 2
            h = self._hist_at(mid)
            if np.abs(h - h_prev).sum() <= np.abs(h - h_cur).sum():
                lo = mid
            else:
                hi = mid
        return hi

    def step(self):
        si, frame = self.tasks[self.pos]
        self._prepare(si)
        rgb = self._grab(frame)
        score, gray = interest_map(rgb, self._prev.get(si))
        self._prev[si] = gray
        src = self.strips[si]
        frame_w = self.scene.render.resolution_x
        win_frac = min(1.0, frame_w / displayed_width(src))
        u = best_window(score.sum(axis=0), win_frac, self.centre_bias)
        h = histogram(rgb)
        cut_at = None
        prev = self.results[si][-1] if self.results[si] else None
        if prev is not None and float(np.abs(h - prev[2]).sum()) > self.cut_threshold:
            cut_at = self._refine_cut(prev[0], prev[2], frame, h)
        self.results[si].append((frame, u, h, cut_at))
        self.pos += 1

    def cleanup(self):
        if self._tmp_scene is not None:
            bpy.data.scenes.remove(self._tmp_scene)
            self._tmp_scene = None
        try:
            for n in os.listdir(self.tmpdir):
                os.remove(os.path.join(self.tmpdir, n))
            os.rmdir(self.tmpdir)
        except OSError:
            pass

    def finish(self):
        """Write offset keyframes. Returns the number of shots found."""
        self.cleanup()
        shots_total = 0
        for si, data in self.results.items():
            if not data:
                continue
            strip = self.strips[si]
            clear_offset_keys(self.scene, strip)
            dw, fw = displayed_width(strip), self.scene.render.resolution_x
            # split samples into shots; each shot starts at its exact cut frame
            shots, cur = [], {"start": strip.frame_final_start, "frames": [], "us": []}
            for frame, u, _h, cut_at in data:
                if cut_at is not None and cur["frames"]:
                    shots.append(cur)
                    cur = {"start": cut_at, "frames": [], "us": []}
                cur["frames"].append(frame)
                cur["us"].append(u)
            shots.append(cur)
            shots_total += len(shots)

            keys = []                                   # (frame, offset, interpolation)
            for shot in shots:
                if self.mode == 'STEADY':
                    keys.append((shot["start"], offset_for(float(np.median(shot["us"])), dw, fw), 'CONSTANT'))
                else:
                    us = smooth(shot["us"], self.follow_smooth)
                    fr = [shot["start"]] + shot["frames"][1:] if shot["frames"][0] != shot["start"] else shot["frames"]
                    for i, (f, u) in enumerate(zip(fr, us)):
                        last = i == len(us) - 1
                        keys.append((f, offset_for(u, dw, fw), 'CONSTANT' if last else 'BEZIER'))
            t = strip.transform
            for f, off, _ in keys:
                t.offset_x = off
                t.keyframe_insert("offset_x", frame=f)
            fc = compat.find_fcurve(self.scene, t.path_from_id("offset_x"))
            if fc is not None:
                interp = {int(f): ip for f, _o, ip in keys}
                for kp in fc.keyframe_points:
                    kp.interpolation = interp.get(int(round(kp.co[0])), 'BEZIER')
                fc.update()
        return shots_total
