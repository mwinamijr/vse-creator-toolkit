# SPDX-License-Identifier: GPL-3.0-or-later
"""Build release zips into dist/.

    python tools/build.py [path-to-blender-4.2+-executable]

Produces:
  * vse_creator_toolkit-<ver>-legacy.zip  -> Blender 3.6 - 4.1: Preferences > Add-ons > Install
  * vse_creator_toolkit-<ver>.zip         -> Blender 4.2+: drag into Blender / Install from Disk
    (built with `blender --command extension build` when a Blender path is given)
"""
import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = "vse_creator_toolkit"
SRC = os.path.join(ROOT, PKG)
DIST = os.path.join(ROOT, "dist")


def version():
    text = open(os.path.join(SRC, "blender_manifest.toml"), encoding="utf-8").read()
    return re.search(r'^version\s*=\s*"([^"]+)"', text, re.M).group(1)


def files():
    for base, dirs, names in os.walk(SRC):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for n in names:
            if not n.endswith(".pyc"):
                yield os.path.join(base, n)


def build_legacy(ver):
    out = os.path.join(DIST, f"{PKG}-{ver}-legacy.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files():
            z.write(f, os.path.relpath(f, ROOT))        # keeps the top-level package folder
    return out


def build_extension(blender):
    subprocess.run([blender, "--factory-startup", "--command", "extension", "build",
                    "--source-dir", SRC, "--output-dir", DIST], check=True)


def main():
    os.makedirs(DIST, exist_ok=True)
    ver = version()
    print("legacy:", build_legacy(ver))
    if len(sys.argv) > 1:
        build_extension(sys.argv[1])
    else:
        print("extension: skipped (pass the Blender 4.2+ executable path to build it)")


if __name__ == "__main__":
    main()
