# VSE Creator Toolkit

Creator features for Blender's **Video Sequence Editor**: the time-savers that make
Premiere, Resolve and CapCut fast, built into Blender.

**Supported:** Blender **3.6 LTS** and newer (legacy add-on on 3.6–4.1, extension on 4.2+).
**License:** GPL-3.0-or-later.

## Features

### ✅ Silence Remover (v0.1)
Automatically finds pauses in your voice track and cuts them out of the audio *and*
the video, closing the gaps (jump cuts) in one click.

- Works with any audio Blender can play, including the audio of video files and packed sounds
- **Auto threshold** measures your noise floor and speech level, so there are no dB numbers to guess
- **Padding** keeps a little air around words so nothing gets clipped
- **Review first:** adds timeline markers at every pause before you commit
- Cuts every strip in the selected channels together, so audio and video stay in sync
- Leaves other channels (music, B-roll) untouched
- One undo step; refuses to touch locked channels or transition strips

### ✅ Export Presets (v0.2)
One click sets up the render for **YouTube 1080p / 1440p / 4K, YouTube Shorts, Instagram
Reels, TikTok, Instagram Feed 4:5, Square** or a **Fast Preview**: resolution, MP4/H.264,
AAC 48 kHz audio and a named output file.

- **New Variant Scene** (default) copies your edit into a new scene such as
  *"My Edit - YouTube Shorts"*, so your 16:9 edit is never changed.
- **Fill / Fit** refits your footage when the aspect ratio changes (Fill crops a 16:9 shot to
  fill a 9:16 frame).
- Warns when the edit is longer than a platform's recommended maximum.

### ✅ YouTube Chapters (v0.2)
Turns timeline markers into chapter text (`0:00 Intro`, `1:23 Modelling`, …), copied to the
clipboard and to a *YouTube Chapters* text block. It checks YouTube's rules (starts at 0:00,
at least 3 chapters, each at least 10 s), adds an intro chapter automatically, and ignores
Silence Remover markers. **Add Chapter Here** drops a named marker at the playhead.

### ✅ Auto Ducking (v0.3)
Select your voice strip(s) and press **Duck Music Under Voice**: the music dips while you talk
and comes back up in the pauses, with smooth fades. Short pauses between sentences keep the
music down, so it doesn't pump. It writes normal volume keyframes you can tweak, and **Remove
Ducking** restores the original level.

### ✅ Loudness (v0.3)
A built-in **EBU R128 / BS.1770 loudness meter** (verified against the EBU Tech 3341 test
signals).
- **Measure Mix**: integrated LUFS and peak of the whole edit.
- **Normalise Mix**: hits −14 LUFS (YouTube/Spotify), −16 (podcasts), −23 / −24 (broadcast) or a
  custom target via the master volume; *Prevent Clipping* keeps peaks under −1 dBFS.
- **Match Selected Clips**: gives clips recorded at different levels the same loudness.

### 🛣 Roadmap
| Version | Feature |
|---|---|
| 0.4 | Auto captions with offline Whisper, word-by-word animated subtitles, SRT import/export |
| 0.5 | 9:16 auto-reframe for vertical cut-downs |

## Download & install

Every version is on the **[Releases page](https://github.com/mwinamijr/vse-creator-toolkit/releases)**,
each with two zips. Install an older or newer one at any time to compare versions;
installing replaces the current version. See [CHANGELOG.md](CHANGELOG.md) for what changed.

- **Blender 4.2+**: download `vse_creator_toolkit-x.y.z.zip`, drag it into Blender (or
  *Edit → Preferences → Get Extensions → ⌄ → Install from Disk*).
- **Blender 3.6 – 4.1**: download `vse_creator_toolkit-x.y.z-legacy.zip`, then
  *Edit → Preferences → Add-ons → Install…* and enable **VSE Creator Toolkit**.

## Use the Silence Remover

1. In the Video Sequencer, select your **voice/sound strip** (make it active) and the
   **video strip(s)** recorded with it.
2. Open the sidebar (**N**) → **Creator** tab → **Silence Remover**.
3. Press **Detect Silence**. The panel shows how many pauses it found and the time saved.
4. Optional: **Add Markers** to review the pauses on the timeline. Tweak *Sensitivity*,
   *Min Silence* or *Padding* and detect again.
5. Press **Remove Silence**. Every strip in the selected channels is cut and the gaps close.
   `Ctrl+Z` undoes it in one step.

| Setting | Default | Meaning |
|---|---|---|
| Threshold | Auto | Auto picks a level between noise floor and speech; Manual uses dB |
| Sensitivity | 0.35 | Higher removes more (breaths, soft words) |
| Min Silence | 0.5 s | Only pauses at least this long are cut |
| Padding | 0.12 s | Audio kept before/after speech |
| Keep Start/End Silence | off | Leave silence before the first and after the last word |
| Ripple | on | Close the gaps (off = leave gaps, e.g. to fill with B-roll) |

**Notes:** sound strips with changed speed/pitch or retiming aren't supported yet.
Channels are cut as a whole: put music and B-roll on channels you don't select.

## Develop

```
vse_creator_toolkit/      the add-on package (zip this)
  __init__.py             bl_info (3.6+) + register
  blender_manifest.toml   extension manifest (4.2+)
  compat.py               3.6 → 5.x API differences
  audio_analysis.py       decode + loudness envelope + silence detection
  cutting.py              split / delete / ripple engine
  silence.py              Silence Remover (settings, operators, panel)
  loudness.py             EBU R128 / BS.1770 loudness meter
  audio_tools.py          Auto Ducking + Loudness panels
  export.py               Export Presets
  chapters.py             YouTube Chapters
tests/run_tests.py        headless test suite
tools/build.py            builds the two install zips
tools/release_all.py      publishes a GitHub Release per tag (used by CI)
.github/workflows/         release automation
```

Run the tests (exit code 0 = all passed):

```bash
blender -b --factory-startup --python tests/run_tests.py
```

Build release zips locally into `dist/`:

```bash
python tools/build.py --blender "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe"
```

### Releasing a version
1. Bump `version` in `blender_manifest.toml` and `bl_info`, add a section to `CHANGELOG.md`.
2. Commit, then tag and push: `git tag v0.3.0 && git push origin main v0.3.0`.
3. The **Release** GitHub Action builds both zips and publishes the release. It also
   creates releases for any older tag that doesn't have one yet.
