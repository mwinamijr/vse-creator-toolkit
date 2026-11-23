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

### 🛣 Roadmap
| Version | Feature |
|---|---|
| 0.2 | Export presets (YouTube, Shorts, Reels, TikTok) + markers → YouTube chapters |
| 0.3 | Auto-ducking (lower music under the voice) + loudness normalise (−14 LUFS) |
| 0.4 | Auto captions with offline Whisper, word-by-word animated subtitles, SRT import/export |
| 0.5 | 9:16 auto-reframe for vertical cut-downs |

## Install

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
  silence.py              settings, operators, sidebar panel
tests/run_tests.py        headless test suite
tools/build.py            builds dist/ zips
```

Run the tests (exit code 0 = all passed):

```bash
blender -b --factory-startup --python tests/run_tests.py
```

Build release zips:

```bash
python tools/build.py "C:/Program Files/Blender Foundation/Blender 4.2/blender.exe"
```
