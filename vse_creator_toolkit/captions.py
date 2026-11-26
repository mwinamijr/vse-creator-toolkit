# SPDX-License-Identifier: GPL-3.0-or-later
"""Caption engine (no bpy): group timed words into captions, read and write SRT.

A *word* is a dict {"start": s, "end": s, "text": str} in seconds.
A *caption* is {"start", "end", "words": [word, ...], "text": str}.
"""
import re

SENTENCE_END = (".", "?", "!", "…")
CLAUSE_END = (",", ";", ":")


def _clean(w):
    return w.strip()


def caption_text(words, upper=False, strip_punct=False):
    text = " ".join(_clean(w["text"]) for w in words if _clean(w["text"]))
    if strip_punct:
        text = re.sub(r"[.,!?;:…]+(?=\s|$)", "", text)
    return text.upper() if upper else text


def group_words(words, max_chars=32, max_duration=3.0, max_gap=0.6,
                break_on_clause=False, one_word=False, min_duration=0.4):
    """Group words into captions.

    A new caption starts when the line would exceed ``max_chars``, the caption
    would last longer than ``max_duration``, there's a pause longer than
    ``max_gap``, or the previous word ended a sentence.
    """
    words = [w for w in words if _clean(w["text"])]
    caps = []
    cur = []
    for w in words:
        if cur:
            text_len = len(caption_text(cur + [w]))
            gap = w["start"] - cur[-1]["end"]
            last = _clean(cur[-1]["text"])
            ends = last.endswith(SENTENCE_END) or (break_on_clause and last.endswith(CLAUSE_END))
            if (one_word or text_len > max_chars or w["end"] - cur[0]["start"] > max_duration
                    or gap > max_gap or ends):
                caps.append(cur)
                cur = []
        cur.append(w)
    if cur:
        caps.append(cur)

    out = [{"start": c[0]["start"], "end": c[-1]["end"], "words": c, "text": caption_text(c)} for c in caps]
    # tidy timing: minimum duration, close tiny gaps, never overlap
    for i, c in enumerate(out):
        nxt = out[i + 1]["start"] if i + 1 < len(out) else None
        if c["end"] - c["start"] < min_duration:
            c["end"] = c["start"] + min_duration
        if nxt is not None:
            if 0 < nxt - c["end"] < 0.25:
                c["end"] = nxt
            c["end"] = min(c["end"], nxt)
    return out


# ------------------------------------------------------------------ SRT
_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")


def _parse_time(s):
    m = _TIME.search(s)
    if not m:
        raise ValueError(f"bad SRT time: {s!r}")
    h, mi, se, ms = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")) / 1000.0


def format_time(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text):
    """Return a list of segments {"start", "end", "text"}."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    segs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.split("\n") if l.strip() != ""]
        if not lines:
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        a, b = lines[ti].split("-->", 1)
        body = " ".join(l.strip() for l in lines[ti + 1:])
        body = re.sub(r"<[^>]+>", "", body)          # strip <i>, <font> tags
        segs.append({"start": _parse_time(a), "end": _parse_time(b), "text": body.strip()})
    return segs


def write_srt(captions):
    blocks = []
    for i, c in enumerate(captions, 1):
        blocks.append(f"{i}\n{format_time(c['start'])} --> {format_time(c['end'])}\n{c['text']}\n")
    return "\n".join(blocks)


def segments_to_words(segments):
    """Approximate word timings inside SRT segments, proportional to word length."""
    words = []
    for seg in segments:
        toks = seg["text"].split()
        if not toks:
            continue
        total = sum(len(t) for t in toks) or 1
        t = seg["start"]
        dur = max(0.01, seg["end"] - seg["start"])
        for tok in toks:
            d = dur * len(tok) / total
            words.append({"start": t, "end": t + d, "text": tok})
            t += d
    return words
