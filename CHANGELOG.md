# Changelog

Every version is downloadable from the
[Releases page](https://github.com/mwinamijr/vse-creator-toolkit/releases).

## [0.3.0] - 2026-11-26

### Added
- **Auto Ducking** panel: lowers music automatically while the selected voice strips have
  speech. Settings for duck amount (dB), fade down, fade up, how long a pause may be before the
  music comes back (prevents pumping), and voice sensitivity. Ducks all other sound strips or one
  chosen channel. Writes ordinary volume keyframes you can still edit; running it again doesn't
  compound, and **Remove Ducking** restores the original volume.
- **Loudness** panel with a built-in **EBU R128 / ITU-R BS.1770-4 loudness meter** (K-weighting,
  absolute and relative gating), accurate to within 0.05 LU on the EBU Tech 3341 reference
  signals.
  - **Measure Mix** renders the edit's audio and shows integrated LUFS and peak.
  - **Normalise Mix** sets the master volume to hit a target: YouTube/Spotify −14, Podcast −16,
    EBU R128 −23, ATSC −24 or custom. *Prevent Clipping* keeps peaks under −1 dBFS.
  - **Match Selected Clips** evens out the loudness of clips recorded at different levels.

## [0.2.0] - 2026-11-23

### Added
- **Export Presets** panel: YouTube 1080p / 1440p / 4K, YouTube Shorts, Instagram Reels,
  TikTok, Instagram Feed 4:5, Square 1:1 and Fast Preview. Each preset sets
  resolution, MP4/H.264 (keyframe every half second), AAC 48 kHz audio, and a named output path.
- **Variant scenes**: apply a preset to a full copy of the edit (e.g. "My Edit - YouTube Shorts")
  so the original 16:9 edit is never changed.
- **Refit footage** when the aspect ratio changes: *Fill* (crop to fill, ideal for 9:16)
  or *Fit* (show everything, with bars).
- Length warning when an edit is longer than a platform's recommended maximum.
- **Render Video** button.
- **YouTube Chapters** panel: turns timeline markers into chapter text, copied to the
  clipboard and a "YouTube Chapters" text block. Checks YouTube's rules (starts at 0:00,
  3+ chapters, each ≥ 10 s), adds an intro chapter automatically, ignores Silence Remover
  markers, and has an *Add Chapter Here* button.

## [0.1.0] - 2026-11-23

### Added
- **Silence Remover**: detects pauses in a voice strip (auto or manual threshold, minimum
  silence, padding), review markers, and cuts every strip in the selected channels together
  with ripple. Works with packed audio and the audio of video files; respects locked channels.
- Blender 3.6 LTS (legacy add-on) through 4.2+ (extension) support.
