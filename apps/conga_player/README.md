# conga_player

Beat- and phrase-mapped desktop audio player built on `pedalboard` for practising congas and percussion over tracks analyzed in rekordbox.

## Features

- **Direct rekordbox dump ingestion**: Reads `track.json` and `_temp/track_dump.json` directly from the song directory.
- **Beat & Bar accurate navigation**: 4-lane display:
  - Phrase lane (mood-aware colored phrase blocks)
  - 3-band overview waveform with loop region shading and playhead
  - Bar ruler with 16-bar markers
  - Zoom lane (4, 8, 16, or 32 bars) centered on the playhead with peak waveform and beat gridlines
- **Instant looping & seeking**: Sample-exact seamless looping with automated declick fades at seam transitions.
- **Low CPU audio feeder**: Output-only `pedalboard.io.AudioStream` with monotonic-clock feeder pacing and streaming gain processing.

## Setup

```powershell
cd apps\conga_player
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

## Running

```powershell
# Run selftest (verifies parsing without opening audio devices or GUI)
.\.venv\Scripts\python main.py --selftest

# List available audio output devices
.\.venv\Scripts\python main.py --list-devices

# Launch player with the default song
.\.venv\Scripts\python main.py

# Launch player with a specific song folder or audio device
.\.venv\Scripts\python main.py songs\i_want_you_she_s_so_heavy_eric_kupper --device "Driver de som primário"
```

## Flags

- `--list-devices`: List all available audio output devices and exit.
- `--device NAME`: Specify audio output device name (defaults to system default).
- `--buffer-size FRAMES`: Audio stream buffer size in frames (default: 2048).
- `--latency-offset-ms MS`: Override playhead latency compensation in milliseconds.
- `--selftest`: Load song map, print summary statistics, and exit without opening audio or UI.

## Keyboard & Mouse Controls

| Input | Effect |
|---|---|
| **Space** | Toggle play / pause |
| **S** | Stop playback and rewind to beginning |
| **L** | Toggle loop on / off |
| **Esc** | Clear active loop |
| **P** | Loop currently playing phrase |
| **1**, **2**, **4**, **8** | Loop 1, 2, 4, or 8 bars starting from current bar downbeat |
| **[** / **]** | Jump to previous / next phrase start |
| **←** / **→** | Seek ∓1 bar |
| **Shift + ←** / **Shift + →** | Seek ∓1 beat |
| **-** / **+** | Zoom lane 4 / 8 / 16 / 32 bars |
| **Click overview / zoom** | Seek to position (snapped according to snap mode) |
| **Drag overview / zoom** | Set loop region from drag start to drag end and enable loop |
| **Click phrase** | Seek to phrase start |
| **Double-click phrase** | Loop phrase |

## Running Tests

```powershell
.\.venv\Scripts\python -m pytest tests -q
```
