# conga_player v1 — beat/phrase-mapped audio player on pedalboard

## Context

`apps/conga_player/songs/` holds songs that have been imported and analysed in
rekordbox, dumped to JSON by the `rbox` tool. The first song is there in full:

```
songs/i_want_you_she_s_so_heavy_eric_kupper/
  i_want_you_..._groove_collective.flac     # 665.71 s, 44100 Hz, 16-bit stereo
  track.json                                # metadata + audio filename
  _temp/track_dump.json                     # 435 KB rekordbox analysis dump
  _temp/anlz.EX2.waveform_3band_preview.data.json   # 1200-column overview waveform
  analysis/ beatgrid/ midi/ refs/ stems/    # all empty today
```

There is no way today to *play* these songs against their map. The goal is a
small desktop app that opens a song folder, draws its map, and lets you jump to
and loop any beat, bar or phrase — the navigation substrate for practising congas
over a track. Audio I/O and processing go through `pedalboard`.

Verified facts about the analysis (these are what the app is built against):

| Thing | Value |
|---|---|
| Tempo | constant 120.1 BPM (`tempo` is 12010 on all 1332 beats) |
| Beats | 1332, `beat_number` cycling 1–4, exactly 333 each → 4/4, 333 bars |
| Beat times | `DAT` `beat_grid.beats[].time` in ms **+** `EXT` `extended_beat_grid.beats[].microseconds` → sub-ms. First beat 358.548 ms, last 665294.058 ms |
| Phrases | 47, from `EXT` `song_structure`, `mood = 2`, `end_beat = 1324` |
| Phrase starts | all land on `beat_number == 1`, all lengths whole bars (2–16 bars) |
| Phrase kinds | 1–10 observed; kind 1 only first, kind 10 only last |
| `fill` / `beat2..4` | all zero (mid-mood tracks carry no fill-ins) — ignore |
| Overview waveform | 1200 columns of `{low, mid, high}`, 0.555 s per column |

Decisions already taken: **tkinter** for the GUI, **player only** for v1 (no
metronome, no count-in, no time-stretch), **pedalboard 0.9.26 from PyPI in a
dedicated venv**, and the player **reads `_temp/track_dump.json` directly** (no
ingest step — the dump-format coupling is confined to one module, `songmap.py`,
so an ingest step can be slotted in later without touching the rest).

## What pedalboard can and cannot do here

Confirmed by reading `pedalboard/io/AudioStream.h` and `pedalboard_native/io/__init__.pyi`:

- Output-only `AudioStream` works: if `input_device_name` is `None`,
  `num_input_channels` is forced to 0 (`AudioStream.h:55-65`). Only the case of
  *both* device names missing is rejected (`AudioStream.h:84-90`).
- `write(audio, sample_rate)` — `sample_rate` is **required** and must equal
  `stream.sample_rate` exactly or it raises (`AudioStream.h:891`).
- **`stream.plugins` are silently ignored on the output-only path.** The chain
  only runs in the full-duplex branch (`AudioStream.h:295-391`); the write-only
  branch at line 364 is a bare FIFO copy. So the `Pedalboard` chain must be
  applied in Python, per block.
- `write()` blocks on *enqueue*, not on playback, and does so by **busy-spinning**
  (`AudioStream.h:510-532`) — no sleep, no yield. The play FIFO is only
  `buffer_size + 1` frames deep. Left alone this pegs a CPU core, so the feeder
  thread must pace itself with a monotonic-clock sleep and let `write()` return
  immediately.
- Calling `write()` on a stream that is not running re-opens and closes the
  device on every call (`AudioStream.h:517-520`) — always use the context manager.
- `__exit__` calls `stop()`, which resets the FIFO and **discards** the queued tail.
- There is no output-FIFO-depth property and no playback underrun counter;
  underruns zero-fill silently. Position must be tracked in Python.
- `ReadableAudioFile` reads FLAC natively on Windows, `seek()` is sample-accurate
  and cheap, and `read()` releases the GIL. Two threads reading one file raises.
- The chunked-playback snippet in the `AudioStream` class docstring is wrong in
  two ways (`output_device=`, and no `sample_rate` on `write`). Don't copy it.

## Approach

Preload the whole FLAC into one `float32` array (665 s × 44100 × 2 × 4 B ≈ 235 MB)
and treat it as the single source of truth for playback. That makes seeking and
loop wraps instant, sidesteps the one-reader-per-file rule, and keeps all disk
and decode work out of the audio feed loop. A feeder thread assembles blocks from
that array, runs them through a `Pedalboard`, and pushes them to an output-only
`AudioStream`. tkinter polls engine state at ~30 fps and draws four lanes.

Everything in the map layer is in **seconds**; frames are computed at the
engine's actual output rate. That keeps the map correct if the device forces a
rate other than 44100 and the audio has to be resampled.

## Files to create

```
apps/conga_player/
  PLAN.md                    # copy of this plan (step 0)
  README.md                  # setup + keybindings
  requirements.txt           # pedalboard==0.9.26, numpy
  main.py                    # launcher: arg parsing, --list-devices, --selftest
  conga_player/
    __init__.py
    songmap.py               # track_dump.json -> SongMap
    engine.py                # AudioStream feeder thread, loop/seek/position
    peaks.py                 # overview bands + zoom-lane peak computation
    ui.py                    # tkinter app
  tests/
    test_songmap.py
    test_engine.py
```

### `songmap.py` — the only module that knows rekordbox's layout

`load_song(folder: Path) -> SongMap`, reading `track.json` for metadata and the
audio filename, and `_temp/track_dump.json` for the analysis.

```python
@dataclass(frozen=True)
class Phrase:
    index: int; kind: int; label: str
    start_beat: int; end_beat: int      # 1-based beat numbers
    start_s: float; end_s: float; bars: float

@dataclass
class SongMap:
    slug: str; artist: str; title: str; key: str
    audio_path: Path; duration_s: float; sample_rate: int; bpm: float
    beat_times_s: np.ndarray      # (1332,) float64, strictly increasing
    beat_numbers: np.ndarray      # (1332,) int8, 1..4
    bar_times_s: np.ndarray       # (333,)  downbeats only
    bar_first_beat: np.ndarray    # (333,)  index into beat_times_s
    phrases: list[Phrase]
    preview_bands: np.ndarray     # (1200, 3) uint8 low/mid/high
```

Key points:

- Beat times: `time + microseconds / 1000.0`, in ms, then `/ 1000` to seconds.
  Zip `anlz.DAT.sections.beat_grid.beats` with
  `anlz.EXT.sections.extended_beat_grid.beats` (both 1332 long); if the lengths
  disagree, fall back to the ms-only times from `DAT`.
- Bars from `beat_numbers == 1`. Assert 4/4 (every bar exactly 4 beats) and warn
  rather than crash if not — other songs may not be this clean.
- Phrases from `anlz.EXT.sections.song_structure`: `phrases[]` gives `beat` and
  `kind`; each phrase ends where the next begins, and the last ends at
  `end_beat` (1324 here — **before** the final beat 1332, so the tail is
  deliberately unlabelled; the UI draws it blank, and `bars` is a float).
- Labels are mood-aware, keyed on `song_structure.mood`:
  - mood 2 (Mid, this track): `1 Intro, 2 Verse1 … 7 Verse6, 8 Bridge, 9 Chorus, 10 Outro`
  - mood 1 (High): `1 Intro, 2 Up, 3 Down, 4 Chorus, 5 Outro`
  - mood 3 (Low): `1 Intro, 2 Verse1, 3 Verse2, 4 Bridge, 5 Chorus, 6 Outro`
  Mood 2 is confirmed by this track (all 10 kinds present, Intro first, Outro
  last). Moods 1 and 3 are from the format's reverse-engineering, not verified
  here — unknown kinds fall back to `f"Kind {k}"` instead of raising. The
  rekordbox skill (`chapters/ch04-phrase-edit.md:8`) notes the on-screen names
  were lost in extraction, so treat this table as replaceable.
- Overview bands from `_temp/anlz.EX2.waveform_3band_preview.data.json`
  (1200 × `{low, mid, high}`). Missing file → `peaks.py` computes a 1200-column
  fallback from the audio.
- Never read the `*_detail.*` JSONs — 3.1 MB and 10.8 MB.

Query helpers, all `np.searchsorted` on `beat_times_s` / `bar_times_s`:
`beat_index_at(t)`, `bar_index_at(t)`, `phrase_at(t)`, and
`snap(t, mode) -> float` for `mode in {"beat", "bar", "phrase", "off"}`
(snap to the *nearest* boundary, not the preceding one).

### `engine.py` — playback

```python
class Engine:
    def __init__(self, audio, sample_rate, *, buffer_size=2048,
                 output_device_name=None, declick_ms=2.0, latency_offset_ms=None)
    def start(self); def close(self)
    def play(self); def pause(self); def toggle(self); def stop()
    def seek(self, seconds)
    def set_loop(self, start_s, end_s); def clear_loop(self); def set_loop_enabled(b)
    def set_gain_db(self, db)
    def state(self) -> EngineState      # position_s, playing, loop, frames_written
```

- Open as a context manager inside the thread:
  `AudioStream(output_device_name=dev, num_input_channels=0, num_output_channels=ch, sample_rate=sr, buffer_size=buffer_size)`.
  Probe at the file's rate first; if construction raises, retry with
  `sample_rate=None` and resample the preloaded audio once to `stream.sample_rate`
  via `AudioFile(path).resampled_to(rate)` (`ReadableAudioFile.h:1030-1043`).
  Everything downstream uses `engine.sample_rate`.
- `_next_block(n) -> np.ndarray (ch, n)` is **pure and lock-protected, with no
  stream involved** — this is what the unit tests drive. It fills from `audio`,
  wraps at `loop_end → loop_start` when looping (or `total → stop`), and can wrap
  more than once if a loop is shorter than a block.
- Declick at every wrap: fade out the last `declick_ms` before the seam and fade
  in the first `declick_ms` after it, in place on the output block. No overlap,
  so loop length stays sample-exact. (A true equal-power crossfade is a
  follow-up; beat-aligned seams on a 4/4 track rarely need it.)
- Paused → write zero blocks, so the device stays open and pacing continues.
- FX: `self.board = Pedalboard([Gain(gain_db=0.0)])`, applied per block as
  `self.board(block, sr, reset=False)` — `reset=False` is what makes it a
  streaming chain. `Gain` is latency-free; a plugin that reports latency would
  shift the playhead, so note that where the chain is built.
- Pacing, to avoid the `write()` busy-spin:

  ```python
  now = time.monotonic()
  if self._next_write - now > 0.002:
      time.sleep(self._next_write - now - 0.002)
  stream.write(block, sr)                     # returns ~immediately
  self._next_write = max(self._next_write + n / sr, now)
  ```

- Position: on each write, store `(monotonic_at_write, song_frame_at_block_start)`
  under the lock. `state()` returns
  `song_frame + (now - t_write) * sr - latency_frames`, clamped into the loop or
  the track. `latency_frames` defaults to `buffer_size` (the FIFO depth) and is
  overridable with `--latency-offset-ms`, since the driver's own buffer is not
  queryable. The UI gets a smooth, interpolated playhead this way without
  polling the engine faster than 30 fps.
- Thread discipline: one feeder thread, one `threading.Lock`, all mutable state
  (`pos`, `playing`, `loop`, `gain`) behind it. Commands are plain method calls
  that take the lock — no queue needed. `close()` sets a quit flag, writes
  `buffer_size` frames of silence, joins, then exits the context manager.

### `peaks.py`

- `overview_columns(song_map, width) -> np.ndarray (width, 3)` — resample the
  1200 stored bands to the canvas width with `np.interp`.
- `zoom_peaks(audio, sr, start_s, end_s, columns) -> np.ndarray (columns,)` —
  mono-mixed `max(abs())` per column via a reshape-and-max over the slice. For an
  8-bar window (~16 s, 700k frames) this is sub-millisecond, which is why the
  3.1 MB detail-waveform JSON is not needed.

### `ui.py` — tkinter

Fixed-width window (1240 px), dark palette, four stacked canvases plus a
transport row. Fixed width keeps the geometry maths trivial for v1; making it
resizable is a follow-up.

```
┌──────────────────────────────────────────────────────────────┐
│ Groove Collective — I Want You (She's So Heavy)  120.1 BPM Am│
├──────────────────────────────────────────────────────────────┤
│ PHRASES │Intro│Vs1│Vs1│Vs1│Brg│Vs2│ … │Outro│                │  h=28
│ WAVE    ▁▂▃▅▇▆▃▂▁▃▅▇▇▅▃▂▁▂▄▆▇▅▃▁▂▃▅▇▆▃▁                      │  h=90
│         ░░░░░loop░░░░░              ▐playhead                │
│ BARS    |1    |17    |33    |49    |65    |81    |97         │  h=18
├──────────────────────────────────────────────────────────────┤
│ ZOOM 8 bars   |69 · · · |70 · · · |71 · · · |72 · · ·        │  h=110
├──────────────────────────────────────────────────────────────┤
│ ▶ ■ │ Loop ☑ │ 1 2 4 8 16 bars │ ⟲ Phrase │ Snap: Beat │ Vol │
│ 02:16.243   bar 69.2   beat 2   phrase 8/47 Verse1   loop 69–76│
└──────────────────────────────────────────────────────────────┘
```

- Phrase lane: one rectangle per phrase, filled by a 10-colour kind→colour map,
  label drawn when the rectangle is wide enough. The unlabelled tail after
  `end_beat` stays empty.
- Overview: 1200 vertical lines, low/mid/high stacked in three colours
  (rekordbox-like). Loop region shaded with a `stipple="gray50"` rectangle (the
  tkinter way to fake transparency). Bar ruler ticks every bar, numbered every
  16 (333 bars / 1200 px = 3.6 px per bar).
- Zoom lane: `zoom_bars` (default 8) centred on the playhead, redrawn only when
  the window moves to a new bar. Beat gridlines, bar numbers, per-beat hit
  targets, loop shading, playhead.
- Redraw: `root.after(33, tick)`. Only the playhead lines are deleted and
  recreated each tick; the lanes are cached and redrawn on change.

Interaction:

| Input | Effect |
|---|---|
| Click overview / zoom lane | seek, snapped per the snap mode |
| Drag on either | set loop from drag start to end, snapped, loop on |
| Click phrase | seek to phrase start |
| Double-click phrase | loop that phrase |
| `Space` / `S` | play-pause / stop |
| `L` / `Esc` | loop on-off / clear loop |
| `[` `]` | previous / next phrase |
| `←` `→` | ∓1 bar; with `Shift`, ∓1 beat |
| `1 2 4 8` | loop N bars from the current downbeat |
| `P` | loop current phrase |
| `-` `+` | zoom lane 4 / 8 / 16 / 32 bars |

### `main.py`

`python main.py [song-slug-or-path]`, defaulting to the single folder in
`songs/` (error listing the options if there are several and none is named).
Flags: `--list-devices` (prints `AudioStream.output_device_names` and the
default), `--device`, `--buffer-size`, `--latency-offset-ms`, `--selftest`
(loads the map, prints a summary, exits — no window, no audio device).

Guard the import shadowing explicitly, because the app lives *inside* the
pedalboard source tree: if `pedalboard.__file__` resolves under the repo's own
`pedalboard/` directory, exit with a message saying to run from
`apps/conga_player` with its venv. (Running `python main.py` puts
`apps/conga_player` on `sys.path[0]`, so the normal path is already safe; this
guard catches `python -m` from the repo root, which otherwise fails with a
confusing `cannot import name 'ExternalPlugin'`.)

## Setup

```powershell
cd apps\conga_player
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # pedalboard==0.9.26, numpy
.venv\Scripts\python main.py --selftest
.venv\Scripts\python main.py
```

The `pedalboard-0.9.26-cp312-cp312-win_amd64.whl` wheel downloads cleanly here
(checked), and 0.9.26 is the version this repo is at, so the app and the source
tree stay in step. Add `apps/conga_player/.venv/` to `.gitignore`.

## Verification

1. `.venv\Scripts\python main.py --selftest` — prints `1332 beats / 333 bars /
   47 phrases / 120.1 BPM / 665.71 s`, the first and last phrase labels
   (`Intro`, `Outro`) and the audio path. Catches every map-parsing error with
   no audio device involved.
2. `.venv\Scripts\python -m pytest tests -q`:
   - `test_songmap.py` against the real song folder: 1332 beats, 333 bars, 47
     phrases, `beat_times_s` strictly increasing, first beat ≈ 0.358548 s, last
     ≈ 665.294058 s, every phrase starting on a downbeat, phrase 0 `Intro` and
     phrase 46 `Outro`, phrase spans contiguous, `snap()` returning the nearest
     boundary on both sides of a midpoint.
   - `test_engine.py` with a synthetic ramp array and **no** `AudioStream`
     (driving `_next_block` directly): a block straddling a loop end comes back
     as loop-tail-then-loop-head, a loop shorter than one block wraps twice,
     declick ramps are applied only at seams, `seek` lands on the exact frame,
     paused output is all zeros, and non-looping playback stops at the end.
3. `.venv\Scripts\python main.py --list-devices` — confirms a usable output
   device before any GUI debugging.
4. Manual, with the real song: audio plays clean; the playhead tracks the music
   (if it reads consistently early or late, adjust `--latency-offset-ms`);
   clicking a phrase jumps to its downbeat; `P` loops that phrase seamlessly
   across the seam with no click; `4` loops four bars from the current downbeat;
   a drag in the zoom lane loops an exact beat range; CPU sits low while playing
   (this is the check that the write-pacing works — a pegged core means the sleep
   maths is wrong).

## Known risks

- **`write()` busy-spin** is the main engine risk; the monotonic-clock pacing
  above is the mitigation, and step 4 of verification is what proves it.
- **Device sample rate.** If the default device refuses 44100 the fallback
  resamples the whole preloaded array once at load (a few seconds) — slower
  startup, nothing else.
- **235 MB resident** for the preloaded audio. Fine for one song on a desktop;
  a streaming mode (`ReadableAudioFile.seek` per wrap) is the follow-up if
  several songs ever need to be open at once.
- **Playhead offset** can only be estimated, since the driver buffer is not
  queryable from Python — hence the tunable flag.
- **`_temp/` is scratch.** If the dump is regenerated or wiped the player stops
  working; the coupling is confined to `songmap.py`, so an ingest step writing a
  stable `analysis/map.json` can be added there later without touching the UI or
  engine.

## Out of scope for v1

Metronome / click, count-in, time-stretch practice tempo, stems, MIDI, multi-song
playlists, waveform zoom beyond the fixed lane, and a resizable window.
