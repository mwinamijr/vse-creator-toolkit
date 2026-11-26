# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline speech-to-text with faster-whisper (optional dependency).

The Python packages are installed on request into the add-on's own user folder
(never into Blender's installation), and speech models are downloaded on first
use into a 'models' folder next to it. Everything runs on the CPU (int8).
"""
import os
import subprocess
import sys
import threading

import bpy

MODELS = [
    ('tiny', "Tiny (≈75 MB, fastest)", "Quick drafts; lower accuracy"),
    ('base', "Base (≈145 MB, recommended)", "Good balance on modest PCs"),
    ('small', "Small (≈480 MB, accurate)", "Better accuracy, about 3x slower than Base"),
    ('tiny.en', "Tiny English-only (≈75 MB)", "English only; faster and more accurate than Tiny"),
    ('base.en', "Base English-only (≈145 MB)", "English only; best choice for English on modest PCs"),
    ('small.en', "Small English-only (≈480 MB)", "English only; accurate"),
]


def _pkg():
    return __package__


def data_dir(sub):
    """Per-user folder for this add-on's data (works for extensions and legacy add-ons)."""
    try:
        return bpy.utils.extension_path_user(_pkg(), path=sub, create=True)
    except (AttributeError, ValueError, KeyError, RuntimeError, TypeError):
        base = bpy.utils.user_resource('SCRIPTS', path=os.path.join("addons_data", "vse_creator_toolkit"))
        p = os.path.join(base, sub)
        os.makedirs(p, exist_ok=True)
        return p


def deps_dir():
    return data_dir("deps")


def models_dir():
    return data_dir("models")


def ensure_path():
    d = deps_dir()
    if os.path.isdir(d) and d not in sys.path:
        sys.path.append(d)          # appended: Blender's own numpy stays first


def available():
    ensure_path()
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ installing
class Installer:
    """Runs pip in a background thread; poll ``done``/``ok``/``log``."""

    def __init__(self):
        self.done = False
        self.ok = False
        self.log = ""

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        target = deps_dir()
        py = sys.executable
        try:
            try:
                import pip  # noqa: F401
            except ImportError:
                subprocess.run([py, "-m", "ensurepip", "--user"], check=True,
                               capture_output=True, text=True)
            r = subprocess.run([py, "-m", "pip", "install", "--upgrade", "--target", target,
                                "--no-warn-script-location", "faster-whisper"],
                               capture_output=True, text=True)
            self.log = (r.stdout or "")[-2000:] + (r.stderr or "")[-2000:]
            if r.returncode == 0:
                # keep Blender's bundled NumPy: remove the copy pip pulled in
                import shutil
                for n in os.listdir(target):
                    if n == "numpy" or n.startswith("numpy-") or n == "numpy.libs":
                        shutil.rmtree(os.path.join(target, n), ignore_errors=True)
                self.ok = True
        except Exception as e:                       # noqa: BLE001
            self.log += f"\n{e}"
        self.done = True


# ------------------------------------------------------------------ transcribing
class Job:
    """Transcribes a list of (samples_16k, offset) chunks in a background thread.

    Never touches bpy from the thread. Results: ``words`` per chunk, each word
    {"start", "end", "text"} in seconds relative to the chunk start.
    """

    def __init__(self, chunks, model_name, language=None, threads=None):
        self.chunks = chunks
        self.model_name = model_name
        self.language = language or None
        self.threads = threads or max(1, (os.cpu_count() or 2))
        self.results = []
        self.progress = 0.0
        self.status = "Loading model…"
        self.cancel = False
        self.done = False
        self.error = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            ensure_path()
            from faster_whisper import WhisperModel
            model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                 cpu_threads=self.threads, download_root=models_dir())
            total = sum(len(s) for s, _ in self.chunks) / 16000.0 or 1.0
            done_sec = 0.0
            for samples, _offset in self.chunks:
                words = []
                segments, _info = model.transcribe(samples, language=self.language,
                                                   word_timestamps=True, vad_filter=True,
                                                   condition_on_previous_text=False)
                for seg in segments:
                    if self.cancel:
                        self.status = "Cancelled"
                        self.done = True
                        return
                    for w in (seg.words or []):
                        words.append({"start": float(w.start), "end": float(w.end), "text": w.word})
                    self.progress = min(1.0, (done_sec + seg.end) / total)
                    self.status = f"Transcribing… {int(self.progress * 100)}%"
                self.results.append(words)
                done_sec += len(samples) / 16000.0
            self.progress = 1.0
            self.status = "Done"
        except Exception as e:                       # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
        self.done = True
