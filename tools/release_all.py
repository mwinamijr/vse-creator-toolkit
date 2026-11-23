# SPDX-License-Identifier: GPL-3.0-or-later
"""Create a GitHub Release, with both install zips, for every v* tag that lacks one.

Runs in GitHub Actions (uses the preinstalled `gh` CLI and GH_TOKEN). Each tag is
checked out into its own worktree and built with *this* build script, so older
versions get the same packaging as new ones.

    python tools/release_all.py            # build + publish missing releases
    python tools/release_all.py --dry-run  # only build into dist/<tag>/, publish nothing
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build  # noqa: E402

ROOT = build.ROOT


def run(*cmd, check=True, capture=True):
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=capture)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}\n{r.stderr}")
    return r


def tags():
    out = run("git", "tag", "-l", "v*").stdout.split()
    key = lambda t: tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", t.lstrip("v")))
    return sorted(out, key=key)


def has_release(tag):
    return run("gh", "release", "view", tag, check=False).returncode == 0


def changelog_notes(ver):
    path = os.path.join(ROOT, "CHANGELOG.md")
    if not os.path.isfile(path):
        return None
    text = open(path, encoding="utf-8").read()
    m = re.search(rf"^## \[?v?{re.escape(ver)}\]?.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


INSTALL = """
### Install
- **Blender 4.2+**: download `{ext}` and drag it into Blender (or *Preferences → Get Extensions → ⌄ → Install from Disk*).
- **Blender 3.6 – 4.1**: download `{legacy}`, then *Preferences → Add-ons → Install…* and enable **VSE Creator Toolkit**.

Installing a different version replaces the current one, so you can switch between releases to compare them.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    all_tags = tags()
    if not all_tags:
        print("no v* tags")
        return
    newest = all_tags[-1]
    work = tempfile.mkdtemp(prefix="vsect_rel_")
    try:
        for tag in all_tags:
            if not args.dry_run and has_release(tag):
                print(f"{tag}: release exists")
                continue
            tree = os.path.join(work, tag)
            run("git", "worktree", "add", "--detach", tree, tag)
            try:
                src = os.path.join(tree, build.PKG)
                ver, files = build.build(src, os.path.join(ROOT, "dist", tag))
                if ver != tag.lstrip("v"):
                    print(f"WARNING {tag}: manifest says version {ver}")
                notes = (changelog_notes(ver) or f"Release {tag}.") + "\n" + INSTALL.format(
                    ext=os.path.basename(files[1]), legacy=os.path.basename(files[0]))
                print(f"{tag}: built {', '.join(os.path.basename(f) for f in files)}")
                if args.dry_run:
                    continue
                cmd = ["gh", "release", "create", tag, *files,
                       "--title", f"VSE Creator Toolkit {tag}", "--notes", notes,
                       f"--latest={'true' if tag == newest else 'false'}"]
                run(*cmd, capture=False)
                print(f"{tag}: released")
            finally:
                run("git", "worktree", "remove", "--force", tree, check=False)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
