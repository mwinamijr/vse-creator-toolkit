# SPDX-License-Identifier: GPL-3.0-or-later
"""Build release zips.

    python tools/build.py [--src PACKAGE_DIR] [--out DIR] [--blender BLENDER_EXE]

Produces, named after the version in blender_manifest.toml:
  * vse_creator_toolkit-<ver>-legacy.zip  -> Blender 3.6 - 4.1: Preferences > Add-ons > Install
  * vse_creator_toolkit-<ver>.zip         -> Blender 4.2+: drag into Blender / Install from Disk

With --blender the extension zip is built and validated by Blender itself
(`blender --command extension build`); without it the zip is assembled directly,
which is what CI uses.
"""
import argparse
import os
import re
import subprocess
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "vse_creator_toolkit"


def version(src):
    text = open(os.path.join(src, "blender_manifest.toml"), encoding="utf-8").read()
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def files(src):
    for base, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for n in sorted(names):
            if not n.endswith(".pyc"):
                yield os.path.join(base, n)


def build_legacy(src, out_dir, ver):
    out = os.path.join(out_dir, f"{PKG}-{ver}-legacy.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files(src):
            z.write(f, os.path.join(PKG, os.path.relpath(f, src)))   # top-level package folder
    return out


def build_extension(src, out_dir, ver, blender=None):
    out = os.path.join(out_dir, f"{PKG}-{ver}.zip")
    if blender:
        subprocess.run([blender, "--factory-startup", "--command", "extension", "build",
                        "--source-dir", src, "--output-filepath", out], check=True)
    else:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in files(src):
                z.write(f, os.path.relpath(f, src))                   # manifest at zip root
    return out


def build(src=None, out_dir=None, blender=None):
    src = os.path.abspath(src or os.path.join(ROOT, PKG))
    out_dir = os.path.abspath(out_dir or os.path.join(ROOT, "dist"))
    os.makedirs(out_dir, exist_ok=True)
    ver = version(src)
    return ver, [build_legacy(src, out_dir, ver), build_extension(src, out_dir, ver, blender)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--out")
    ap.add_argument("--blender")
    a = ap.parse_args()
    ver, outs = build(a.src, a.out, a.blender)
    print(f"version {ver}")
    for o in outs:
        print("  ", o)


if __name__ == "__main__":
    main()
